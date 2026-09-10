"""知识库生产装配；构造时无 I/O，连接在应用用例进入工作单元后建立。"""

from functools import lru_cache, partial
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


@lru_cache
def build_document_processor():
    """在后台计算线程首次构造，随后复用只读 tokenizer 与处理规格。"""
    from agent_lab.config.document_processing import get_document_processing_settings
    from agent_lab.knowledge.processing.processor import DocumentProcessor
    from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
    from agent_lab.knowledge.adapters.docling_chunker import DoclingStructuredChunker

    settings = get_document_processing_settings()
    return DocumentProcessor(
        parser=DoclingDocumentParser(),
        chunker=DoclingStructuredChunker(tokenizer_path=settings.tokenizer_path, max_tokens=settings.chunk_max_tokens),
    )


def build_document_processing_application(session_factory=async_session_factory):
    """接收和后台消费共用装配；缺少 S3 配置时明确失败，不回落到无原件路径。"""
    from agent_lab.config.object_storage import get_object_storage_settings
    from agent_lab.knowledge.adapters.processing import postgres_processing_work
    from agent_lab.knowledge.processing.application import DocumentProcessingApplication
    from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
    from agent_lab.knowledge.storage import ObjectStorageError, S3ObjectStorage

    try:
        storage = S3ObjectStorage(get_object_storage_settings())
    except ObjectStorageError as exc:
        raise ProcessingApplicationError(exc.code) from None
    return DocumentProcessingApplication(partial(postgres_processing_work, session_factory), storage, build_document_processor)


def build_document_adoption_application(session_factory=async_session_factory, *, qdrant_settings=None, ollama_settings=None):
    """人工受理与后台采用共用冻结契约；只有消费待办才创建写客户端。"""
    from agent_lab.config.ollama_embedding import get_ollama_embedding_settings
    from agent_lab.config.qdrant import get_qdrant_settings
    from agent_lab.knowledge.adapters.adoption import postgres_adoption_work
    from agent_lab.knowledge.processing.adoption import DocumentAdoptionApplication
    from agent_lab.qdrant.index_spec import VectorIndexSpec
    from agent_lab.qdrant.runtime import DocumentIndexingRuntime

    qdrant = qdrant_settings or get_qdrant_settings()
    ollama = ollama_settings or get_ollama_embedding_settings()
    spec = VectorIndexSpec.from_settings(qdrant, ollama)

    @asynccontextmanager
    async def indexer():
        runtime = DocumentIndexingRuntime.build(qdrant, ollama)
        try:
            await runtime.ensure_ready()
            yield runtime.service
        finally:
            await runtime.close()

    return DocumentAdoptionApplication(
        partial(postgres_adoption_work, session_factory), WriteCoordinator(session_factory),
        indexer, spec.collection_metadata,
    )


def build_document_processing_batch(session_factory=async_session_factory, **index_settings):
    from agent_lab.knowledge.processing.batch import DocumentProcessingBatch
    return DocumentProcessingBatch(
        build_document_processing_application(session_factory),
        build_document_adoption_application(session_factory, **index_settings),
    )


def build_file_document_service():
    """列表只读 PostgreSQL；上传按需创建原件接收组件，删除按需连接 Qdrant。"""
    from agent_lab.knowledge.adapters.files import postgres_file_work
    from agent_lab.knowledge.file_application import FileDocumentService
    from agent_lab.config.qdrant import get_qdrant_settings
    from agent_lab.qdrant.lifecycle import build_qdrant_client
    from agent_lab.qdrant.store import QdrantDeletionStore

    @asynccontextmanager
    async def deletion_store():
        settings = get_qdrant_settings()
        client = build_qdrant_client(settings)
        try:
            yield QdrantDeletionStore(client, settings)
        finally:
            await client.close()

    return FileDocumentService(
        partial(postgres_file_work, async_session_factory),
        WriteCoordinator(async_session_factory), deletion_store, build_document_processing_application,
    )


def build_source_import_service(settings, session_factory=async_session_factory, *, client_factory=FreshRSSClient,
                                processing_factory=None) -> SourceImportService:
    """生产与离线验证使用同一导入应用，替换外部来源和持久化适配器即可。"""
    @asynccontextmanager
    async def external_source():
        async with client_factory(settings) as client:
            yield FreshRSSSourceAdapter(settings, client)

    return SourceImportService(external_source, partial(postgres_import_work, session_factory),
                               processing_factory or partial(build_document_processing_application, session_factory))


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
