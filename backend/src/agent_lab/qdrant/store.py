"""通过 current Alias 写入、扫描和删除新闻 Chunk Point。

将chunk,vector，document_id传入，然后写入向量数据库

先解释三个名词：
- Point：Qdrant 里一条存储记录 = 稳定 Chunk UUID（Point ID）+ 向量（Vector）+
  附加字段（Payload）；
- dense Vector：密集向量，即 1024 个浮点数排成的数组；
- Payload：附加的普通 JSON 字段（标题、URL、时间等）。

本模块的硬约束：所有读写都把 current Alias 当作 collection_name 使用——普通应用
代码永远碰不到物理 Collection 名。它不创建 Collection/Alias、不检索、不生成
Embedding、不修改 PostgreSQL 状态；完整状态编排由上层 DocumentIndexingService 负责。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any
from uuid import UUID

from langchain_core.documents import Document
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.payload import QdrantPayloadMapper


class QdrantPointStoreError(RuntimeError):
    """Point upsert、扫描或删除失败，或数据不符合 Qdrant 写入契约。"""


@dataclass(frozen=True, slots=True)
class ReplaceChunksResult:
    """记录一次单篇新闻替换操作实际 upsert 和删除的 Point ID。

    结果只存在于当前索引任务内存中，不保存 Vector 或 Payload；它用于 Service 报告和
    测试幂等行为，不能当作 Qdrant 事务日志。只有方法无异常返回时，ID 元组才表示
    current Alias 下本次已确认完成的操作。
    """

    document_id: str
    upserted_ids: tuple[str, ...]
    deleted_ids: tuple[str, ...]


class QdrantChunkStore:
    """通过 current Alias 对新闻 Chunk 做「整篇替换」的写入器。

    核心操作 replace_document_chunks 的顺序（先建后删）：
    1. 先读出一篇新闻现有的全部旧 Point ID；
    2. 把新版本的 Chunk 全部 upsert（写入/覆盖）进去；
    3. 最后删除「旧有但新版本不再需要」的多余 Point。
    为什么先写后删而不是先删后写：先删会让这篇新闻在 Qdrant 里出现一段「完全
    没有数据」的可见空窗；先写后删最多短暂出现新旧并存，检索质量好得多。

    Store 生命周期覆盖多个索引任务，不持有 PostgreSQL Session；可被多个 asyncio
    Task 复用，内部无可变状态。写入前会一次性构造并验证所有 Point，避免输入错误
    导致半批数据入库。
    """

    def __init__(
        self,
        client: AsyncQdrantClient,
        settings: QdrantSettings,
        spec: VectorIndexSpec,
        payload_mapper: QdrantPayloadMapper | None = None,
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
        self._collection_alias = settings.collection_alias

    @property
    def collection_name(self) -> str:
        """返回 Store 实际使用的名称；这里必定是 current Alias 而非物理 Collection。"""

        return self._collection_alias

    @property
    def index_spec(self) -> VectorIndexSpec:
        """返回 Store 写入和校验 Point 时使用的不可变索引规格。"""

        return self._spec

    async def replace_document_chunks(
        self,
        document_id: str,
        chunks: Sequence[Document],
        vectors: Sequence[Sequence[Real]],
    ) -> ReplaceChunksResult:
        """用当前 Chunk 集合替换一篇新闻在 Alias 下的全部 Point。

        Args:
            document_id: PostgreSQL ``DocumentRecord.id`` 字符串，用于定位旧 Point。
            chunks: 当前版本按原文顺序排列的 LangChain Chunk。
            vectors: 与 chunks 一一对应的已完成 Embedding 向量。

        Returns:
            本次成功完成的 upsert ID 和删除旧 ID 结果。

        Raises:
            QdrantPointStoreError: 数量、ID、Payload、向量数值/维度、Qdrant 写入或删除
                不满足契约。

        Notes:
            本方法进行 Qdrant 网络 I/O，但所有请求都使用 current Alias。空 chunks
            表示该文档当前没有可写 Chunk，会删除 Alias 中该文档的旧 Point；成功
            upsert 后才删除旧尾部 Point，避免删除先于新数据造成更大的可见空窗。
        """

        # 0、前置校验：文档 ID 必须是合法 UUID；Chunk 和向量必须一一对应
        document_id = self._canonical_uuid(document_id, context="document_id")
        if len(chunks) != len(vectors):
            raise QdrantPointStoreError(
                f"Chunk 与向量数量不匹配：{len(chunks)} 个 Chunk，{len(vectors)} 个向量。"
            )

        # 1、在内存中构造并验证本版全部 Point（此时不发起任何网络请求）
        points = self._build_points(document_id, chunks, vectors)
        # 2、读出这篇新闻当前在 Qdrant 里的旧 Point ID
        existing_ids = await self.list_point_ids(document_id)
        current_ids = {str(point.id) for point in points}
        if len(current_ids) != len(points):
            raise QdrantPointStoreError("同一文档批次内的 Chunk ID 必须唯一。")

        # 3、先写入新 Point（幂等覆盖同 ID 的旧版本）
        await self._upsert_points(points)
        # 4、再删除「新版本已不需要」的旧 Point——先建后删，避免可见空窗
        # stale_ids = 旧的ID - 新的ID  多的旧id就会被删除
        # 所以使用这个方法，最好是一个文档所有chunk以前upsert，不然会导致只保留修改的chunk
        stale_ids = sorted(existing_ids - current_ids)
        if stale_ids:
            await self._delete_ids(stale_ids)
        return ReplaceChunksResult(
            document_id=document_id,
            upserted_ids=tuple(str(point.id) for point in points),
            deleted_ids=tuple(stale_ids),
        )

    async def list_point_ids(self, document_id: str) -> set[str]:
        """通过 current Alias 分页读取一篇新闻已有的 Point ID。

        Args:
            document_id: Payload 中的 PostgreSQL 文档 UUID 字符串。

        Returns:
            Alias 中匹配 ``document_id`` 的 Point ID 集合。

        Raises:
            QdrantPointStoreError: Qdrant scroll 请求失败。

        Notes:
            这是 Qdrant 网络 I/O，不读取向量正文；分页是为了避免长新闻一次性加载
            全部 Point。业务代码不能把物理 Collection 名传入本 Store。
        """

        document_id = self._canonical_uuid(document_id, context="document_id")
        point_ids: set[str] = set()
        offset: Any = None
        # 构造过滤条件：只取 Payload.document_id == 当前文档 的 Point
        scroll_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            ]
        )
        try:
            # 分页滚动：Qdrant 用 offset 游标翻页，翻到 offset=None 表示取完了
            while True:
                records, offset = await self._client.scroll(
                    collection_name=self._collection_alias,
                    scroll_filter=scroll_filter,
                    limit=self._settings.write_batch_size,
                    offset=offset,
                    with_payload=False,
                    with_vectors=False,
                )
                point_ids.update(str(record.id) for record in records)
                if offset is None:
                    break
        except Exception as exc:
            raise QdrantPointStoreError(
                f"无法通过 Alias {self._collection_alias!r} 列出 Qdrant Points："
                f"{type(exc).__name__}。"
            ) from None
        return point_ids

    async def delete_document(self, document_id: str) -> tuple[str, ...]:
        """通过 current Alias 删除一篇新闻的全部 Point。

        Args:
            document_id: Payload 中的 PostgreSQL 文档 UUID 字符串。

        Returns:
            已删除的 Point ID，按稳定字符串顺序排列。

        Raises:
            QdrantPointStoreError: 扫描或删除失败。

        Notes:
            这是 Qdrant 网络 I/O；只有明确业务删除事件才应调用，FreshRSS 本轮没有
            返回某篇新闻不等于可以删除它。
        """

        ids = sorted(await self.list_point_ids(document_id))
        if ids:
            await self._delete_ids(ids)
        return tuple(ids)

    def _build_points(
        self,
        document_id: str,
        chunks: Sequence[Document],
        vectors: Sequence[Sequence[Real]],
    ) -> list[models.PointStruct]:
        """在任何远程写入前构造并验证完整 Point 列表。"""

        points: list[models.PointStruct] = []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            if chunk.id is None or not chunk.id.strip():
                raise QdrantPointStoreError(f"Chunk {index} 没有稳定的 ID。")
            canonical_chunk_id = self._canonical_uuid(
                chunk.id,
                context=f"Chunk {index} ID",
            )
            # payload的构建
            payload = self._payload_mapper.build(chunk)
            if payload["document_id"] != document_id:
                raise QdrantPointStoreError(
                    f"Chunk {chunk.id!r} 属于文档 {payload['document_id']!r}，"
                    f"而不是 {document_id!r}。"
                )
            normalized_vector = self._validate_vector(vector, index)
            points.append(
                models.PointStruct(
                    id=canonical_chunk_id,
                    vector=normalized_vector,
                    payload=payload,
                )
            )
        return points

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

    async def _delete_ids(self, ids: Sequence[str]) -> None:
        """按批次从 current Alias 删除 Point，并等待服务端完成。"""

        for start in range(0, len(ids), self._settings.write_batch_size):
            batch = list(ids[start : start + self._settings.write_batch_size])
            try:
                result = await self._client.delete(
                    collection_name=self._collection_alias,
                    points_selector=batch,
                    wait=True,
                )
            except Exception as exc:
                raise QdrantPointStoreError(
                    f"通过 Alias {self._collection_alias!r} 执行 Qdrant 删除失败："
                    f"{type(exc).__name__}。"
                ) from None
            self._ensure_completed(result, "delete")

    @staticmethod
    def _ensure_completed(result: Any, operation: str) -> None:
        """检查 wait=True 的写操作状态；fake 返回 None 时视为已完成。"""

        status = getattr(result, "status", None)
        if status is not None and status != models.UpdateStatus.COMPLETED:
            raise QdrantPointStoreError(
                f"Qdrant 操作 {operation} 未完成：status={status.value}。"
            )

    @staticmethod
    def _canonical_uuid(value: str, *, context: str) -> str:
        """验证并规范化 PostgreSQL 文档/Chunk Point 的 UUID 字符串。"""

        try:
            return str(UUID(value))
        except (AttributeError, ValueError) as exc:
            raise QdrantPointStoreError(
                f"{context} 必须是 UUID 字符串。"
            ) from exc
