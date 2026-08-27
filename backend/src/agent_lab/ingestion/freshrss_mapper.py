"""把 FreshRSS 外部协议对象转换为稳定的内部 ``SourceDocument``。

本模块位于 ingestion 映射层，负责 URL、时间、标签、图片、HTML 文本和内容质量规则；
它不发送 FreshRSS 请求、不写 PostgreSQL、不构建 LangChain Chunk，也不调用 Embedding
或 Qdrant。任何无法安全判断的关键字段或正文质量问题以明确映射异常返回。
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from agent_lab.domain.source_document import (
    ImageReference,
    SourceDocument,
    SourceInfo,
)
from agent_lab.ingestion.content_quality import ContentQualityNormalizer
from agent_lab.schemas.freshrss import FreshRSSItem, FreshRSSSubscription


class FreshRSSMappingError(ValueError):
    """FreshRSS 数据缺少构建内部文档所需的关键字段。"""


class FreshRSSContentQualityError(FreshRSSMappingError):
    """FreshRSS 新闻因稳定内容质量原因不能进入持久化流程。

    异常生命周期限于当前同步页。``reason`` 是可测试、可安全统计的固定标识，异常
    不保存标题、正文或 HTML；调用方必须让整页失败，避免 checkpoint 越过该文章。
    """

    reason: str

    def __init__(self, reason: str) -> None:
        """保存稳定拒绝原因。

        Args:
            reason: ``empty_title``、``empty_content`` 或 ``title_only`` 等固定标识。
        """

        self.reason = reason
        super().__init__(f"FreshRSS content quality rejected item: {reason}.")


class FreshRSSItemMapper:
    """把一篇 FreshRSS 协议文章统一成内部 ``SourceDocument``。

    职责：URL、时间、标签、图片、HTML→纯文本、内容质量规则全都在这层做，因为
    只有在这一层才能做到「把外部协议的差异一次性消化掉」——之后的 PostgreSQL/
    LangChain/Embedding 都不用再关心来源是 FreshRSS 还是 SEC。

    一个关键约束：HTML 转纯文本后若命中已确认的完全重复片段会删除；但「正文过短」
    只作为诊断信息、不阻断入库，以兼容确实有业务价值的短快讯。
    """

    _LABEL_PREFIX = "user/-/label/"
    _IGNORED_TAGS = ("script", "style", "noscript", "template")

    def __init__(self) -> None:
        """创建无状态的正文质量规范化器。

        规范化器不保存文章状态，也不访问数据库；同一个 Mapper 可以安全复用。
        """

        self._content_quality_normalizer = ContentQualityNormalizer()

    def map(
        self,
        item: FreshRSSItem,
        subscription: FreshRSSSubscription,
        *,
        provider: str,
    ) -> SourceDocument:
        """将一篇 FreshRSSItem 转换为与外部协议解耦的 SourceDocument。

        Args:
            item: 已通过 Pydantic 校验的 FreshRSS 外部协议对象。
            subscription: 文章所属订阅，用于补全 Feed URL、主页和分类。
            provider: 当前 FreshRSS 实例的稳定标识，会参与来源唯一键。

        Returns:
            已完成标题、时间、正文、标签和图片规范化的统一领域文档。

        Raises:
            FreshRSSMappingError: 文章缺少 URL、可读正文或非空标题时抛出。
            pydantic.ValidationError: 外部 URL 等值无法满足领域模型约束时抛出。
        """

        # 1. 取文章 URL（canonical 优先），缺了就拒绝——没有 URL 无法回查原文
        article_url = item.article_url()
        if article_url is None:
            raise FreshRSSMappingError("FreshRSS 条目没有规范链接或备用链接（URL）。")

        # 2. 清洗标题并抽纯文本，空标题按质量原因拒绝（can't 入库检索）
        title = self._clean_title(item.title)
        if not title:
            raise FreshRSSContentQualityError("empty_title")

        # 3. 选正文块（content 优先于 summary，绝不拼接二者），并转成纯文本+取图片
        content_html, content_kind = self._select_body_html(item)
        if not content_html:
            raise FreshRSSContentQualityError("empty_content")

        content_text, images = self._clean_html(content_html, article_url)
        if not content_text:
            raise FreshRSSContentQualityError("empty_content")

        # 4. 统一质量规范化：HTML entity / NFC / 空白 / 边界标题块 / 相邻重复段
        #    （只做确定性去重；合法短快讯仍保留，正文过短只是诊断信号不阻断）
        quality_result = self._content_quality_normalizer.inspect(
            title=title,
            content_text=content_text,
            content_kind=content_kind,
        )
        content_text = quality_result.normalized_text
        if not content_text:
            raise FreshRSSContentQualityError(
                quality_result.rejection_reason or "empty_content"
            )

        # 5. 抽标签和作者
        labels = self._extract_labels(item, subscription)
        authors = (item.author.strip(),) if item.author and item.author.strip() else ()

        # 6. 组装来源信息（provider + external_id 构成来源唯一键）
        source = SourceInfo(
            provider=provider,
            external_id=subscription.id,
            name=subscription.title or item.origin.title,
            feed_url=subscription.url,
            home_url=item.origin.html_url or subscription.html_url,
        )

        # 7. 返回与外部协议解耦的统一领域文档；时间统一转成带时区 UTC
        return SourceDocument(
            external_id=item.id,
            title=title,
            url=article_url,
            published_at=self._seconds_to_datetime(item.published),
            source_updated_at=self._microseconds_to_datetime(item.timestamp_usec),
            source=source,
            authors=authors,
            labels=labels,
            content_html=content_html,
            content_text=content_text,
            images=images,
        )

    @staticmethod
    def _select_body_html(item: FreshRSSItem) -> tuple[str, str]:
        """按 FreshRSS content、summary 优先级选择单一正文块。

        Args:
            item: 已校验的 FreshRSS 外部文章。

        Returns:
            非空 HTML 与 ``content``/``summary`` 来源类型；都为空时返回两个空串。

        Notes:
            content 与 summary 常是同一正文的两种协议表示，绝不拼接二者。选择一个
            块可从源头避免整篇重复；块内部重复仍交给统一质量规范化器保守处理。
        """

        for kind, block in (("content", item.content), ("summary", item.summary)):
            if block is not None and block.content.strip():
                return block.content, kind
        return "", ""

    @staticmethod
    def _clean_title(title_html: str) -> str:
        """把可能含简单 HTML/entity 的 FreshRSS 标题转换为稳定纯文本。

        Args:
            title_html: FreshRSS title 原值，可能包含标签、entity 或 Unicode 空白。

        Returns:
            NFC 且空白稳定的纯文本标题；没有可见文本时返回空字符串。
        """

        title_text = BeautifulSoup(title_html, "html.parser").get_text(
            separator=" ",
            strip=True,
        )
        return ContentQualityNormalizer.normalize_inline_text(title_text)

    def _clean_html(
        self,
        content_html: str,
        article_url: str,
    ) -> tuple[str, tuple[ImageReference, ...]]:
        """使用 BeautifulSoup 提取纯文本和绝对图片 URL。

        Args:
            content_html: FreshRSS 返回的正文或摘要 HTML。
            article_url: 文章规范 URL，用于解析正文中的相对图片地址。

        Returns:
            清洗后的纯文本，以及按正文顺序去重后的图片引用元组。

        Notes:
            本方法保留换行表达的段落边界，但不会为特定网站编写正文 selector；
            网页回源和 selector 属于 FreshRSS 的职责。
        """

        soup = BeautifulSoup(content_html, "html.parser")

        # 这些元素不属于可检索正文；decompose 会同时删除标签和内部文本。
        # 删除非正文标签（连同内部文本）
        for unwanted in soup.find_all(self._IGNORED_TAGS):
            unwanted.decompose()

        # 提取图片（img 标签的 src 等属性）
        images = self._extract_images(soup, article_url)

        # 提取纯文本（separator="\n" 保留段落边界，strip=True 去首尾空白）
        raw_text = soup.get_text(separator="\n", strip=True)
        content_text = "\n".join(
            line for line in raw_text.splitlines() if line.strip()
        )

        return content_text, images

    def _extract_images(
        self,
        soup: BeautifulSoup,
        article_url: str,
    ) -> tuple[ImageReference, ...]:
        """提取 img 的常见懒加载属性，转成绝对 URL 并保持原有顺序。

        Args:
            soup: 已解析且已删除不可读元素的 BeautifulSoup 文档。
            article_url: 用于把相对图片地址转换成绝对地址的文章 URL。

        Returns:
            只包含 HTTP/HTTPS 地址、按首次出现顺序去重的图片引用。
        """

        images: list[ImageReference] = []
        seen_urls: set[str] = set()

        for image in soup.find_all("img"):
            if not isinstance(image, Tag):
                continue
            source = self._first_attribute(image, "src", "data-src", "data-original")
            if source is None:
                continue

            absolute_url = urljoin(article_url, source)
            if urlparse(absolute_url).scheme not in {"http", "https"}:
                continue
            if absolute_url in seen_urls:
                continue

            seen_urls.add(absolute_url)
            images.append(
                ImageReference(
                    url=absolute_url,
                    alt_text=self._string_attribute(image, "alt"),
                    title=self._string_attribute(image, "title"),
                )
            )

        return tuple(images)

    def _extract_labels(
        self,
        item: FreshRSSItem,
        subscription: FreshRSSSubscription,
    ) -> tuple[str, ...]:
        """保留用户标签，排除 reading-list/read 等 FreshRSS 状态。

        Args:
            item: 包含文章分类和 FreshRSS 内部状态的外部协议对象。
            subscription: 包含用户为订阅设置的分类名称的协议对象。

        Returns:
            按首次出现顺序去重后的业务标签，不包含已读等内部状态。
        """

        labels = [category.label.strip() for category in subscription.categories]
        labels.extend(
            unquote(category.removeprefix(self._LABEL_PREFIX)).strip()
            for category in item.categories
            if category.startswith(self._LABEL_PREFIX)
        )
        return tuple(dict.fromkeys(label for label in labels if label))

    @staticmethod
    def _seconds_to_datetime(value: int | None) -> datetime | None:
        """把 Unix 秒转换成 UTC aware datetime。

        Args:
            value: 来源提供的 Unix 秒；缺失时为 ``None``。

        Returns:
            带 UTC 时区的时间；输入缺失时返回 ``None``。
        """

        if value is None:
            return None
        return datetime.fromtimestamp(value, tz=UTC)

    @staticmethod
    def _microseconds_to_datetime(value: str | None) -> datetime | None:
        """无浮点精度损失地转换 FreshRSS 微秒时间戳。

        Args:
            value: FreshRSS 以字符串返回的 Unix 微秒时间戳。

        Returns:
            带 UTC 时区的精确时间；输入缺失时返回 ``None``。

        Raises:
            FreshRSSMappingError: 时间戳不是整数字符串时抛出。
        """

        if value is None:
            return None
        try:
            seconds, microseconds = divmod(int(value), 1_000_000)
        except ValueError as exc:
            raise FreshRSSMappingError("FreshRSS timestampUsec 不是整数。") from exc
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=microseconds)

    @classmethod
    def _first_attribute(cls, tag: Tag, *names: str) -> str | None:
        """返回标签中第一个非空字符串属性。

        Args:
            tag: 要读取属性的 BeautifulSoup 标签。
            *names: 按优先级排列的属性名，例如 ``src``、``data-src``。

        Returns:
            第一个非空字符串属性值；全部缺失时返回 ``None``。
        """

        for name in names:
            value = cls._string_attribute(tag, name)
            if value:
                return value
        return None

    @staticmethod
    def _string_attribute(tag: Tag, name: str) -> str | None:
        """读取 BeautifulSoup 属性，并排除 class 等列表类型属性。

        Args:
            tag: 要读取属性的 BeautifulSoup 标签。
            name: 属性名称。

        Returns:
            去除两端空白后的字符串值；非字符串或空值返回 ``None``。
        """

        value = tag.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = [
    "FreshRSSContentQualityError",
    "FreshRSSItemMapper",
    "FreshRSSMappingError",
]
