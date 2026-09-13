"""真实 Celery 业务联调的外部端口替身；生产 Runtime、处理算法及事务保持原实现。"""

import asyncio
from contextlib import asynccontextmanager
from functools import partial
import json
import os
from pathlib import Path
import sys

from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.knowledge.document_contracts import SourceImportPage
from agent_lab.services.write_coordination import WriteCoordinator
from tests.task_queue_support import database

SOURCE = SourceInfo(provider="synthetic", external_id="feed/1", name="任务业务联调来源")


class SyntheticSource:
    """每页只有一份合成正文，测试文件控制上游当前可见的页，不访问 FreshRSS。"""

    async def list_sources(self):
        return [SOURCE]

    async def read_page(self, source, *, expected_checkpoint, limit):
        page = (Path(os.environ["TASK_TEST_BUSINESS_DIRECTORY"]) / "source-page.txt").read_text()
        if page == expected_checkpoint:
            return SourceImportPage((), expected_checkpoint)
        document = SourceDocument(external_id=f"article/{page}", title=f"Synthetic {page}",
            url=f"https://example.invalid/article/{page}", source=source,
            raw_bytes=f"<h1>Synthetic {page}</h1><p>Body for task execution {page}.</p>".encode())
        return SourceImportPage((document,), page)


def install_business_ports():
    """只替换来源、原件、Embedding 和向量端口，解析、采用和批次均走生产应用。"""
    from agent_lab.knowledge import composition
    from agent_lab.knowledge.adapters.adoption import postgres_adoption_work
    from agent_lab.knowledge.adapters.importing import postgres_import_work
    from agent_lab.knowledge.importing import SourceImportService
    from agent_lab.knowledge.processing.adoption import DocumentAdoptionApplication
    from agent_lab.knowledge.processing.indexing import CandidateIndexer
    from agent_lab.pipeline import assembly
    from agent_lab.qdrant.index_spec import VectorIndexSpec
    from agent_lab.tasks.context import current_run_id
    from tests.test_processing_application import MemoryStorage

    storage = MemoryStorage()
    composition.build_document_storage = lambda: storage
    spec = VectorIndexSpec(dimension=3, chunk_size=128)
    points = Path(os.environ["TASK_TEST_BUSINESS_DIRECTORY"]) / "points"
    points.mkdir(exist_ok=True)

    class Embeddings:
        embedding_model = spec.embedding_model

        async def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    class Points:
        async def prepare_candidate(self, target, vectors):
            # 文件证明真实 Worker 交给向量端口的是哪份冻结正文及哪次执行。
            (points / f"{target.index_instance_id}.json").write_text(json.dumps({
                "document_id": str(target.document_id), "run_id": str(current_run_id.get()),
                "process_id": os.getpid(), "chunks": [chunk.embedding_text for chunk in target.preview.chunk_result.chunks],
                "vector_count": len(vectors),
            }), encoding="utf-8")

        async def delete_instance(self, document_id, index_instance_id):
            (points / f"{index_instance_id}.json").unlink(missing_ok=True)

    @asynccontextmanager
    async def indexer():
        yield CandidateIndexer(Embeddings(), Points(), spec.collection_metadata)

    def adoption(sessions, **_settings):
        return DocumentAdoptionApplication(partial(postgres_adoption_work, sessions),
            WriteCoordinator(sessions), indexer, spec.collection_metadata)

    @asynccontextmanager
    async def external_source():
        yield SyntheticSource()

    def importer(_settings, sessions):
        return SourceImportService(external_source, partial(postgres_import_work, sessions),
            partial(composition.build_document_processing_application, sessions))

    composition.build_document_adoption_application = adoption
    assembly.build_source_import_service = importer
    assembly.get_freshrss_settings = lambda: None


async def hold_index():
    """独立进程使用生产协调器持有 index，测试发出释放文件后正常退出。"""
    directory = Path(os.environ["TASK_TEST_BUSINESS_DIRECTORY"])
    engine, sessions = database(os.environ["TASK_TEST_DATABASE_URL"], os.environ["TASK_TEST_SCHEMA"])
    try:
        async with WriteCoordinator(sessions).hold(("index",)):
            (directory / "index-held").touch()
            async with asyncio.timeout(180):
                while not (directory / "release-index").exists():
                    await asyncio.sleep(0.1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(hold_index())
