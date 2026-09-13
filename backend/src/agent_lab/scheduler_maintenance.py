"""人工核实后的恢复入口，默认只查看；不重做业务，不删除 Document。

只有操作者已停止所属进程、确认远端未决写操作结束后，才可使用 --confirm-stopped。
心跳过期自身不是安全恢复的证据。本命令不会自动控制任何进程或远端服务。
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import sys
from uuid import UUID

from sqlalchemy import delete, select, text

from agent_lab.models.scheduled_job import JobRunRecord
from agent_lab.models.write_operation import WriteOperationRecord
from agent_lab.services.write_coordination import WRITE_MUTEX
from agent_lab.tasks.contracts import ACTIVE_STATUSES, ExecutionPolicy


async def inspect_or_recover(session_factory, *, run_id=None, operation_id=None, confirm_stopped=False):
    async with session_factory() as session:
        if not confirm_stopped:
            operations = list((await session.scalars(select(WriteOperationRecord))).all())
            runs = list((await session.scalars(select(JobRunRecord).where(
                JobRunRecord.status.in_(ACTIVE_STATUSES),
            ))).all())
            return {
                "operations": [{"id": str(item.id), "run_id": str(item.run_id) if item.run_id else None, "owner": item.owner, "status": item.status, "resources": item.resources, "heartbeat_at": item.heartbeat_at.isoformat()} for item in operations],
                "runs": [{"id": str(item.id), "job_id": str(item.source_job_id) if item.source_job_id else None,
                          "status": item.status, "owner": item.owner, "wait_reason": item.wait_reason,
                          "heartbeat_at": item.heartbeat_at.isoformat() if item.heartbeat_at else None} for item in runs],
            }
        if (run_id is None) == (operation_id is None):
            raise ValueError("恢复必须指定一个 run-id 或 operation-id。")
        operation = await session.get(WriteOperationRecord, operation_id) if operation_id else None
        if operation_id and operation is None:
            raise ValueError("写操作不存在。")
        run_id = run_id or (operation.run_id if operation else None)
        run = await session.get(JobRunRecord, run_id) if run_id else None
        if run_id and run is None:
            raise ValueError("任务执行不存在。")
        if run:
            await session.refresh(run, with_for_update=True)
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_MUTEX})
        criterion = WriteOperationRecord.run_id == run_id if run_id else WriteOperationRecord.id == operation_id
        operations = list((await session.scalars(select(WriteOperationRecord).where(criterion).with_for_update())).all())
        recent = datetime.now(UTC) - timedelta(seconds=30)
        if any(item.heartbeat_at >= recent for item in operations) or (run and run.status in ACTIVE_STATUSES and run.heartbeat_at and run.heartbeat_at >= recent):
            raise ValueError("执行者近期仍有心跳，拒绝恢复。")
        if run and run.status in ACTIVE_STATUSES:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.expires_at = run.finished_at + timedelta(days=ExecutionPolicy.model_validate(run.policy_snapshot).history_retention_days)
            run.error_type = "OwnerConfirmedStopped"
            run.stats = {**run.stats, "error_reason": "owner_confirmed_stopped", "business_outcome": "unknown"}
        if run:
            run.claim_token = None
            run.delivery_generation += 1
            run.wait_reason = None
            run.recovery = {"confirmed_stopped_at": datetime.now(UTC).isoformat(), "business_outcome": "unknown"}
            run.stats = {**run.stats, "needs_attention": False, "recovery_confirmed_at": datetime.now(UTC).isoformat()}
        await session.execute(delete(WriteOperationRecord).where(criterion))
        await session.commit()
        return {"recovered": True, "run_id": str(run_id) if run_id else None, "released_operations": len(operations)}


async def main(args):
    from agent_lab.db.session import async_session_factory, engine
    try:
        result = await inspect_or_recover(async_session_factory, run_id=args.run_id, operation_id=args.operation_id, confirm_stopped=args.confirm_stopped)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", type=UUID)
    group.add_argument("--operation-id", type=UUID)
    parser.add_argument("--confirm-stopped", action="store_true")
    args = parser.parse_args()
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(main(args))
        else:
            asyncio.run(main(args))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        sys.exit(1)
