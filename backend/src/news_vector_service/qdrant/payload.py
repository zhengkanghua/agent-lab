"""把 LangChain Chunk Document 映射成受控的 Qdrant Payload。

Payload = 向量旁边附带的结构化数据，用途有四：展示新闻内容、按来源/时间过滤、
按文档清理旧 Chunk、审计索引版本。本模块只从 Chunk 的 ``page_content`` 和「白名单
Metadata」生成一个扁平字典（纯内存操作，无任何 I/O）。

在 Qdrant 里，一个 Point（一条记录）= 向量（那 1024 个数）+ Payload（一堆附加的 JSON 字段：标题、URL、来源、正文、标签、时间…）。
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.documents import Document

from news_vector_service.qdrant.index_spec import VectorIndexSpec


class QdrantPayloadError(ValueError):
    """Chunk 缺少 Payload 必需字段或字段类型不符合契约。"""


# mapper 就是中转序列化的对象
class QdrantPayloadMapper:
    """
    封装Qdrant Payload的，维持Payload结构

    把一个 Chunk 的正文和业务 Metadata 转成稳定、扁平的 Payload。

    Mapper 存在是为了隔离「LangChain 内存对象」和「Qdrant 存储格式」两种形态：
    - chunk.page_content → Payload.page_content（这是被 Embedding 的原文，检索时展示用）；
    - chunk.id → 直接变成 Qdrant Point 的 ID（不进 Payload，因为 Point 自带主键）；
    - 其他白名单 Metadata → Payload 的业务字段。

    实例无状态、可多任务共享；映射过程不进行任何网络/数据库/Embedding/向量库 I/O。
    """

    # 这些就是需要转为Payload的字段
    REQUIRED_FIELDS = (
        "document_id",
        "source_id",
        "source_provider",
        "document_external_id",
        "document_type",
        "title",
        "url",
        "content_hash",
    )

    def __init__(self, spec: VectorIndexSpec) -> None:
        """绑定不可变索引规格，用于写入版本和模型审计字段。

        Args:
            spec: 当前物理 Collection 所使用的索引契约。
        """

        self._spec = spec

    def build(self, chunk: Document) -> dict[str, Any]:
        """把一个 LangChain Chunk 转换为 Qdrant 扁平 Payload。

        Args:
            chunk: ``DocumentChunker`` 生成的 Chunk。只有 ``page_content`` 进入
                Embedding；``id`` 不复制进 Payload，而是由 Point 使用。

        Returns:
            包含正文、新闻字段、Chunk 关系和索引规格审计信息的 Payload 字典。

        Raises:
            QdrantPayloadError: Chunk ID、正文、必需 Metadata 缺失或列表字段类型错误。

        Notes:
            本方法只做内存映射。缺失的可选 ``published_at`` 和 ``source_updated_at``
            不写入 null，以保持时间字段类型稳定；新闻没有发布时间时不伪造抓取时间。
        """

        # 1. 校验 Chunk 本身：必须要有稳定 ID 和非空正文
        if chunk.id is None or not chunk.id.strip():
            raise QdrantPayloadError("映射到 Qdrant 前必须设置 Chunk 的 Document.id。")

        # 非空文本
        if not isinstance(chunk.page_content, str) or not chunk.page_content.strip():
            raise QdrantPayloadError("Chunk 的 page_content 必须包含非空白文本。")

        # 2. 校验必需 Metadata：白名单字段一个都不能缺
        metadata = chunk.metadata
        for field in self.REQUIRED_FIELDS:
            if field not in metadata or metadata[field] in (None, ""):
                raise QdrantPayloadError(f"缺少必需的 Chunk 元数据字段 {field!r}。")

        # 3. 抽取并强类型校验关键字段（UUID / 内容哈希 / Chunk 序号）
        document_id = self._required_uuid(metadata, "document_id")
        source_id = self._required_uuid(metadata, "source_id")
        content_hash = self._required_string(metadata, "content_hash")
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash.lower()
        ):
            raise QdrantPayloadError(
                "Chunk 元数据字段 'content_hash' 必须是 64 位 SHA-256 十六进制字符串。"
            )
        chunk_index = self._required_int(metadata, "chunk_index")
        chunk_count = self._required_int(metadata, "chunk_count")
        if chunk_count < 1 or chunk_index >= chunk_count:
            raise QdrantPayloadError(
                "Chunk 元数据要求 chunk_count > 0 且 chunk_index < chunk_count。"
            )

        # 4. 组装扁平 Payload：正文 + 业务字段 + 索引规格审计字段
        payload: dict[str, Any] = {
            "page_content": chunk.page_content,
            "document_id": document_id,
            "content_hash": content_hash,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "title": self._required_string(metadata, "title"),
            "url": self._required_string(metadata, "url"),
            "document_type": self._required_string(metadata, "document_type"),
            "source_id": source_id,
            "source_provider": self._required_string(metadata, "source_provider"),
            "source_name": self._required_string(metadata, "source_name"),
            "source_external_id": self._required_string(metadata, "source_external_id"),
            "document_external_id": self._required_string(
                metadata, "document_external_id"
            ),
            "authors": self._required_string_list(metadata, "authors"),
            "labels": self._required_string_list(metadata, "labels"),
            "index_schema_version": self._spec.schema_version,
            "embedding_model": self._spec.embedding_model,
        }

        # 5. 可选关系字段（前/后 Chunk）：有值才写入，缺失不写 null（保持类型稳定）
        for optional_field in (
            "previous_chunk_id",
            "next_chunk_id",
        ):
            value = metadata.get(optional_field)
            if value is not None:
                if not isinstance(value, str):
                    raise QdrantPayloadError(
                        f"可选 Chunk 元数据字段 {optional_field!r} 必须是 UUID 字符串。"
                    )
                try:
                    payload[optional_field] = str(UUID(value))
                except ValueError as exc:
                    raise QdrantPayloadError(
                        f"可选 Chunk 元数据字段 {optional_field!r} 必须是 UUID 字符串。"
                    ) from exc
        # 6. 可选时间字段：缺失不伪造抓取时间，有值则强制带时区
        for optional_time_field in ("published_at", "source_updated_at"):
            value = metadata.get(optional_time_field)
            if value is not None:
                payload[optional_time_field] = self._timezone_datetime_string(
                    value,
                    optional_time_field,
                )
        return payload

    def build_many(self, chunks: list[Document]) -> list[dict[str, Any]]:
        """
        多个 Chunk批量 转换为Payload
        按输入顺序映射多个 Chunk，遇到任何错误立即停止。

        Args:
            chunks: 按原文顺序排列的 LangChain Chunk 列表。

        Returns:
            与输入一一对应的 Payload 列表。

        Raises:
            QdrantPayloadError: 任一 Chunk 不符合 Payload 契约。

        Notes:
            空列表返回空列表，不进行 I/O。
        """

        return [self.build(chunk) for chunk in chunks]

    @staticmethod
    def _required_string(metadata: Mapping[str, Any], field: str) -> str:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise QdrantPayloadError(f"Chunk 元数据字段 {field!r} 必须是字符串。")
        return value

    @classmethod
    def _required_uuid(cls, metadata: Mapping[str, Any], field: str) -> str:
        value = cls._required_string(metadata, field)
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise QdrantPayloadError(
                f"Chunk 元数据字段 {field!r} 必须是 UUID 字符串。"
            ) from exc

    @staticmethod
    def _required_int(metadata: Mapping[str, Any], field: str) -> int:
        """拒绝布尔的数字"""
        value = metadata.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise QdrantPayloadError(
                f"Chunk 元数据字段 {field!r} 必须是非负整数。"
            )
        return value

    @staticmethod
    def _required_string_list(metadata: Mapping[str, Any], field: str) -> list[str]:
        value = metadata.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise QdrantPayloadError(
                f"Chunk 元数据字段 {field!r} 必须是字符串列表。"
            )
        return list(value)

    @staticmethod
    def _timezone_datetime_string(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise QdrantPayloadError(
                f"可选 Chunk 元数据字段 {field!r} 必须是 ISO datetime 字符串。"
            )
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise QdrantPayloadError(
                f"可选 Chunk 元数据字段 {field!r} 必须是 ISO datetime 字符串。"
            ) from exc
        if parsed.utcoffset() is None:
            raise QdrantPayloadError(
                f"可选 Chunk 元数据字段 {field!r} 必须包含时区信息。"
            )
        return parsed.isoformat()
