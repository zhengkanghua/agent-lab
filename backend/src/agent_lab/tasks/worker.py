"""队列消息只有编号与代次；先数据库领取，再由普通业务函数处理可信快照。"""

import asyncio
import logging

from agent_lab.services.execution_cleanup import finish_cleanup
from agent_lab.tasks.context import TaskClaim, current_claim, current_run_id
from agent_lab.tasks.contracts import ExecutionPolicy, FailureDecision, RecoveryDecision, ResourceWait

logger = logging.getLogger(__name__)


class TaskWorker:
    def __init__(self, store, registry, *, owner, heartbeat_seconds=5):
        self.store, self.registry, self.owner = store, registry, owner
        self.heartbeat_seconds = heartbeat_seconds

    async def execute(self, run_id, generation):
        run = await self.store.claim(run_id, generation, self.owner)
        if run is None:
            return
        token = current_run_id.set(run.id)
        claim = TaskClaim(run.id, run.claim_token)
        claim_context = current_claim.set(claim)
        heartbeat = asyncio.create_task(self._heartbeat(run, asyncio.current_task()))
        runtime, spec, started = None, None, False
        result = None
        try:
            spec = self.registry.require(run.task_type, run.task_version)
            params = spec.params_model.model_validate(run.config_snapshot["params"]).model_dump()
            runtime = spec.runtime_factory()
            async with spec.execution_scope(runtime, params):
                # 取消与开始业务竞争同一个数据库状态；资源准备期间仍允许取消。
                started = await self.store.start(run.id, run.claim_token)
                if not started:
                    return
                claim.started = True
                stats = await spec.execute(runtime, params)
                if not isinstance(stats, dict):
                    raise TypeError("任务结果必须是脱敏统计对象。")
                result = {"status": "succeeded", "stats": stats, "on_success": spec.on_success}
        except ResourceWait as error:
            if started:
                result = {"status": "needs_attention", "stats": {},
                          "wait_reason": "业务开始后返回资源等待，需核实本次业务结果。",
                          "recovery": {"reason": "resource_wait_after_start"}}
            else:
                result = {"status": "waiting_resource", "stats": {}, "wait_reason": error.reason,
                          "delay_seconds": error.check_after_seconds}
        except asyncio.CancelledError:
            result = ({"status": "needs_attention", "stats": {}, "error_type": "CancelledError",
                       "wait_reason": "执行中断，需核实执行者与远端写入后使用维护入口。",
                       "recovery": {"reason": "execution_interrupted"}}
                      if started else {"status": "queued", "stats": {}, "delay_seconds": 5})
        except Exception as error:
            decision = spec.classify_error(error) if spec else FailureDecision()
            policy = ExecutionPolicy.model_validate(run.policy_snapshot)
            attempts = run.attempts + int(started)
            status = "needs_attention" if decision.needs_attention else "failed"
            if decision.retryable and not decision.needs_attention and started and attempts <= policy.max_retries:
                status = "retry_wait"
            result = {"status": status, "stats": {**decision.stats, "error_reason": decision.reason},
                      "error_type": type(error).__name__, "wait_reason": decision.detail,
                      "delay_seconds": policy.delay(attempts)}
        finally:
            try:
                await finish_cleanup(self._finish(run, runtime, heartbeat, result))
            finally:
                current_claim.reset(claim_context)
                current_run_id.reset(token)

    async def _finish(self, run, runtime, heartbeat, result):
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if runtime is not None and callable(getattr(runtime, "close", None)):
            try:
                await runtime.close()
            except Exception as error:
                if result is not None:
                    result["stats"]["resource_close_error"] = type(error).__name__
        if result is None:
            return
        # 提交结果不明时只重试条件收尾；不会重进业务函数。
        for _ in range(2):
            try:
                confirmed = await self.store.finish(run.id, run.claim_token, **result)
                if confirmed:
                    logger.info("任务执行收尾 run_id=%s status=%s", run.id, result["status"])
                else:
                    logger.info("领取已失效，丢弃迟到结果 run_id=%s", run.id)
                return
            except Exception as error:
                logger.error("任务终态未确认 run_id=%s error_type=%s", run.id, type(error).__name__)

    async def _heartbeat(self, run, execution):
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                if not await self.store.heartbeat(run.id, run.claim_token):
                    execution.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("任务心跳失败 run_id=%s error_type=%s", run.id, type(error).__name__)
            execution.cancel()

    async def recover_stalled(self):
        for run in await self.store.stale_claims():
            decision = RecoveryDecision()
            spec = None
            try:
                spec = self.registry.require(run.task_type, run.task_version)
                if spec.recover:
                    decision = await spec.recover(run)
            except Exception as error:
                logger.error("任务恢复核对失败 run_id=%s error_type=%s", run.id, type(error).__name__)
            await self.store.recover_claim(run, decision,
                discard_preparation=spec.discard_preparation if spec else None,
                on_success=spec.on_success if spec else None)
