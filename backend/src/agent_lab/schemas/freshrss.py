"""FreshRSS Google Reader API 的外部协议对象与文章 ID 等价规则。

这些 Pydantic 模型「忠实照抄」FreshRSS 返回的字段，只做类型和别名（字段名）映射，
不含任何业务清洗规则。本层只和 FreshRSS 打交道；外部 API 新增未知字段会被忽略，
避免对方升级导致我们同步断掉。

术语速查（后面不再重复解释）：
- 协议对象：描述「外部系统长什么样」的模型，和内部业务对象关注点不同，天然会随
  外部 API 变化，所以单独放一层、不污染内部模型；
- Google Reader API：FreshRSS 对外提供的读取接口风格（stream/items/...）那套 URL；
- canonical / alternate：文章原文链接的主选 / 备用；
- entry ID：FreshRSS 内部给每条内容分配的编号；
- continuation：FreshRSS 的翻页游标，用来取「这一页之后」的下一批；
- external_id：落到我们 documents 表、用于幂等去重的外部文章 ID。
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


type FreshRSSItemIdKey = tuple[str, int | str]

_READER_ITEM_TAG_PREFIX = "tag:google.com,2005:reader/item/"
_READER_ITEM_HEX_RE = re.compile(r"^[0-9a-fA-F]{16}$")


def freshrss_item_id_key(value: str) -> FreshRSSItemIdKey:
    """
    为了解决不同接口响应id不一样问题，进行归一化统一处理

    把 FreshRSS 同一篇文章的多套 ID 形式归一到同一个键。

    同一个人，一个叫"ZhangSan"，一个叫"张三"——freshrss_item_id_key 就是"把他的中文名和英文名都归一成同一个编号（身份证号）"，这样两个接口返回的“同一个人”能被认出来。

    为什么需要：FreshRSS 两个接口对同一篇文章给出不同样式的 ID——
    stream/items/ids 返回十进制 entry ID，stream/items/contents 返回带 16 位十六进制
    尾部的 Google Reader tag。若不做归一，同一篇文章会被当成两篇。本函数把两种
    形式转成可互相比较的等价键：
    - 全数字十进制 → ("reader_numeric", 转成 int)；
    - Google Reader tag 且尾部是 16 位十六进制 → ("reader_numeric", 按 16 进制转 int)；
    - 其余无法识别的 → ("exact", 去掉空白)，只能字符串完全相等时匹配。

    Args:
        value: ``itemRefs[].id`` 或 ``items[].id``。当前 FreshRSS 的 IDs 接口返回十进制
            entry ID，而 contents 接口返回带 16 位十六进制尾部的 Google Reader tag。

    Returns:
        已识别 Reader ID 返回 ``("reader_numeric", int_value)``；其他非空协议 ID
        返回 ``("exact", stripped_value)``，只能与完全相同字符串匹配。

    Raises:
        ValueError: ID 去除两端空白后为空。

    Notes:
        本函数只用于一页请求/响应关联，不改变 ``FreshRSSItem.id``。持久化仍使用
        contents 返回的 tag ID，保持既有 ``documents.external_id`` 业务幂等。
    """

    normalized = value.strip()
    if not normalized:
        raise ValueError("FreshRSS 条目 ID 不能为空")
    if normalized.isascii() and normalized.isdecimal():
        return "reader_numeric", int(normalized)
    if normalized.startswith(_READER_ITEM_TAG_PREFIX):
        hexadecimal = normalized.removeprefix(_READER_ITEM_TAG_PREFIX)
        if _READER_ITEM_HEX_RE.fullmatch(hexadecimal):
            return "reader_numeric", int(hexadecimal, 16)
    return "exact", normalized


class FreshRSSLink(BaseModel):
    """Google Reader API 中 canonical/alternate 使用的链接对象。"""

    href: str = Field(description="链接地址。")
    media_type: str | None = Field(default=None, alias="type", description="链接媒体类型。")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class FreshRSSContentBlock(BaseModel):
    """FreshRSS 的 summary 或 content HTML 内容块。"""

    content: str = Field(description="正文或摘要 HTML。")

    model_config = ConfigDict(extra="ignore")


class FreshRSSOrigin(BaseModel):
    """文章所属 FreshRSS Feed 的来源信息。"""

    stream_id: str = Field(alias="streamId", description="FreshRSS Feed ID，例如 feed/2。")
    title: str = Field(description="来源名称。")
    html_url: str | None = Field(default=None, alias="htmlUrl", description="来源主页。")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class FreshRSSItem(BaseModel):
    """stream/items/contents 返回的一篇 FreshRSS 文章（协议对象）。

    附带了两个便利方法：FreshRSS 会把正文同时放在 content 和 summary 两个块、把
    原文链接同时放在 canonical 和 alternate 两个列表里，body_html() / article_url()
    按「content 优先、canonical 优先」的规则取第一个可用的值。
    """

    id: str = Field(description="FreshRSS Google Reader 文章 ID。")
    title: str = Field(description="文章标题。")
    published: int | None = Field(default=None, description="Unix 秒级发布时间。")
    timestamp_usec: str | None = Field(
        default=None,
        alias="timestampUsec",
        description="FreshRSS 微秒级更新时间；API 以字符串返回。",
    )
    crawl_time_msec: str | None = Field(
        default=None,
        alias="crawlTimeMsec",
        description="FreshRSS 毫秒级抓取时间；API 以字符串返回。",
    )
    author: str | None = Field(default=None, description="来源 Feed 提供的作者。")
    canonical: list[FreshRSSLink] = Field(default_factory=list, description="规范原文链接。")
    alternate: list[FreshRSSLink] = Field(default_factory=list, description="备用原文链接。")
    categories: list[str] = Field(default_factory=list, description="标签与 FreshRSS 状态集合。")
    summary: FreshRSSContentBlock | None = Field(default=None, description="summary 内容块。")
    content: FreshRSSContentBlock | None = Field(default=None, description="content 内容块。")
    origin: FreshRSSOrigin = Field(description="文章所属 Feed。")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def body_html(self) -> str:
        """按 content、summary 的优先级返回可用 HTML。

        Returns:
            第一个非空正文 HTML；两个内容块都不可用时返回空字符串。
        """

        for block in (self.content, self.summary):
            if block is not None and block.content.strip():
                return block.content
        return ""

    def article_url(self) -> str | None:
        """按 canonical、alternate 的优先级返回文章 URL。

        Returns:
            第一个非空链接；两个链接列表都没有有效值时返回 ``None``。
        """

        for links in (self.canonical, self.alternate):
            for link in links:
                if link.href.strip():
                    return link.href
        return None


class FreshRSSItemIdPage(BaseModel):
    """stream/items/ids 返回的一页「文章 ID 列表 + 翻页游标」。

    对象只存在于一次 FreshRSS 读取期间，不是数据库实体。同步是分两步走的：先用
    ids 接口拿这一页的文章 ID 列表，再用 contents 接口按 ID 拉正文。
    ``continuation`` 是 FreshRSS 内部的 entry 翻页游标，只用于「同一订阅的后续分页」，
    它不等于新闻 ``external_id``，也不进入 Embedding、LangChain Document 或 Qdrant
    Payload。
    """

    item_ids: tuple[str, ...] = Field(
        default=(),
        description=(
            "来自 FreshRSS itemRefs[].id 的稳定文章 ID；不可包含空值或重复值，允许"
            "空元组表示当前游标之后没有新闻，后续作为 documents.external_id 使用。"
        ),
    )
    continuation: str | None = Field(
        default=None,
        description=(
            "来自 FreshRSS continuation 的可选十进制内部 entry 游标；仅在服务返回"
            "后续分页锚点时存在，持久化前不得从文章 ID 或发布时间猜测。"
        ),
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("item_ids")
    @classmethod
    def validate_item_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """规范化文章 ID，并拒绝会破坏幂等或页内顺序的值。

        Args:
            values: 从 ``itemRefs`` 按响应顺序提取的文章 ID。

        Returns:
            去除两端空白、顺序不变的文章 ID 元组。

        Raises:
            ValueError: 任一 ID 为空，或同一页包含重复 ID。
        """

        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("FreshRSS 条目 ID 不能包含空值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("FreshRSS 条目 ID 在同一页面内不能重复")
        return normalized

    @field_validator("continuation")
    @classmethod
    def normalize_continuation(cls, value: str | None) -> str | None:
        """把 FreshRSS continuation 规范化为可持久化的十进制字符串。

        Args:
            value: 响应中的 continuation，缺失时为 ``None``。

        Returns:
            去除前导零的十进制字符串；缺失时仍返回 ``None``。

        Raises:
            ValueError: continuation 不是非负十进制整数。
        """

        if value is None:
            return None
        normalized = value.strip()
        if not normalized or not normalized.isascii() or not normalized.isdecimal():
            raise ValueError("FreshRSS continuation 必须是十进制字符串")
        return str(int(normalized))


class FreshRSSSubscriptionCategory(BaseModel):
    """FreshRSS 订阅所属的用户分类。"""

    id: str = Field(description="Google Reader 分类 ID。")
    label: str = Field(description="可展示分类名称。")

    model_config = ConfigDict(extra="ignore")


class FreshRSSSubscription(BaseModel):
    """subscription/list 返回的一条 FreshRSS 订阅。"""

    id: str = Field(description="Feed ID，例如 feed/2。")
    title: str = Field(description="订阅名称。")
    url: str | None = Field(default=None, description="RSS/Atom 地址。")
    html_url: str | None = Field(default=None, alias="htmlUrl", description="来源主页。")
    categories: list[FreshRSSSubscriptionCategory] = Field(
        default_factory=list,
        description="用户为订阅设置的分类。",
    )

    model_config = ConfigDict(populate_by_name=True, extra="ignore")
