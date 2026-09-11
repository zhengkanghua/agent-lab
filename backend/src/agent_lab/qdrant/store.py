"""候选 Point 写入与完整性核验；实例和文档删除按当前环境覆盖所有 generation。"""

import math
import re
from collections.abc import Sequence
from numbers import Real
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.payload import QdrantPayloadMapper
from agent_lab.domain.write_scope import remote_write
from agent_lab.knowledge.processing.indexing import IndexTarget


class QdrantPointStoreError(RuntimeError):
    """Point upsert、扫描或删除失败，或数据不符合 Qdrant 写入契约。"""


async def _owned_collections(client, settings):
    """清理只枚举本项目当前环境的物理 generation，不能匹配相似环境前缀。"""
    pattern = re.compile(rf"knowledge_chunks_{re.escape(settings.environment)}_v\d+_\d+")
    result = await client.get_collections()
    return sorted(item.name for item in result.collections if pattern.fullmatch(item.name))


class QdrantDeletionStore:
    """清除本项目当前环境的所有 generation 中指定文档，不删除 Collection。"""

    def __init__(self, client, settings: QdrantSettings) -> None:
        self._client = client
        self._settings = settings

    def _filter(self, document_ids):
        ids = [str(UUID(value)) for value in document_ids]
        return models.Filter(must=[models.FieldCondition(
            key="document_id", match=models.MatchAny(any=ids),
        )])

    async def count_by_document_ids(self, document_ids: list[str]) -> int:
        if not document_ids:
            return 0
        try:
            count = 0
            for collection in await _owned_collections(self._client, self._settings):
                result = await self._client.count(collection_name=collection, count_filter=self._filter(document_ids), exact=True)
                count += result.count
            return count
        except Exception as exc:
            raise QdrantPointStoreError(type(exc).__name__) from None

    @remote_write
    async def delete_by_document_ids(self, document_ids: list[str]) -> None:
        """只返回已完成确认；Qdrant 不提供实际删除 Point 数量。"""
        if not document_ids:
            return
        try:
            for collection in await _owned_collections(self._client, self._settings):
                result = await self._client.delete(collection_name=collection,
                    points_selector=models.FilterSelector(filter=self._filter(document_ids)), wait=True)
                QdrantChunkStore._ensure_completed(result, "document delete")
                remaining = await self._client.count(collection_name=collection, count_filter=self._filter(document_ids), exact=True)
                if remaining.count:
                    raise QdrantPointStoreError("文档 Point 删除尚未完成。")
        except Exception as exc:
            raise QdrantPointStoreError(type(exc).__name__) from None


class QdrantChunkStore:
    """按冻结清单写入独立实例；正常写入使用 current Alias，重建提供隔离目标。"""

    def __init__(
        self,
        client: AsyncQdrantClient,
        settings: QdrantSettings,
        spec: VectorIndexSpec,
        payload_mapper: QdrantPayloadMapper | None = None,
        *,
        rebuild_collection: str | None = None,
    ) -> None:
        """绑定 Qdrant client，并锁定 Alias、维度和 Payload 映射规则。

        Args:
            client: 异步 Qdrant client；测试可注入 fake。
            settings: 提供 ``current`` Alias 和 Point 写入批量大小。
            spec: 校验向量维度、模型版本和零范数规则的索引规格。
            payload_mapper: 可选 Payload mapper；缺省使用绑定规格创建标准 mapper。
        """

        self._client = client
        self._settings = settings
        self._spec = spec
        self._payload_mapper = payload_mapper or QdrantPayloadMapper(spec)
        # 只有重建适配器提供物理目标；普通装配固定使用 current Alias。
        if rebuild_collection is not None and rebuild_collection != settings.collection_name:
            raise ValueError("重建目标必须是配置中的独立 generation。")
        self._collection_alias = rebuild_collection or settings.collection_alias

    @property
    def collection_name(self) -> str:
        """返回普通写入 Alias 或重建指定的物理目标。"""

        return self._collection_alias

    @property
    def index_spec(self) -> VectorIndexSpec:
        """返回 Store 写入和校验 Point 时使用的不可变索引规格。"""

        return self._spec

    @staticmethod
    def _instance_filter(document_id: UUID, index_instance_id: UUID) -> models.Filter:
        return models.Filter(must=[
            models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id))),
            models.FieldCondition(key="index_instance_id", match=models.MatchValue(value=str(index_instance_id))),
        ])

    async def prepare_candidate(self, target: IndexTarget, vectors: Sequence[Sequence[Real]]) -> None:
        """只准备一个隔离索引实例；完整回读核验之前绝不删除旧 Point。"""
        if target.index_spec != self._spec.collection_metadata:
            raise QdrantPointStoreError("候选索引规格与写入目标不一致。")
        chunks = target.preview.chunk_result.chunks
        if not chunks or len(chunks) != len(vectors):
            raise QdrantPointStoreError("候选 Chunk 与向量数量不一致。")
        if [chunk.sequence for chunk in chunks] != list(range(len(chunks))):
            raise QdrantPointStoreError("候选 Chunk 序号不连续。")
        points = [models.PointStruct(
            id=target.chunk_id(chunk.sequence), vector=self._validate_vector(vector, chunk.sequence),
            payload=self._payload_mapper.build_candidate(target, chunk),
        ) for chunk, vector in zip(chunks, vectors, strict=True)]
        await self._upsert_points(points)
        await self.verify_candidate(target, vectors)

    async def verify_candidate(self, target: IndexTarget, vectors=None) -> None:
        """准备时核对向量方向，发布恢复时核对已持久确认目标的身份、Payload 和完整性。"""
        chunks = target.preview.chunk_result.chunks
        expected = {target.chunk_id(chunk.sequence): self._payload_mapper.build_candidate(target, chunk) for chunk in chunks}
        expected_vectors = dict(zip(expected, vectors, strict=True)) if vectors is not None else None
        try:
            count = await self._client.count(
                collection_name=self._collection_alias, exact=True,
                count_filter=self._instance_filter(target.document_id, target.index_instance_id),
            )
            if count.count != len(expected):
                raise QdrantPointStoreError("候选 Point 数量不完整。")
            ids = list(expected)
            for start in range(0, len(ids), self._settings.write_batch_size):
                batch = ids[start:start + self._settings.write_batch_size]
                records = await self._client.retrieve(
                    collection_name=self._collection_alias, ids=batch,
                    with_payload=True, with_vectors=True,
                )
                actual = {str(record.id): record for record in records}
                if len(actual) != len(records) or set(actual) != set(batch):
                    raise QdrantPointStoreError("候选 Point 身份不完整。")
                for point_id in batch:
                    record = actual[point_id]
                    if record.payload != expected[point_id]:
                        raise QdrantPointStoreError("候选 Point 内容与冻结预览不一致。")
                    if not isinstance(record.vector, list):
                        raise QdrantPointStoreError("候选 Point 缺少稠密向量。")
                    vector = self._validate_vector(record.vector, 0)
                    if expected_vectors is None:
                        continue
                    expected_vector = expected_vectors[point_id]
                    expected_norm, actual_norm = math.hypot(*expected_vector), math.hypot(*vector)
                    # Qdrant Cosine 存储 float32 单位向量；只比较方向，不要求原始幅值相等。
                    if any(not math.isclose(a / actual_norm, b / expected_norm, rel_tol=1e-4, abs_tol=1e-5)
                           for a, b in zip(vector, expected_vector, strict=True)):
                        raise QdrantPointStoreError("候选 Point 向量与本次写入不一致。")
        except QdrantPointStoreError:
            raise
        except Exception as exc:
            raise QdrantPointStoreError(type(exc).__name__) from None

    @remote_write
    async def delete_instance(self, document_id: UUID, index_instance_id: UUID) -> None:
        """跨 generation 回收明确指名的实例，全部确认后才移除排除记录。"""
        condition = self._instance_filter(document_id, index_instance_id)
        try:
            for collection in await _owned_collections(self._client, self._settings):
                result = await self._client.delete(collection_name=collection,
                    points_selector=models.FilterSelector(filter=condition), wait=True)
                self._ensure_completed(result, "instance delete")
                count = await self._client.count(collection_name=collection, count_filter=condition, exact=True)
                if count.count:
                    raise QdrantPointStoreError("旧索引实例清理尚未完成。")
        except QdrantPointStoreError:
            raise
        except Exception as exc:
            raise QdrantPointStoreError(type(exc).__name__) from None

    def _validate_vector(self, vector: Sequence[Real], index: int) -> list[float]:
        """
        验证维度、有限数值和非零 L2 范数，不执行手动归一化。
        最后返回的内容重新包了一层，是为了 过一道安检 + 转统一格式 + 产出体检通过的纯净副本
        """

        if len(vector) != self._spec.dimension:
            raise QdrantPointStoreError(
                f"索引 {index} 处向量维度不匹配：期望 "
                f"{self._spec.dimension}，实际 {len(vector)}。"
            )
        normalized: list[float] = []
        for value_index, value in enumerate(vector):
            if isinstance(value, bool) or not isinstance(value, Real):
                raise QdrantPointStoreError(
                    f"向量 {index} 第 {value_index} 位不是数值。"
                )
            number = float(value)
            if not math.isfinite(number):
                raise QdrantPointStoreError(
                    f"向量 {index} 第 {value_index} 位不是有限值。"
                )
            normalized.append(number)
        # 为什么用 hypot 而不是自己平方求和开根？ 
        # math.hypot 会缩放中间值，避免直接平方有限的大数时先溢出成 Infinity。
        # Cosine 需要非零且可表示的向量方向，异常 norm 不能交给 Qdrant 猜测处理。
        # 为什么要开根？为了算出"向量有多长（它的'大小/长度'）"。
        # 每个向量就是一串数，比如 (3, 4)。它的"长度"（不管方向，只看从原点到这个点的直线距离）用勾股定理算：√(3² + 4²) = √(9+16) = 5。这个"5"就是这个向量的长度（L2 范数）。
        l2_norm = math.hypot(*normalized)
        if l2_norm == 0.0:
            raise QdrantPointStoreError(f"向量 {index} 的 L2 范数为零。")
        if not math.isfinite(l2_norm):
            raise QdrantPointStoreError(f"向量 {index} 的 L2 范数不是有限值。")
        return normalized

    @remote_write
    async def _upsert_points(self, points: Sequence[models.PointStruct]) -> None:
        """
        按配置批量 upsert 到 current Alias，并等待服务端完成。"""

        for start in range(0, len(points), self._settings.write_batch_size):
            batch = list(points[start : start + self._settings.write_batch_size])
            if not batch:
                continue
            try:
                result = await self._client.upsert(
                    collection_name=self._collection_alias,
                    points=batch,
                    wait=True,
                )
            except Exception as exc:
                raise QdrantPointStoreError(
                    f"通过 Alias {self._collection_alias!r} 执行 Qdrant upsert 失败："
                    f"{type(exc).__name__}。"
                ) from None
            self._ensure_completed(result, "upsert")

    @staticmethod
    def _ensure_completed(result: Any, operation: str) -> None:
        """检查 wait=True 的写操作状态；fake 返回 None 时视为已完成。"""

        status = getattr(result, "status", None)
        if status is not None and status != models.UpdateStatus.COMPLETED:
            raise QdrantPointStoreError(
                f"Qdrant 操作 {operation} 未完成：status={status.value}。"
            )
