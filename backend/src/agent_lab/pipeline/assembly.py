"""API、CLI 与 Worker 共用的按需业务 Runtime 装配。"""

from agent_lab.config.freshrss import get_freshrss_settings
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.db.session import async_session_factory
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.knowledge.composition import build_document_processing_batch, build_source_import_service


def build_pipeline_write_runtime(session_factory=async_session_factory):
    return PipelineWriteRuntime.lazy(
        session_factory=session_factory,
        freshrss_factory=lambda: build_source_import_service(get_freshrss_settings(), session_factory),
        processing_factory=lambda: build_document_processing_batch(session_factory),
        qdrant_settings_factory=get_qdrant_settings,
    )
