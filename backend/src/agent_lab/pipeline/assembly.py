"""API、CLI 与 scheduler 共用的按需写 Runtime 装配。"""

from agent_lab.config.freshrss import get_freshrss_settings
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.config.scheduler import get_scheduler_settings
from agent_lab.db.session import async_session_factory
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.repositories.scheduled_job_repository import ScheduledJobStore
from agent_lab.knowledge.composition import build_document_processing_batch, build_source_import_service
from agent_lab.services.scheduler_runner import ScheduledJobRunner


def build_pipeline_write_runtime():
    return PipelineWriteRuntime.lazy(
        session_factory=async_session_factory,
        freshrss_factory=lambda: build_source_import_service(get_freshrss_settings()),
        processing_factory=build_document_processing_batch,
        qdrant_settings_factory=get_qdrant_settings,
    )


def build_scheduler_runner(pipeline_runtime_factory=build_pipeline_write_runtime, *, status_writer=None):
    return ScheduledJobRunner(
        store_factory=lambda: ScheduledJobStore(async_session_factory),
        write_runtime_factory=pipeline_runtime_factory, settings=get_scheduler_settings(), status_writer=status_writer,
    )


def build_document_processing_consumer():
    from agent_lab.knowledge.processing.consumer import DocumentProcessingConsumer
    return DocumentProcessingConsumer(
        build_document_processing_batch,
        shutdown_grace_seconds=get_scheduler_settings().shutdown_grace_seconds,
    )
