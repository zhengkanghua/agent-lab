# 03: 已注销账号上的动作有明确结局

**要交付什么：** 对一个已注销的账号再点注销不会报错；点停用或改密码会得到一句说明「它已注销」的明确提示，而不是「稍后重试」或「账号不存在」。

**被谁阻塞：** 01（注销取代删除）

**状态：** 已完成

- [x] 对已注销账号再次注销：返回成功，且注销时间不变、凭据不重复清
- [x] 对已注销账号停用/启用、重置密码：返回稳定的「账号已注销」错误码
- [x] 对已注销账号撤销会话仍返回成功
- [x] 上述两个新错误码在界面上显示成可读文案，不是兜底的「请稍后重试」

**施工记录（2026-02）：**

- 后端：`delete_user` 在已注销时直接返回（不改时间戳、不重复清 Token，只 rollback 放开行锁）；`update_user` 与 `reset_password` 抛 `account_already_deleted`（409，detail 分则写「不能再修改状态」/「不能再重置密码」）；`revoke_sessions` 不改，注销过的账号上仍可执行、删 0 行。
- 错误码两个都已进 `_domain_error` 的映射表（`account_self_protected` 的抛点由工单 04 加），前端文案表同时加两条，并把 `last_superuser_protected` 文案里的「删除」改成「注销」。
- 新增前端用例 `tests/admin-error.spec.ts`：断两个新码有各自的文案、不退到兜底。

**已运行验证：** `uv run pytest -q tests/test_user_admin.py`（15 passed，含两条新用例；把 `backend/src` 回退到开工状态后这两条失败，已实测）；`RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1 uv run pytest -q -k deregistering tests/test_agent_thread_ownership_integration.py`（真库，含注销后 `revoke_sessions` 返回 0）；`npm run typecheck`、`npx eslint`、`npx prettier --check`。

**验收说明：** 第一条与第四条是一组对立的读法，上一轮它们是两份互相矛盾的验收点，本条工单合起来定死：注销本身是「同一个目标状态，重复到达算成功」，停用与改密对已注销账号没有意义、一律拒绝。新错误码要进前端那张「错误码 → 文案」表——不落表时用户看到的是「请稍后重试」，对一个永远不会成功且原因很明确的动作给出这种指引是误导。
