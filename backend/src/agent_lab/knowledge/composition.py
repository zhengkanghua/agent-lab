"""知识库生产装配；构造时无 I/O，连接在应用用例进入工作单元后建立。"""

from functools import partial
from contextlib import AsyncExitStack, asynccontextmanager

from agent_lab.db.session import async_session_factory
from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.adapters.sources import postgres_source_binding_work
from agent_lab.services.source_binding_service import SourceBindingService
from agent_lab.services.write_coordination import WriteCoordinator
from agent_lab.knowledge.adapters.freshrss import FreshRSSSourceAdapter
from agent_lab.knowledge.adapters.importing import postgres_import_work
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.ingestion.freshrss_client import FreshRSSClient


def build_knowledge_base_service() -> KnowledgeBaseService:
    """HTTP、CLI 和任务入口可复用同一个应用服务装配。"""

    return KnowledgeBaseService(partial(postgres_knowledge_base_work, async_session_factory))


def build_source_binding_service() -> SourceBindingService:
    """复用同一数据库和持久写协调协议；构造时不打开连接。"""
    return SourceBindingService(
        partial(postgres_source_binding_work, async_session_factory),
        WriteCoordinator(async_session_factory),
    )


def build_source_import_service(settings, session_factory=async_session_factory, *, client_factory=FreshRSSClient) -> SourceImportService:
    """生产与离线验证使用同一导入应用，替换外部来源和持久化适配器即可。"""
    @asynccontextmanager
    async def external_source():
        async with client_factory(settings) as client:
            yield FreshRSSSourceAdapter(settings, client)

    return SourceImportService(external_source, partial(postgres_import_work, session_factory))


@asynccontextmanager
async def index_rebuild_service(generation: int):
    """显式维护命令使用的重建装配；客户端随一次命令关闭。"""
    from agent_lab.config.qdrant import get_qdrant_settings
    from agent_lab.config.ollama_embedding import get_ollama_embedding_settings
    from agent_lab.knowledge.adapters.rebuilding import PostgresRebuildRepository
    from agent_lab.knowledge.rebuilding import IndexRebuildService
    from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
    from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
    from agent_lab.qdrant.index_spec import VectorIndexSpec
    from agent_lab.qdrant.lifecycle import build_qdrant_client
    from agent_lab.qdrant.rebuilding import QdrantRebuildTarget

    if generation < 1:
        raise ValueError("generation 必须大于零")
    settings = get_qdrant_settings().model_copy(update={"collection_generation": generation})
    ollama = get_ollama_embedding_settings()
    spec = VectorIndexSpec.from_settings(settings, ollama)
    async with AsyncExitStack() as stack:
        client = build_qdrant_client(settings)
        stack.push_async_callback(client.close)
        embeddings = OllamaEmbeddingProvider(ollama)
        stack.push_async_callback(embeddings.close)
        yield IndexRebuildService(
            PostgresRebuildRepository(async_session_factory),
            QdrantRebuildTarget(client, settings, spec, DocumentChunkPipeline(), embeddings),
            WriteCoordinator(async_session_factory),
        )
