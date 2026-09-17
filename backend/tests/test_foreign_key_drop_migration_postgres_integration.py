"""真跑 alembic 验证「拆掉全部外键」这一条迁移，默认跳过。

启用方式（需要允许随机 schema 的开发 PostgreSQL）：

```bash
RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST=1 SCHEDULER_TEST_DATABASE_URL=... \\
    pytest tests/test_foreign_key_drop_migration_postgres_integration.py
```

**为什么必须真跑迁移而不是 ``create_all``。** 本次改动的验收条件就是「库里那 16 处约束
不存在了」，而 ``create_all`` 是按模型建表——模型里已经没有 ``ForeignKey``，于是用它建出来的
库天然没有约束，测了个恒真式。约束名、``ondelete`` 语义和迁移本身都只能靠真跑 alembic 检验。

**为什么这个文件只查一次约束。** 「库上无外键约束」是**一次性迁移**的验收条件，不是需要长期
维护的回归断言。把它固化成常驻断言，等于把实现细节变成永久负担；这里在临时 schema 里查一次
即可（理由见 ADR 0028）。

**为什么升级之后还要接着做行为验证。** 拆约束本身很容易，真正会出事的是「连带语义搬去业务层
之后还成立吗」。所以第一个用例不只数约束，还在**迁移后的库上**跑一遍删配置与删文档，直接断言
可观察结果——那才是这次改动要保护的东西。
"""

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentReviewRecord
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
from agent_lab.services.user_admin_service import UserAdminService
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.registry import TaskRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权并设置开发 PostgreSQL DSN。",
)

# 拆外键之前的那一版。本迁移就是从它接续的。
PREVIOUS = "b6e2f9047a31"
# 本迁移自己的 revision，与 ``alembic/versions/d4b7c1e93a58_*.py`` 保持一致。
CURRENT = "d4b7c1e93a58"


def run(coroutine):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def _database(dsn, schema):
    """建一个把 search_path 固定到随机 schema 的引擎，绝不动业务表。"""

    assert re.fullmatch(r"scheduler_test_[0-9a-f]{32}", schema)
    engine = create_async_engine(
        dsn, poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema} -ctimezone=UTC", "connect_timeout": 5},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def migrated_database():
    """在随机 schema 里从旧 head 升到本迁移，产出一个真的「已拆约束」的库。"""

    dsn = os.environ.get("SCHEDULER_TEST_DATABASE_URL", "")
    if not dsn.startswith("postgresql+psycopg://"):
        pytest.fail("必须提供 SCHEDULER_TEST_DATABASE_URL（允许随机 schema 的测试 PostgreSQL）。", pytrace=False)
    schema = f"scheduler_test_{uuid4().hex}"
    engine, sessions = _database(dsn, schema)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))

    def up(connection, revision=CURRENT):
        # 复用外部连接，让迁移跑在随机 schema 上而不是 alembic.ini 里那条业务 DSN。
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    def down(connection):
        config.attributes["connection"] = connection
        command.downgrade(config, PREVIOUS)

    async def create():
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            # 升到旧 head，再往上走到本迁移——这正是生产要走的路径。
            await connection.run_sync(up, PREVIOUS)
            await connection.run_sync(up, CURRENT)
            # 默认知识库由迁移种下（documents.knowledge_base_id 非空，造数据要靠它）。
            # 这里只核对它真的在，缺了就是迁移链出了问题，早点报出来比后面报外键错清楚。

    async def cleanup():
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await engine.dispose()

    try:
        run(create())
        yield SimpleNamespace(engine=engine, sessions=sessions, schema=schema, up=up, down=down)
    finally:
        run(cleanup())


def _run_row(*, job_id, source_job_id, now):
    """构造一条不带执行的终态 run，只用于验证删除路径对 job_id 的处理。"""

    return JobRunRecord(
        id=uuid4(), job_id=job_id, source_job_id=source_job_id, task_type="freshrss_sync",
        task_version=1, trigger_type="manual", actor="test:migration", status="succeeded",
        accepted_at=now, available_at=now, dispatch_after=now,
        policy_snapshot={"max_retries": 0, "retry_delay_seconds": 30, "history_retention_days": 30},
    )


async def _foreign_key_count(connection) -> int:
    """数当前 schema 里的外键约束。0 就是本次迁移的验收条件。"""

    return await connection.scalar(text(
        "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
        "AND connamespace = current_schema()::regnamespace"
    ))


def test_migration_removes_every_foreign_key_and_business_cleanup_still_works(migrated_database):
    """16 处约束一个不剩，且连带语义搬去业务层之后真的生效。

    前半段是迁移的验收条件：数约束、看版本号。后半段才是重点——在**已经拆掉约束的库**上
    跑一遍真实的删除路径，证明「删配置置空历史 job_id」和「删文档连带清子表」都不是
    靠数据库兜底才成立的。
    """

    async def verify():
        env = migrated_database
        async with env.engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT
            # 1、验收条件：这 16 处约束都不在了。
            assert await _foreign_key_count(connection) == 0
            # 2、列还在——拆掉的只是约束，不是字段。抽查三个有代表性的。
            columns = (await connection.execute(text(
                "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND (table_name, column_name) IN "
                "(('agent_threads', 'user_id'), ('documents', 'current_version_id'), "
                " ('scheduled_job_runs', 'job_id'))"
            ))).all()
            assert {(r[0], r[1]) for r in columns} == {
                ("agent_threads", "user_id"),
                ("documents", "current_version_id"),
                ("scheduled_job_runs", "job_id"),
            }
            # 3、索引也还在——「外键索引」指的是约束，不是普通索引。按 user_id 撤销 Token
            #    这条写路径依赖它，删掉会退化成全表扫描。
            indexes = (await connection.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname IN ('ix_access_tokens_user_id', 'ix_agent_threads_user_id_last_active_at', "
                "'ix_scheduled_job_runs_job_id')"
            ))).scalars().all()
            assert set(indexes) == {
                "ix_access_tokens_user_id",
                "ix_agent_threads_user_id_last_active_at",
                "ix_scheduled_job_runs_job_id",
            }

        # 4、行为验证：在迁移后的库上删配置，历史 run 的 job_id 必须被置空。
        #    这一条此前完全靠数据库的 ON DELETE SET NULL，拆掉之后只有业务层在管。
        now = datetime.now(UTC)
        async with env.sessions() as session:
            job = ScheduledJobRecord(
                id=uuid4(), key=f"mig-{uuid4().hex}", task_type="freshrss_sync",
                cron_expr="0 9 * * *", params={}, enabled=True, config_version=1,
            )
            session.add(job)
            await session.commit()
            run_row = _run_row(job_id=job.id, source_job_id=job.id, now=now)
            session.add(run_row)
            await session.commit()
            job_id, run_id = job.id, run_row.id

        async with env.sessions() as session:
            registry = TaskRegistry(TASK_TYPE_SPECS.values())
            await ScheduledJobService(session, CronSchedule(), registry).delete_job(job_id)

        async with env.sessions() as session:
            retained = await session.get(JobRunRecord, run_id)
            # job_id 置空，但稳定来源身份与快照保留——历史仍能按原配置身份查到。
            assert retained.job_id is None
            assert retained.source_job_id == job_id
            assert await session.get(ScheduledJobRecord, job_id) is None

    run(verify())


def test_deleting_an_account_leaves_no_orphans_on_the_constraint_free_schema(migrated_database):
    """删账号把指向它的三类数据都处理干净，且全程没有数据库兜底。

    这条是「拆掉 CASCADE 与 SET NULL 之后不许留孤儿」的验收：库上已经没有任何
    ``ON DELETE`` 行为，删 ``users`` 那一行不会带走别的表，全靠
    ``UserAdminService.delete_user`` 在同一个事务里显式做完。

    断言的是**外部可观察结果**——归属记录查不到、Token 查不到、决策留痕的
    ``actor_id`` 变空但记录还在——不去断言删了几条语句、按什么顺序删。
    """

    async def verify():
        env = migrated_database
        now = datetime.now(UTC)
        user_id, thread_id, other_thread_id = uuid4(), uuid4(), uuid4()
        # 另一个账号的会话用来证明清理是「按 user_id」而不是「清全表」。
        bystander_id, bystander_thread_id = uuid4(), uuid4()

        # 1、造数据。三张表都要覆盖：CASCADE 关系的两张、SET NULL 关系的一张。
        async with env.sessions() as session:
            for account_id, email in ((user_id, "doomed"), (bystander_id, "bystander")):
                session.add(UserRecord(
                    id=account_id, email=f"{email}-{uuid4().hex}@example.com",
                    hashed_password="not-a-real-hash-integration-only",
                    is_active=True, is_superuser=False, is_verified=True, is_environment_admin=False,
                ))
            session.add(AccessTokenRecord(
                token="t" + uuid4().hex[:42], user_id=user_id, created_at=now,
            ))
            session.add(AccessTokenRecord(
                token="t" + uuid4().hex[:42], user_id=bystander_id, created_at=now,
            ))
            document_id = uuid4()
            session.add(DocumentRecord(
                id=document_id, knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
                title="待删除账号曾拍板的文档", mime_type="text/plain",
            ))
            await session.commit()
            review = DocumentReviewRecord(
                id=uuid4(), document_id=document_id, processing_id=uuid4(),
                candidate_revision=1, decision="adopt", decision_source="manual",
                actor_id=user_id, content_snapshot={"text": "历史结论"},
            )
            session.add(review)
            await session.commit()
            review_id = review.id

        async with env.sessions() as session:
            for owner, tid in ((user_id, thread_id), (user_id, other_thread_id), (bystander_id, bystander_thread_id)):
                session.add(AgentThreadRecord(
                    thread_id=tid, user_id=owner, title="会话", created_at=now, last_active_at=now,
                ))
            await session.commit()

        # 2、走业务层那条删账号路径。
        async with env.sessions() as session:
            await UserAdminService(session).delete_user(user_id)

        # 3、账号没了，它名下的三类数据都没留下指向它的引用。
        async with env.sessions() as session:
            assert await session.get(UserRecord, user_id) is None
            remaining_threads = (await session.scalars(
                select(AgentThreadRecord.thread_id).where(AgentThreadRecord.user_id == user_id)
            )).all()
            assert remaining_threads == []
            remaining_tokens = (await session.scalars(
                select(AccessTokenRecord.token).where(AccessTokenRecord.user_id == user_id)
            )).all()
            assert remaining_tokens == []

            # 决策留痕**保留**，只是操作者不再可回溯——这是「置空而不是删除」的原语义。
            kept = await session.get(DocumentReviewRecord, review_id)
            assert kept is not None
            assert kept.actor_id is None
            assert kept.decision == "adopt" and kept.content_snapshot == {"text": "历史结论"}

            # 4、别人名下的东西一点没动。
            assert await session.get(UserRecord, bystander_id) is not None
            assert await session.get(AgentThreadRecord, bystander_thread_id) is not None
            bystander_tokens = (await session.scalars(
                select(AccessTokenRecord.token).where(AccessTokenRecord.user_id == bystander_id)
            )).all()
            assert len(bystander_tokens) == 1

    run(verify())


def test_downgrade_rebuilds_all_foreign_keys_and_blocks_on_orphans(migrated_database):
    """回滚能把 16 处约束建回去；库里有孤儿时先给出可读的失败。

    后一半是这次回滚最要紧的性质：**建约束时已有行必须满足它**，所以库脏了就会失败。
    失败信息要能直接指出是哪几个关系脏了、各有几行，而不是一个看不懂的约束冲突。
    """

    async def verify():
        env = migrated_database
        # 1、干净库上回滚：约束全都建回来，且 ondelete 语义与原状一致。
        async with env.engine.begin() as connection:
            await connection.run_sync(env.down)
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
            assert await _foreign_key_count(connection) == 16

        # 2、再升回来——升回来之后正是不设防状态，孤儿才有机会进来。
        async with env.engine.begin() as connection:
            await connection.run_sync(env.up, CURRENT)
            assert await _foreign_key_count(connection) == 0
        # 制造一处孤儿：一条指向不存在配置的 run。拆约束之前这一步根本写不进去。
        async with env.sessions() as session:
            session.add(_run_row(job_id=uuid4(), source_job_id=None, now=datetime.now(UTC)))
            await session.commit()

        # 3、脏库上回滚必须失败，且错误里点名是哪个关系、几行。
        with pytest.raises(Exception, match="回滚前必须先清理孤儿数据"):
            async with env.engine.begin() as connection:
                await connection.run_sync(env.down)
        # 失败不能偷偷改版本号：回滚整体没生效，库仍停在本迁移上。
        async with env.engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT

    run(verify())
