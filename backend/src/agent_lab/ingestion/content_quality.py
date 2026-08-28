"""规范化进入持久化、LangChain 构建和 Embedding 之前的新闻纯文本。

本模块位于 ingestion 层，只处理与来源协议无关、可以确定解释的文本质量问题。它
统一 HTML entity、Unicode NFC 与空白，并仅删除边界标题块和相邻完全重复段落；它
不做语义相似度、跨来源去重、网页正文抽取或数据库写回，也不会记录原始正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field


type ContentKind = Literal["content", "summary", "unknown"]
type ContentRejectionReason = Literal[
    "empty_content",
    "title_only",
    "content_too_short",
]


class ContentQualityResult(BaseModel):
    """一篇正文经过确定性规范化后的质量结果。

    对象只存在于导入或离线诊断期间。``normalized_text`` 是后续 content_hash、
    revision、LangChain ``Document.page_content`` 和 Chunk 的唯一正文输入；统计字段
    解释发生了哪些确定性删除，不包含被删除的原文。
    """

    original_char_count: int = Field(
        ge=0,
        description="规范化前正文的 Python 字符数，不包含标题参数。",
    )
    normalized_text: str = Field(
        description="完成 HTML entity、NFC、空白和保守去重后的正文；不可用时可为空。",
    )
    normalized_char_count: int = Field(
        ge=0,
        description="规范化后正文的 Python 字符数，供质量诊断使用。",
    )
    removed_title_line: bool = Field(
        description="是否删除了正文开头与标题匹配的完整独立文本块。",
    )
    removed_trailing_title_line: bool = Field(
        description="是否删除了正文末尾与标题匹配的完整独立文本块。",
    )
    removed_duplicate_prefix: bool = Field(
        description="兼容历史诊断字段；阶段 6 不再删除单行重复半段，恒为 false。",
    )
    removed_duplicate_lines: int = Field(
        ge=0,
        description="删除的相邻、规范化后完全相同的完整正文块数量。",
    )
    content_kind: ContentKind = Field(
        description="正文来自 FreshRSS content、summary，或历史记录无法判断时为 unknown。",
    )
    is_usable: bool = Field(
        description="正文是否非空、不是仅标题且达到当前诊断最小字符数。",
    )
    rejection_reason: ContentRejectionReason | None = Field(
        default=None,
        description="不可用时的稳定质量原因；可用正文为 null，不包含原文。",
    )


@dataclass(frozen=True, slots=True)
class ContentQualityNormalizer:
    """用幂等、保守且可解释的规则规范化一篇纯文本正文。

    实例无状态，可跨文章复用。``min_content_chars`` 是质量状态阈值，不代表数据库
    约束：Mapper 会拒绝 ``empty_content`` 和 ``title_only``，但可明确保留合法短快讯。
    标题比较只发生在正文首尾完整块，并仅忽略 Unicode 标点、大小写和空白；正文中间
    的同句重复不会触发删除。相邻段落去重要求规范化后的字符串完全相同。
    """

    min_content_chars: int = 80

    def __post_init__(self) -> None:
        """校验正文质量阈值。

        Raises:
            ValueError: ``min_content_chars`` 小于一。
        """

        if self.min_content_chars < 1:
            raise ValueError("min_content_chars 必须大于零")

    def inspect(
        self,
        *,
        title: str,
        content_text: str,
        content_kind: ContentKind = "unknown",
    ) -> ContentQualityResult:
        """规范化正文并返回明确质量状态，不执行任何外部 I/O。

        Args:
            title: 文章标题；允许为空，此时不会执行标题边界删除。
            content_text: 准备持久化的纯文本正文，可以包含 HTML entity 和 Unicode 空白。
            content_kind: 正文来自 FreshRSS ``content``、``summary``，或未知历史记录。

        Returns:
            规范化正文、删除计数和稳定拒绝原因。相同输入重复调用得到完全相同结果。
        """

        # 1. 全文先做 Unicode NFC + HTML entity 解码，再按换行切成「段落」
        normalized_title = self.normalize_inline_text(title)
        paragraphs = self._normalize_paragraphs(content_text)
        had_nonempty_content = bool(paragraphs)
        removed_title_line = False
        removed_trailing_title_line = False

        # 2. 去掉「正文开头或结尾与标题完全重复的完整段落」——很多源会把标题在
        #    正文里再贴一遍。比较键忽略标点/空白/大小写，只在首尾做
        title_key = self._title_comparison_key(normalized_title)
        if title_key and paragraphs and self._title_comparison_key(paragraphs[0]) == title_key:
            paragraphs.pop(0)
            removed_title_line = True
        if title_key and paragraphs and self._title_comparison_key(paragraphs[-1]) == title_key:
            paragraphs.pop()
            removed_trailing_title_line = True

        # 3. 只压缩「相邻且规范化后完全相同」的完整段落。非相邻重复可能是作者有意
        #    的结构回环/引用，单段内重复句子也可能有语义，都不删
        deduplicated: list[str] = []
        removed_duplicate_lines = 0
        for paragraph in paragraphs:
            if deduplicated and paragraph == deduplicated[-1]:
                removed_duplicate_lines += 1
                continue
            deduplicated.append(paragraph)

        # 4. 用换行拼接段落（保持历史序列化格式），再统计长度、定拒绝原因
        normalized_text = "\n".join(deduplicated)
        normalized_char_count = len(normalized_text)
        rejection_reason: ContentRejectionReason | None
        if not had_nonempty_content:
            rejection_reason = "empty_content"
        elif not normalized_text and (removed_title_line or removed_trailing_title_line):
            rejection_reason = "title_only"
        elif normalized_char_count < self.min_content_chars:
            rejection_reason = "content_too_short"
        else:
            rejection_reason = None

        return ContentQualityResult(
            original_char_count=len(content_text),
            normalized_text=normalized_text,
            normalized_char_count=normalized_char_count,
            removed_title_line=removed_title_line,
            removed_trailing_title_line=removed_trailing_title_line,
            removed_duplicate_prefix=False,
            removed_duplicate_lines=removed_duplicate_lines,
            content_kind=content_kind,
            is_usable=rejection_reason is None,
            rejection_reason=rejection_reason,
        )

    @classmethod
    def normalize_inline_text(cls, value: str) -> str:
        """统一一个标题或段落内部的 entity、Unicode 形式和空白。

        Args:
            value: 外部来源提供的纯文本，可能包含 ``&nbsp;`` 等 entity。

        Returns:
            NFC 形式、首尾无空白且内部任意 Unicode 空白压缩为普通空格的文本。
        """

        normalized = unicodedata.normalize("NFC", unescape(value))
        return " ".join(normalized.split())

    @classmethod
    def _normalize_paragraphs(cls, content_text: str) -> list[str]:
        """把换行分隔的正文转换为稳定非空文本块。

        Args:
            content_text: 待规范化的纯文本正文。

        Returns:
            保持原顺序的非空段落；段内空白已归一化，换行统一由调用方重建。
        """

        normalized = unicodedata.normalize("NFC", unescape(content_text))
        return [
            paragraph
            for line in normalized.splitlines()
            if (paragraph := cls.normalize_inline_text(line))
        ]

    @staticmethod
    def _title_comparison_key(value: str) -> str:
        """生成只用于正文首尾标题判断的严格比较键。

        Args:
            value: 已抽取为纯文本的标题或完整正文块。

        Returns:
            NFKC、casefold 后移除 Unicode 空白与标点的字符串。字母、数字和符号仍
            保留，因此不会把正文中的近似句子当作标题；空输入返回空字符串。
        """

        comparable = unicodedata.normalize("NFKC", value).casefold()
        return "".join(
            character
            for character in comparable
            if not character.isspace()
            and not unicodedata.category(character).startswith("P")
        )


__all__ = ["ContentQualityNormalizer", "ContentQualityResult"]
