"""把 FreshRSS 外部协议对象转换为稳定的内部 ``SourceDocument``。

本模块负责 URL、时间、标签、图片引用和单一原始 HTML 的选择。协议身份错误阻止接收，
正文质量交给原件保存后的统一解析组件；这里不发送请求、不执行解析或索引。
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from agent_lab.domain.source_document import (
    ImageReference,
    SourceDocument,
    SourceInfo,
)
from agent_lab.ingestion.content_quality import normalize_inline_text
from agent_lab.schemas.freshrss import FreshRSSItem, FreshRSSSubscription


class FreshRSSMappingError(ValueError):
    """FreshRSS 数据缺少构建内部文档所需的关键字段。"""


class FreshRSSItemMapper:
    """把一篇 FreshRSS 协议文章统一成内部 ``SourceDocument``。

    这里只消化协议差异，选择单一原始正文；质量异常留给保存后的统一处理能力。
    """

    _LABEL_PREFIX = "user/-/label/"
    _IGNORED_TAGS = ("script", "style", "noscript", "template")

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
            保留原始 HTML 字节的接收对象。

        Raises:
            FreshRSSMappingError: 文章缺少协议所需 URL 或时间标识无法解释时抛出。
            pydantic.ValidationError: 外部 URL 等值无法满足领域模型约束时抛出。
        """

        # 1、取文章 URL（canonical 优先），缺了就拒绝——没有 URL 无法回查原文
        article_url = item.article_url()
        if article_url is None:
            raise FreshRSSMappingError("FreshRSS 条目没有规范链接或备用链接（URL）。")

        # 2、标题可规范化；正文保持实际选中的 HTML，不在接收前压平或拒绝。
        title = self._clean_title(item.title)
        content_html = self._select_body_html(item)
        images = self._images_from_html(content_html, article_url)

        # 3、抽标签和作者
        labels = self._extract_labels(item, subscription)
        authors = (item.author.strip(),) if item.author and item.author.strip() else ()

        # 4、组装来源信息（provider + external_id 构成来源唯一键）
        source = SourceInfo(
            provider=provider,
            external_id=subscription.id,
            name=subscription.title or item.origin.title,
            feed_url=subscription.url,
            home_url=item.origin.html_url or subscription.html_url,
        )

        # 5、返回与外部协议解耦的统一领域文档；时间统一转成带时区 UTC
        return SourceDocument(
            external_id=item.id,
            title=title,
            url=article_url,
            published_at=self._seconds_to_datetime(item.published),
            source_updated_at=self._microseconds_to_datetime(item.timestamp_usec),
            source=source,
            authors=authors,
            labels=labels,
            mime_type="text/html",
            raw_bytes=content_html.encode("utf-8", errors="surrogatepass"),
            images=images,
        )

    @staticmethod
    def _select_body_html(item: FreshRSSItem) -> str:
        """按 FreshRSS content、summary 优先级选择单一正文块。

        Args:
            item: 已校验的 FreshRSS 外部文章。

        Returns:
            选中的 HTML；空正文仍保留实际字节。

        Notes:
            content 与 summary 常是同一正文的两种协议表示，绝不拼接二者。选择一个
            块可从源头避免整篇重复；块内部重复交给 Docling 结构解析保守处理。
        """

        for block in (item.content, item.summary):
            if block is not None and block.content.strip():
                return block.content
        for block in (item.content, item.summary):
            if block is not None:
                return block.content
        return ""

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
        return normalize_inline_text(title_text)

    def _images_from_html(
        self,
        content_html: str,
        article_url: str,
    ) -> tuple[ImageReference, ...]:
        """只提取既有图片引用元数据，不获取图片或生成索引正文。

        Args:
            content_html: FreshRSS 返回的正文或摘要 HTML。
            article_url: 文章规范 URL，用于解析正文中的相对图片地址。

        Returns:
            按正文顺序去重后的图片引用元组。

        Notes:
            不执行图片下载；网页回源和正文选择属于 FreshRSS 的职责。
        """

        soup = BeautifulSoup(content_html, "html.parser")

        # 隐藏资源不进入图片引用元数据；实际原件始终保持不变。
        for unwanted in soup.find_all(self._IGNORED_TAGS):
            unwanted.decompose()

        # 提取图片（img 标签的 src 等属性）
        return self._extract_images(soup, article_url)

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
    "FreshRSSItemMapper",
    "FreshRSSMappingError",
]
