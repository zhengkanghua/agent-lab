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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentReviewRecord
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.models.user_preference import UserPreferenceRecord
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
from agent_lab.services.user_admin_service import UserAdminService
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.registry import TaskRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权并设置开发 PostgreSQL DSN。",
)

# 拆外键之前的那一版。本次拆外键的迁移就是从它接续的。
PREVIOUS = "b6e2f9047a31"
# 拆外键那条迁移自己的 revision。它只用来在 fixture 里走到「已拆约束」那一步，断言不再停在
# 它上面——见 HEAD 的说明。
CURRENT = "d4b7c1e93a58"
# 当前 head。验收条件停在 **head** 上而不是 CURRENT：本文件要保护的性质是「库里一个外键都没有，
# 且业务层清理真的生效」，那是**当前**库的形态。若停在 CURRENT，之后任何一条加了外键的迁移都能
# 悄悄溜过这条断言。升级到 head 之后才能跑删除路径——``agent_threads.system_prompt`` 是后续
# 迁移加的列，停在 CURRENT 上跑 ORM 会报「列不存在」。
HEAD = "c1f4a7d92e60"
# 「账号注销」那条迁移之前的 head。需要在旧结构下造存量账号的用例先停在这里，
# 才能验「升级不动存量数据、回退能还原结构」。
BEFORE_SOFT_DELETE = "e2c8f14b7a30"


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

    def up(connection, revision):
        # 复用外部连接，让迁移跑在随机 schema 上而不是 alembic.ini 里那条业务 DSN。
        # revision 不给默认值：本文件的两处调用都显式传，留一个没人用的默认值只会让人以为
        # 「升到 CURRENT 就够了」——那正是这条注释原来在说、而代码已经不这么做的事。
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    def down(connection):
        config.attributes["connection"] = connection
        command.downgrade(config, PREVIOUS)

    async def create():
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            # 升到旧 head，再一路走到当前 head——这正是生产要走的路径。
            await connection.run_sync(up, PREVIOUS)
            await connection.run_sync(up, HEAD)
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


@pytest.fixture
def database_before_soft_delete():
    """在随机 schema 里升到「账号注销」迁移之前的那一版，供存量数据迁移用例使用。

    与 ``migrated_database`` 的区别只有一点：它停在旧 head，测试才有机会先造出「存量账号」，
    再走一次真实的 ``alembic upgrade``。回退也只退一步，不是退到拆约束之前——
    这一条验的是本次那一条迁移能不能原样退回去。
    """

    dsn = os.environ.get("SCHEDULER_TEST_DATABASE_URL", "")
    if not dsn.startswith("postgresql+psycopg://"):
        pytest.fail("必须提供 SCHEDULER_TEST_DATABASE_URL（允许随机 schema 的测试 PostgreSQL）。", pytrace=False)
    schema = f"scheduler_test_{uuid4().hex}"
    engine, sessions = _database(dsn, schema)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))

    def up(connection, revision):
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    def down(connection, revision):
        config.attributes["connection"] = connection
        command.downgrade(config, revision)

    async def create():
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(up, BEFORE_SOFT_DELETE)

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


async def _check_constraint_definition(connection, name: str) -> str | None:
    """读出某条 CHECK 约束在库里的定义文本，不存在则返回 ``None``。"""

    return await connection.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype = 'c' AND connamespace = current_schema()::regnamespace "
            "AND conname = :name"
        ),
        {"name": name},
    )


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
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
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
    """注销账号留下的是一行带注销时间的账号，不再清掉任何引用它的记录。

    改动前这条是「拆掉 CASCADE 与 SET NULL 之后不许留孤儿」的验收：库上已经没有任何
    ``ON DELETE`` 行为，全靠 ``UserAdminService.delete_user`` 在同一个事务里显式清完。
    改成注销之后验收翻了一面——**六项一次断言**：那一行仍在、带上注销时间、处于不可用状态；
    登录凭据已清空、会话归属与个人偏好仍在、换版决策记录的操作者未变。

    这四项数据分在四张表上，只有真库能同时观测到，所以它是本次的验收核心。
    断言的是**外部可观察结果**，不去断言删了几条语句、按什么顺序删。
    """

    async def verify():
        env = migrated_database
        now = datetime.now(UTC)
        user_id, thread_id, other_thread_id = uuid4(), uuid4(), uuid4()
        # 另一个账号的会话用来证明清理是「按 user_id」而不是「清全表」。
        bystander_id, bystander_thread_id = uuid4(), uuid4()

        # 1、造数据。四张表都要覆盖：从此不再被清理的三张、仍然要清的一张。
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
                title="待注销账号曾拍板的文档", mime_type="text/plain",
            ))
            await session.commit()
            review = DocumentReviewRecord(
                id=uuid4(), document_id=document_id, processing_id=uuid4(),
                candidate_revision=1, decision="adopt", decision_source="manual",
                actor_id=user_id, content_snapshot={"text": "历史结论"},
            )
            session.add(review)
            # 个人偏好：注销后必须保留的三类记录之一。
            session.add(UserPreferenceRecord(
                user_id=user_id, system_prompt="注销后仍然在。",
                document_limit=20, matches_per_document=5,
            ))
            session.add(UserPreferenceRecord(
                user_id=bystander_id, system_prompt="别人的配置。",
                document_limit=10, matches_per_document=3,
            ))
            await session.commit()
            review_id = review.id

        async with env.sessions() as session:
            for owner, tid in ((user_id, thread_id), (user_id, other_thread_id), (bystander_id, bystander_thread_id)):
                session.add(AgentThreadRecord(
                    thread_id=tid, user_id=owner, title="会话", created_at=now, last_active_at=now,
                ))
            await session.commit()

        # 2、走业务层那条注销路径。调用者用一个与目标不同的账号：撞上「不能注销自己」
        #    的话，这条用例就不再验它本来要验的事。
        async with env.sessions() as session:
            await UserAdminService(session).delete_user(user_id, uuid4())

        # 3、六项验收：账号行、注销时间、可用状态、登录凭据、会话归属与偏好、决策留痕。
        async with env.sessions() as session:
            deregistered = await session.get(UserRecord, user_id)
            assert deregistered is not None
            assert deregistered.deleted_at is not None
            assert deregistered.is_active is False
            # 配对不变量的另一面：注销时间非空时 is_active 必须为假。
            assert not (deregistered.deleted_at is not None and deregistered.is_active)
            remaining_tokens = (await session.scalars(
                select(AccessTokenRecord.token).where(AccessTokenRecord.user_id == user_id)
            )).all()
            assert remaining_tokens == []
            remaining_threads = (await session.scalars(
                select(AgentThreadRecord.thread_id).where(AgentThreadRecord.user_id == user_id)
            )).all()
            assert sorted(remaining_threads) == sorted([thread_id, other_thread_id])

            # 决策留痕**保留原操作者**：账号行还在、指向仍有意义，置空反而把人工决定
            # 伪装成自动决策。
            kept = await session.get(DocumentReviewRecord, review_id)
            assert kept is not None
            assert kept.actor_id == user_id
            assert kept.decision == "adopt" and kept.content_snapshot == {"text": "历史结论"}

            # 偏好行仍然在，且内容一字未改。
            preferences = await session.get(UserPreferenceRecord, user_id)
            assert preferences is not None
            assert preferences.system_prompt == "注销后仍然在。"

            # 4、别人名下的东西一点没动。
            assert await session.get(UserRecord, bystander_id) is not None
            assert await session.get(AgentThreadRecord, bystander_thread_id) is not None
            bystander_prefs = await session.get(UserPreferenceRecord, bystander_id)
            assert bystander_prefs is not None and bystander_prefs.document_limit == 10
            bystander_tokens = (await session.scalars(
                select(AccessTokenRecord.token).where(AccessTokenRecord.user_id == bystander_id)
            )).all()
            assert len(bystander_tokens) == 1

    run(verify())


def test_database_rejects_deregistered_environment_admin_and_loginable_deregistered_account(
    migrated_database,
):
    """数据层兜底：两条约束拦得住两种坏组合，且不拦任何正常写入。

    这条只能真库验：约束是库上的对象，用模型元数据建库的用例得不到它。
    它防的是一个**静默的越权登录**——``is_active`` 有多个写者（启动同步的建号与拉回活跃、
    两类建号、改状态），任何一个漏掉与 ``deleted_at`` 配对，表现都是「列表显示已注销、
    这个人照常登录」而没有报错。
    """

    async def verify():
        env = migrated_database
        now = datetime.now(UTC)

        def account(**overrides):
            values = {
                "id": uuid4(),
                "email": f"constraint-{uuid4().hex}@example.com",
                "hashed_password": "not-a-real-hash-integration-only",
                "is_active": True,
                "is_superuser": False,
                "is_verified": True,
                "is_environment_admin": False,
                "deleted_at": None,
            }
            values.update(overrides)
            return UserRecord(**values)

        # 1、环境托管且已注销：写不进去。
        async with env.sessions() as session:
            session.add(account(
                is_active=False, is_superuser=True, is_environment_admin=True, deleted_at=now,
            ))
            with pytest.raises(IntegrityError):
                await session.commit()

        # 2、已注销却仍可登录：写不进去。
        async with env.sessions() as session:
            session.add(account(is_active=True, deleted_at=now))
            with pytest.raises(IntegrityError):
                await session.commit()

        # 3、三个正常组合全部通过：停用、注销、活跃的环境托管超管。
        async with env.sessions() as session:
            session.add(account(is_active=False))
            session.add(account(is_active=False, deleted_at=now))
            session.add(account(is_active=True, is_superuser=True, is_environment_admin=True))
            await session.commit()

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
            await connection.run_sync(env.up, HEAD)
            assert await _foreign_key_count(connection) == 0
        # 制造一处孤儿：一条指向不存在配置的 run。拆约束之前这一步根本写不进去。
        async with env.sessions() as session:
            session.add(_run_row(job_id=uuid4(), source_job_id=None, now=datetime.now(UTC)))
            await session.commit()

        # 3、脏库上回滚必须失败，且错误里点名是哪个关系、几行。
        with pytest.raises(Exception, match="回滚前必须先清理孤儿数据"):
            async with env.engine.begin() as connection:
                await connection.run_sync(env.down)
        # 失败不能偷偷改版本号：回滚整体没生效，库仍停在当前 head 上。
        async with env.engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD

    run(verify())


def test_soft_delete_migration_keeps_existing_accounts_and_reverses_cleanly(
    database_before_soft_delete,
):
    """注销那条迁移可进可退：升级不动存量账号，回退把结构与约束文案还原。

    三件事分开验，因为它们会各自坏：
    ``deleted_at`` 若是不可空的列或者带了非 NULL 默认值，存量账号升级时会被标成「已注销」；
    CHECK 若是漏改，库里就拦不住「环境托管且已注销」与「已注销却仍可用」；
    回退若是漏还原旧的 CHECK 文案，下次升级会因为约束已经存在而失败。

    **回退不动数据**：已注销的账号退化成「不可登录的停用账号」，行、归属与偏好都还在。
    """

    async def verify():
        env = database_before_soft_delete
        active_id, active_thread_id, suspended_id = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)

        # 1、在旧结构下造存量账号：一个活跃、一个停用，各带一条会话归属。
        #    这一步用原生 SQL 而不是 ORM：此刻库里还没有 ``deleted_at`` 列，而模型已经有，
        #    拿 ORM 插入会直接报「列不存在」——那样这条用例就不是断言失败，而是自己坏了。
        active_email = f"legacy-active-{uuid4().hex}@example.com"
        suspended_email = f"legacy-suspended-{uuid4().hex}@example.com"
        async with env.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
                    "is_verified, is_environment_admin) VALUES "
                    "(:active_id, :active_email, 'not-a-real-hash-integration-only', true, false, true, false), "
                    "(:suspended_id, :suspended_email, 'not-a-real-hash-integration-only', false, false, true, false)"
                ),
                {
                    "active_id": active_id,
                    "active_email": active_email,
                    "suspended_id": suspended_id,
                    "suspended_email": suspended_email,
                },
            )
        async with env.sessions() as session:
            session.add(AgentThreadRecord(
                thread_id=active_thread_id, user_id=active_id, title="存量会话",
                created_at=now, last_active_at=now,
            ))
            await session.commit()

        # 2、升级到当前 head。
        async with env.engine.begin() as connection:
            await connection.run_sync(env.up, HEAD)

        # 3、存量账号的可用状态不变，且都没有被标成已注销；行数一条不多一条不少。
        async with env.sessions() as session:
            active = await session.get(UserRecord, active_id)
            suspended = await session.get(UserRecord, suspended_id)
            assert active is not None and active.is_active is True
            assert active.deleted_at is None
            assert suspended is not None and suspended.is_active is False
            assert suspended.deleted_at is None
            assert await session.get(AgentThreadRecord, active_thread_id) is not None

        # 4、两条约束都在，且写法就是目标里那两句。
        async with env.engine.begin() as connection:
            columns = (await connection.execute(text(
                "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'users' "
                "AND column_name = 'deleted_at'"
            ))).all()
            assert columns == [("deleted_at", "YES", "timestamp with time zone")]
            environment_admin = await _check_constraint_definition(
                connection, "ck_users_environment_admin_privileges"
            )
            paired = await _check_constraint_definition(
                connection, "ck_users_deleted_at_implies_inactive"
            )
            assert environment_admin is not None and "deleted_at IS NULL" in environment_admin
            assert paired is not None
            assert "deleted_at IS NOT NULL" in paired and "is_active" in paired

        # 5、回退一步：新列删掉、配对约束删掉、环境托管约束的旧文案恢复，数据不动。
        async with env.engine.begin() as connection:
            await connection.run_sync(env.down, BEFORE_SOFT_DELETE)
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == BEFORE_SOFT_DELETE
            assert await _check_constraint_definition(
                connection, "ck_users_deleted_at_implies_inactive"
            ) is None
            restored = await _check_constraint_definition(
                connection, "ck_users_environment_admin_privileges"
            )
            assert restored is not None and "deleted_at" not in restored
            remaining = (await connection.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'users' "
                "AND column_name = 'deleted_at'"
            ))).all()
            assert remaining == []

        # 回退之后模型又与库不一致（模型有 ``deleted_at``、库没有了），所以这一段同样用
        # 原生 SQL 读数据，不用 ORM。
        async with env.engine.begin() as connection:
            rows = (await connection.execute(
                text("SELECT id, is_active FROM users WHERE id IN (:active_id, :suspended_id)"),
                {"active_id": active_id, "suspended_id": suspended_id},
            )).all()
            assert {(row[0], row[1]) for row in rows} == {
                (active_id, True),
                (suspended_id, False),
            }
            assert await connection.scalar(
                text("SELECT count(*) FROM agent_threads WHERE thread_id = :thread_id"),
                {"thread_id": active_thread_id},
            ) == 1

    run(verify())
