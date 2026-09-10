"""搜索只交付当前已采用实例，跨存储状态变化时整次重查。"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from agent_lab.schemas.vector_search import VectorSearchResult


class SearchVisibilityError(RuntimeError):
    """读取状态不稳定或无法核验；调用方可稍后重试，不交付未核验内容。"""


class IndexSearchHit(VectorSearchResult):
    """索引适配器内部结果；HTTP 仍投影为既有 VectorSearchResult。"""

    index_instance_id: UUID


@dataclass(frozen=True, slots=True)
class VisibilitySnapshot:
    revisions: tuple[tuple[UUID, int, bool], ...]
    excluded_index_instances: tuple[UUID, ...]


class DocumentVisibility(Protocol):
    async def snapshot(self, knowledge_base_ids: tuple[UUID, ...]) -> VisibilitySnapshot: ...
    async def validate(self, snapshot: VisibilitySnapshot, hits: list[IndexSearchHit]) -> bool: ...


class AdoptedVectorSearch:
    """包装向量查询端口；同一查询只向量化一次，漂移时重查而非事后删结果。"""

    def __init__(self, search, visibility: DocumentVisibility):
        self._search, self._visibility = search, visibility
        self.index_spec = search.index_spec

    async def _query(self, vector, *, filters, grouped, **parameters):
        scope = filters.knowledge_base_ids or ((filters.knowledge_base_id,) if filters.knowledge_base_id else ())
        if not scope:
            raise ValueError("版本核验必须明确知识库范围。")
        query = self._search.search_groups if grouped else self._search.search
        for _ in range(3):
            snapshot = await self._visibility.snapshot(tuple(scope))
            results = await query(vector, filters=filters, excluded_index_instances=snapshot.excluded_index_instances, **parameters)
            hits = [hit for group in results for hit in group.matches] if grouped else results
            if await self._visibility.validate(snapshot, hits):
                # 底层按实例分组；经过当前指向核验后，同 Document 应恰好剩一组。
                if grouped and len({group.document_id for group in results}) != len(results):
                    raise SearchVisibilityError()
                return results
        raise SearchVisibilityError()

    async def search(self, vector, *, top_k, score_threshold, filters):
        return await self._query(vector, top_k=top_k, score_threshold=score_threshold, filters=filters, grouped=False)

    async def search_groups(self, vector, *, document_limit, matches_per_document, score_threshold, filters):
        return await self._query(vector, document_limit=document_limit, matches_per_document=matches_per_document,
                                 score_threshold=score_threshold, filters=filters, grouped=True)
