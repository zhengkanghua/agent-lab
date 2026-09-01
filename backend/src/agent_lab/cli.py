"""提供内部账号创建、新闻同步与向量索引的一次性命令行入口。

本模块位于进程 composition root 层，解析新闻 Pipeline 命令和交互式 ``create-user``，
按命令构造最小依赖并输出机器可读摘要。它不提供公开注册、HTTP/WebSocket、定时调度、
常驻 Worker 或无限循环；搜索继续由独立 FastAPI 只读接口负责。
"""

import argparse
import asyncio
from getpass import getpass
import json
import logging
from secrets import compare_digest
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from agent_lab.agent.checkpointer import (
    CHECKPOINTER_TABLE_NAMES,
    delete_checkpointer_threads,
    list_checkpointer_thread_ids,
    setup_checkpointer_tables,
)
from agent_lab.auth.manager import UserManager
from agent_lab.config.freshrss import get_freshrss_settings
from agent_lab.config.ollama_embedding import (
    get_ollama_embedding_settings,
)
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.config.settings import get_settings
from agent_lab.db.session import async_session_factory, engine
from agent_lab.models.user import UserRecord
from agent_lab.pipeline.limits import (
    DEFAULT_INDEX_BATCH_SIZE,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_STALE_AFTER_MINUTES,
    MAX_INDEX_BATCH_SIZE,
    MAX_LIMIT_PER_SOURCE,
    MAX_STALE_AFTER_MINUTES,
)
from agent_lab.qdrant.runtime import DocumentIndexingRuntime
from agent_lab.schemas.auth import AuthUserCreate
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.services.freshrss_import_service import FreshRSSImportService
from agent_lab.services.news_pipeline_execution_service import (
    NewsPipelineExecutionService,
    NewsSyncExecutionResult,
    PendingIndexExecutionResult,
)


logger = logging.getLogger(__name__)


class PasswordConfirmationError(ValueError):
    """交互式创建账号时两次密码输入不一致。"""


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """封装一次 CLI 的安全 JSON 字段和进程退出码。"""

    payload: dict[str, Any]
    exit_code: int


def _bounded_integer(
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> Callable[[str], int]:
    """创建带清晰 argparse 错误的有界整数解析器。

    做成闭包工厂是因为 argparse 的 ``type=`` 只接受「单参数 callable」，没法把上下界
    传进去。闭包把 name/minimum/maximum 记住，交给 argparse 的就只剩一个 ``parse``。

    抛 ``ArgumentTypeError`` 而不是 ``ValueError``：前者会被 argparse 接住，变成
    「usage: ... error: ...」加退出码 2；后者会一路冒到 ``main`` 变成堆栈。

    Args:
        name: 出错信息里显示的参数名。
        minimum: 允许的最小值（含）。
        maximum: 允许的最大值（含）。

    Returns:
        可直接交给 ``add_argument(type=...)`` 的解析函数。
    """

    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} 必须是整数") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} 必须介于 {minimum} 和 {maximum} 之间"
            )
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    """构造 Pipeline 与账号管理 CLI 参数树，不读取配置或执行外部 I/O。

    Returns:
        包含三个 Pipeline 子命令和一个交互式建号子命令的 ``ArgumentParser``。

    Notes:
        本方法只创建进程内解析对象，不执行 PostgreSQL、FreshRSS、Embedding 或 Qdrant
        I/O，也不写外部数据。
    """

    parser = argparse.ArgumentParser(
        prog="agent-lab",
        description="一次性同步 FreshRSS 新闻并处理 PostgreSQL 待索引任务。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_user_parser = subparsers.add_parser(
        "create-user",
        help="交互式创建一个不可公开注册的内部登录账号。",
    )
    create_user_parser.add_argument(
        "--email",
        required=True,
        help="账号登录邮箱；密码会在终端中隐藏输入。",
    )
    create_user_parser.add_argument(
        "--superuser",
        action="store_true",
        help="允许该账号执行 POST /pipeline/run-once。",
    )

    sync_parser = subparsers.add_parser(
        "sync-news",
        help="从每个白名单 FreshRSS 来源同步最近新闻到 PostgreSQL。",
    )
    _add_sync_arguments(sync_parser)

    index_parser = subparsers.add_parser(
        "index-pending",
        help="准备 Qdrant current Alias 并处理一批 pending/failed 新闻。",
    )
    _add_index_arguments(index_parser)

    run_parser = subparsers.add_parser(
        "run-once",
        help="先同步新闻，再准备 Qdrant 并处理一个有界索引批次。",
    )
    _add_sync_arguments(run_parser)
    _add_index_arguments(run_parser)

    subparsers.add_parser(
        "init-checkpointer",
        help="创建或升级 Agent 会话历史所需的 LangGraph checkpointer 表。",
    )

    prune_parser = subparsers.add_parser(
        "prune-orphan-threads",
        help="清理没有归属记录的 Agent 会话历史；默认只预演，加 --yes 才真删。",
    )
    prune_parser.add_argument(
        "--yes",
        action="store_true",
        # 默认预演是有意的：这个命令删的是用户的对话历史，且不可恢复。让「看一眼」成为默认
        # 动作、「真删」成为显式选择。
        help="确认执行删除；省略时只列出将被删除的会话数量，不做任何修改。",
    )
    return parser


def _add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    """向命令添加每来源同步数量参数。"""

    parser.add_argument(
        "--limit-per-source",
        type=_bounded_integer(
            "limit-per-source",
            minimum=1,
            maximum=MAX_LIMIT_PER_SOURCE,
        ),
        default=DEFAULT_LIMIT_PER_SOURCE,
        help="每个白名单来源最多同步的新闻数，默认 2，最大 100。",
    )


def _add_index_arguments(parser: argparse.ArgumentParser) -> None:
    """向命令添加索引批量与 stale lease 参数。"""

    parser.add_argument(
        "--batch-size",
        type=_bounded_integer(
            "batch-size",
            minimum=1,
            maximum=MAX_INDEX_BATCH_SIZE,
        ),
        default=DEFAULT_INDEX_BATCH_SIZE,
        help="本次最多处理的 pending/failed 文档数，默认 20，最大 1000。",
    )
    parser.add_argument(
        "--stale-after-minutes",
        type=_bounded_integer(
            "stale-after-minutes",
            minimum=1,
            maximum=MAX_STALE_AFTER_MINUTES,
        ),
        default=DEFAULT_STALE_AFTER_MINUTES,
        help="processing 超过多少分钟后重新排队，默认 60，最大 10080。",
    )


async def dispatch_command(args: argparse.Namespace) -> CommandOutcome:
    """按已校验参数执行一个命令，并返回安全摘要。

    Args:
        args: ``build_parser`` 生成并校验的子命令 Namespace。

    Returns:
        不含正文、Vector、密码、Token 或异常文本的 JSON payload 与退出码；索引批次
        存在单篇失败时返回退出码 1，其余成功返回 0。

    Raises:
        Exception: 配置、FreshRSS、PostgreSQL、Ollama、Qdrant lifecycle 或批次级操作
            失败；最外层会仅按异常类型安全报告并返回非零退出码。

    Notes:
        ``create-user`` 只交互读取密码并写 PostgreSQL 用户表；``sync-news`` 不接触
        Qdrant；索引命令会显式准备 Collection/current Alias。所有命令都是一次性执行，
        不进行 Vector Search 或自动调度。
    """

    if args.command == "create-user":
        return await _create_user(args)

    if args.command == "init-checkpointer":
        return await _init_checkpointer(args)

    if args.command == "prune-orphan-threads":
        return await _prune_orphan_threads(args)

    executor = NewsPipelineExecutionService(async_session_factory)
    # 命令分派：sync-news 只同步；index-pending 只索引；run-once 两步都做
    if args.command == "sync-news":
        # 1、只做 FreshRSS → PostgreSQL，不接触 Qdrant
        sync_result = await executor.sync_news(
            FreshRSSImportService(get_freshrss_settings()),
            limit_per_source=args.limit_per_source,
        )
        return _sync_outcome(args.command, sync_result)

    if args.command == "index-pending":
        index_result = await _execute_index_batch(executor, args)
        return _index_outcome(args.command, index_result)

    if args.command == "run-once":
        # 1、先同步：FreshRSS → PostgreSQL
        sync_result = await executor.sync_news(
            FreshRSSImportService(get_freshrss_settings()),
            limit_per_source=args.limit_per_source,
        )
        # 2、再准备 Alias 并索引。个别来源同步失败不影响这一步——那类失败被隔进
        #    sync_result 里不会抛出，而上一轮可能还留着没索引的文档，值得一并处理掉。
        #    批次级失败（订阅列表读不到等）会直接抛出，走不到这里。
        index_result = await _execute_index_batch(executor, args)
        # 3、合并两个子结果，任一部分失败整个命令就 ok=false
        index_outcome = _index_outcome(args.command, index_result)
        sync_outcome = _sync_outcome(args.command, sync_result)
        ok = sync_outcome.exit_code == 0 and index_outcome.exit_code == 0
        return CommandOutcome(
            payload={
                **index_outcome.payload,
                **{
                    key: value
                    for key, value in sync_outcome.payload.items()
                    if key not in {"command", "ok"}
                },
                "ok": ok,
            },
            exit_code=0 if ok else 1,
        )

    raise ValueError(f"不支持的命令：{args.command!r}")


async def _create_user(args: argparse.Namespace) -> CommandOutcome:
    """隐藏输入并创建一个已确认的内部账号。

    Args:
        args: 包含 ``email`` 与 ``superuser`` 的已解析 CLI 参数。

    Returns:
        不含密码 Hash 或 Token 的用户 ID、邮箱、管理员标记和成功退出码。

    Raises:
        PasswordConfirmationError: 两次密码输入不一致。
        InvalidPasswordException: 密码不满足 UserManager 的最低强度规则。
        UserAlreadyExists: 邮箱已被现有账号使用。

    Notes:
        会在终端同步读取两次隐藏密码并执行一次 PostgreSQL 写事务；不访问 FreshRSS、
        Ollama 或 Qdrant。明文密码只保存在当前函数局部变量中，不进入参数或日志。
    """

    # 1、隐藏输入读两遍。用 compare_digest 而不是 ==：它是定时比较，不会因为
    #    「前几个字符就不一样」而提前返回。这里的两个值都来自本机终端、泄漏面很小，
    #    用它主要是别在代码里留下「明文密码用 == 比」的示范。
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if not compare_digest(password, confirmation):
        raise PasswordConfirmationError()

    # 2、走 UserManager 而不是直接插表：密码强度校验和 Hash 都在它里面，绕过去就等于
    #    CLI 建的号和接口建的号规则不一样。
    async with async_session_factory() as session:
        user_database = SQLAlchemyUserDatabase(session, UserRecord)
        manager = UserManager(user_database)
        user = await manager.create(
            AuthUserCreate(
                email=args.email,
                password=password,
                is_active=True,
                is_superuser=args.superuser,
                is_verified=True,
            )
        )

    # 3、只回 id/邮箱/管理员标记。密码 Hash 不进 payload——这份 payload 会被 print 到
    #    stdout，可能进 Scheduler 日志。
    return CommandOutcome(
        payload={
            "command": args.command,
            "ok": True,
            "user_id": str(user.id),
            "email": user.email,
            "is_superuser": user.is_superuser,
        },
        exit_code=0,
    )


async def _init_checkpointer(args: argparse.Namespace) -> CommandOutcome:
    """创建或升级 LangGraph checkpointer 的四张表。

    Args:
        args: 只用到 ``command``，本命令没有自己的参数。

    Returns:
        建表成功的摘要与退出码 0。

    Raises:
        Exception: 无法连接 PostgreSQL 或没有建表权限时传播；最外层只按异常类型报告。

    Notes:
        这是**数据库结构写入**：``setup()`` 会 ``CREATE TABLE IF NOT EXISTS`` 那四张表，
        并按 LangGraph 自己的迁移记录补齐缺失的版本。它不写业务表、不动 Qdrant，也不删除
        任何已有会话历史。

        为什么必须是一条显式命令、而不是服务启动时自动建：
        应用进程通常只该有业务表的读写权限，建表是运维动作。放进启动路径意味着每次重启都
        带着 DDL 权限，且一旦 LangGraph 升级了表结构，重启会静默改库——那种变更应当由人
        在知情的情况下触发。理由见 docs/adr/0004-checkpointer-tables-outside-alembic.md。

        幂等：重复执行安全，第二次不会报错也不会重建已存在的表。
    """

    await setup_checkpointer_tables(str(get_settings().database_url))
    return CommandOutcome(
        payload={
            "command": args.command,
            "ok": True,
            "checkpointer_tables": sorted(CHECKPOINTER_TABLE_NAMES),
        },
        exit_code=0,
    )


async def _prune_orphan_threads(args: argparse.Namespace) -> CommandOutcome:
    """清理 checkpointer 里没有归属记录的会话历史。

    Args:
        args: 用到 ``command`` 与 ``yes``；``yes`` 为假时只预演不删。

    Returns:
        孤儿会话数量与本次实际删除数量，退出码 0。

    Raises:
        Exception: 无法连接 PostgreSQL、checkpointer 表不存在、或删除失败时传播；
            最外层只按异常类型报告。

    Notes:
        「孤儿」的定义是：checkpointer 里存有历史，但 ``agent_threads`` 里查不到归属记录。
        这类数据的来源有三种——归属功能上线之前产生的历史、迁移被回滚过、以及删除会话时
        「清历史成功、删归属记录失败」留下的残余。它们在网页上既列不出来也删不掉。

        **默认只预演。** 加 ``--yes`` 才真删，且删除不可恢复。

        为什么不在 Alembic 迁移里做这件事：迁移文件按约定是「写下来就不再变」的历史记录，
        而这里要调第三方库的删除逻辑，那段逻辑自己带版本管理——回滚到某个旧迁移时它的行为
        已经不是当初那个了。理由同 ADR 0004 拒绝「在迁移里建 checkpointer 表」。

        顺序上它必须在 ``alembic upgrade head`` 之后跑：``agent_threads`` 还不存在的话，
        全部会话都会被判成孤儿。
    """

    database_url = str(get_settings().database_url)

    # 1、先读业务表的归属记录，再读 checkpointer。顺序无所谓正确性，但先读业务表更快失败——
    #    表不存在时立刻报错，而不是先花时间遍历完所有 checkpoint。
    threads = AgentThreadService(async_session_factory)
    owned = await threads.list_known_thread_ids()
    # 两侧都转成字符串再比：业务表存的是 UUID 对象，checkpointer 存的是字符串。万一库里被手工
    # 塞进过非 UUID 的 thread_id，按字符串比会把它算成孤儿并清掉，这也正是想要的结果——
    # 若先 UUID() 解析，那种值会让整个命令抛异常。
    known = {str(thread_id) for thread_id in owned}
    stored = await list_checkpointer_thread_ids(database_url)
    orphans = sorted(stored - known)

    # 2、预演模式只报数，不动数据。
    if not args.yes:
        return CommandOutcome(
            payload={
                "command": args.command,
                "ok": True,
                "dry_run": True,
                "orphan_threads": len(orphans),
                "deleted_threads": 0,
            },
            exit_code=0,
        )

    deleted = await delete_checkpointer_threads(database_url, orphans)
    return CommandOutcome(
        payload={
            "command": args.command,
            "ok": True,
            "dry_run": False,
            "orphan_threads": len(orphans),
            "deleted_threads": deleted,
        },
        exit_code=0,
    )


async def _execute_index_batch(
    executor: NewsPipelineExecutionService,
    args: argparse.Namespace,
) -> PendingIndexExecutionResult:
    """组装写入 Runtime，显式准备 Alias 并确保任何路径都关闭 client。

    Args:
        executor: 持有 Session 工厂的批次执行 Service。
        args: 提供 ``batch_size`` 与 ``stale_after_minutes`` 的已解析参数。

    Returns:
        本批次的候选、成功、跳过和失败统计。

    Raises:
        Exception: Qdrant lifecycle、Embedding 或 PostgreSQL 的批次级失败。

    Notes:
        执行 Qdrant 写入（Collection/Alias 准备加 Point 写入）、Ollama Embedding 和
        PostgreSQL 读写。无论成败都会关闭 Qdrant client。
    """

    # 1、每次调用建一个新 Runtime。它持有 Qdrant client 和 Embedding client，是「这一批
    #    专用」的资源，用完就关，不做进程级复用。
    runtime = DocumentIndexingRuntime.build(
        get_qdrant_settings(),
        get_ollama_embedding_settings(),
    )
    operation_error: BaseException | None = None
    try:
        # 2、先 ensure_ready 再索引：Collection 和 current Alias 必须在写 Point 之前就位。
        await runtime.ensure_ready()
        return await executor.index_pending(
            runtime.service,
            batch_size=args.batch_size,
            stale_after=timedelta(minutes=args.stale_after_minutes),
        )
    # 3、把主异常记下来再原样抛出。记它只为了给下面的 finally 一个判断依据：
    #    「主流程是成功的还是失败的」。用 BaseException 是为了连 CancelledError 也算上。
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        # 4、关闭一定要执行，但关闭本身也可能失败，于是分两种情况：
        #    主流程成功 → 关闭失败就是唯一的失败，正常抛出去。
        #    主流程已经失败 → 关闭失败挂成 note 附在主异常上。直接 raise 会把主异常
        #    顶掉，那才是真正要查的那个。
        try:
            await runtime.close()
        except Exception as close_error:
            if operation_error is None:
                raise
            operation_error.add_note(
                "此外关闭索引运行时也失败："
                f"{type(close_error).__name__}。"
            )


def _index_outcome(
    command: str,
    result: PendingIndexExecutionResult,
) -> CommandOutcome:
    """把索引结果转换成不含单篇异常文本的稳定命令摘要。

    ``failures`` 里每项只放 document_id 和异常类名，不放 ``str(exc)``——那里面可能有
    Qdrant 返回的原始报文或文档正文片段。

    Args:
        command: 当前 CLI 子命令名称。
        result: Execution Service 返回的批次统计。

    Returns:
        全部成功时退出码为零；有单篇失败时为一，但成功的那些已经写进 Qdrant 不会回滚。
    """

    failed_count = result.failed_count
    return CommandOutcome(
        payload={
            "command": command,
            "ok": failed_count == 0,
            "requeued_stale_documents": result.requeued_stale_count,
            "index_candidates": result.candidate_count,
            "indexed_documents": result.indexed_count,
            "skipped_documents": result.skipped_count,
            "failed_documents": failed_count,
            "failures": [
                {
                    "document_id": str(item.document_id),
                    "error_type": item.error_type,
                }
                for item in result.failures
            ],
        },
        exit_code=0 if failed_count == 0 else 1,
    )


def _sync_outcome(
    command: str,
    result: NewsSyncExecutionResult,
) -> CommandOutcome:
    """把增量同步结果转换成不含异常文本或正文的 CLI 摘要。

    Args:
        command: 当前 CLI 子命令名称。
        result: Execution Service 返回的来源级同步统计。

    Returns:
        没有来源失败时退出码为零；存在隔离失败时为一，但已成功来源不会回滚。
    """

    failed_count = result.failed_source_count
    return CommandOutcome(
        payload={
            "command": command,
            "ok": failed_count == 0,
            "sync_sources": result.source_count,
            "successful_sync_sources": result.successful_source_count,
            "failed_sync_sources": failed_count,
            "advanced_sync_checkpoints": result.checkpoint_advanced_count,
            "synchronized_documents": result.synchronized_count,
            "sync_failures": [
                {
                    "source_external_id": item.source_external_id,
                    "error_type": item.error_type,
                }
                for item in result.failures
            ],
        },
        exit_code=0 if failed_count == 0 else 1,
    )


async def _run_with_cleanup(args: argparse.Namespace) -> CommandOutcome:
    """执行命令并在结束时释放全局 SQLAlchemy Engine 连接池。

    为什么必须显式 dispose：``engine`` 是模块级全局对象，进程退出时不保证连接池里的
    连接被优雅归还。CLI 是一次性进程，跑完就走，留着的连接会在 PostgreSQL 侧挂一会儿。

    ``add_note`` 那套和 ``_execute_index_batch`` 的 finally 是同一个模式，理由见那里。

    Args:
        args: 已解析的 CLI 参数。

    Returns:
        ``dispatch_command`` 的原样结果。

    Raises:
        Exception: 命令自身的失败原样传播；连接池释放失败只在命令成功时才抛。
    """

    operation_error: BaseException | None = None
    try:
        return await dispatch_command(args)
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        try:
            await engine.dispose()
        except Exception as dispose_error:
            if operation_error is None:
                raise
            operation_error.add_note(
                "此外释放 SQLAlchemy Engine 也失败："
                f"{type(dispose_error).__name__}。"
            )


def main(argv: Sequence[str] | None = None) -> int:
    """解析参数并同步返回适合 Scheduler/终端判断的退出码。

    Args:
        argv: 可选参数序列；``None`` 时读取当前进程 ``sys.argv``。

    Returns:
        成功为 0；单篇索引失败或命令级异常为 1。argparse 参数错误使用标准退出码 2。

    Notes:
        根据平台创建事件循环；Windows 使用 Psycopg 异步驱动要求的 Selector loop。
        ``create-user`` 会在执行协程中隐藏读取两次密码。命令级错误只输出 Python 异常
        类型，不输出可能包含凭据、密码或远程正文的异常文本。
    """

    # 1、先解析参数。参数不合法时 argparse 自己打 usage 并退出（码 2），下面的代码不会跑到。
    parser = build_parser()
    args = parser.parse_args(argv)
    # 2、日志根级别压到 WARNING，只把本项目提到 INFO。httpx 等第三方库的 INFO 可能包含
    #    完整请求 URL，没有必要进入 Scheduler 输出，即使当前 URL 不含认证 header。
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("agent_lab").setLevel(logging.INFO)
    # 3、Windows 上用 Selector 事件循环（Psycopg 异步驱动要求），其余平台用默认 asyncio 实现
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    # 4、同步入口在这里跨进异步世界：整个命令跑在这一个 asyncio.run 里面。
    try:
        outcome = asyncio.run(_run_with_cleanup(args), loop_factory=loop_factory)
    except Exception as exc:
        # 5、命令级异常：只输出异常类型，不带异常文本——文本里可能有连接串、凭据或远程
        #    正文。写 stderr、退出码 1，让 Scheduler 能和正常输出分开。
        error_payload = {
            "command": args.command,
            "ok": False,
            "error_type": type(exc).__name__,
        }
        print(json.dumps(error_payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    # 6、成功路径：payload 打到 stdout。sort_keys 让字段顺序稳定，方便 diff 两次运行的输出。
    print(json.dumps(outcome.payload, ensure_ascii=False, sort_keys=True))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
