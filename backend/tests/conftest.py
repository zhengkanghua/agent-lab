"""默认测试禁止真实连接；显式授权的集成测试继续使用各自的环境开关。"""

import os

import httpx
import psycopg
import pytest


INTEGRATION_SWITCHES = {
    "test_auth_environment_integration.py": "RUN_POSTGRES_AUTH_INTEGRATION_TEST",
    "test_ollama_embedding_integration.py": "RUN_OLLAMA_INTEGRATION_TEST",
    "test_document_storage_integration.py": "RUN_S3_INTEGRATION_TEST",
    "test_qdrant_remote_integration.py": "RUN_QDRANT_REMOTE_INTEGRATION_TEST",
    "test_scheduler_postgres_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_task_migration_postgres_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_task_handoff_postgres_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_task_queue_integration.py": "RUN_TASK_QUEUE_INTEGRATION_TEST",
    "test_task_local_integration.py": "RUN_TASK_LOCAL_INTEGRATION_TEST",
    "test_task_cross_storage_integration.py": "RUN_TASK_CROSS_STORAGE_INTEGRATION_TEST",
    "test_knowledge_postgres_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_file_documents_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_processing_postgres_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_document_review_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_document_deletion_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_document_rebuild_integration.py": "RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST",
    "test_knowledge_answer_acceptance.py": "RUN_KNOWLEDGE_ANSWER_ACCEPTANCE_TEST",
    "test_scheduler_retention_integration.py": "RUN_SCHEDULER_QDRANT_INTEGRATION_TEST",
    "test_agent_thread_ownership_integration.py": "RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST",
}


def pytest_addoption(parser):
    parser.addoption(
        "--scheduler-configured-services", action="store_true",
        help="使用当前开发配置的 PostgreSQL/Qdrant 验证定时任务，只写随机测试资源。",
    )


def pytest_configure(config):
    if not config.getoption("--scheduler-configured-services"):
        return
    from agent_lab.config.settings import get_settings
    from agent_lab.config.qdrant import get_qdrant_settings

    try:
        database, qdrant = get_settings(), get_qdrant_settings()
    except Exception as exc:
        raise pytest.UsageError(f"开发服务配置无效：{type(exc).__name__}") from None
    os.environ.update(
        RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST="1",
        RUN_SCHEDULER_QDRANT_INTEGRATION_TEST="1",
        SCHEDULER_TEST_DATABASE_URL=str(database.database_url),
        SCHEDULER_TEST_QDRANT_URL=str(qdrant.base_url),
        SCHEDULER_TEST_QDRANT_API_KEY=qdrant.api_key.get_secret_value(),
    )


@pytest.fixture(autouse=True)
def offline_connections_only(request, monkeypatch):
    switch = INTEGRATION_SWITCHES.get(request.path.name)
    if switch and os.getenv(switch) == "1":
        return

    def blocked(*_args, **_kwargs):
        pytest.fail("离线测试禁止真实连接，请注入数据库或 HTTP 替身。", pytrace=False)

    async def async_blocked(*_args, **_kwargs):
        blocked()

    monkeypatch.setattr(psycopg.Connection, "connect", blocked)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", async_blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_blocked)
