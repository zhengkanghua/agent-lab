# 02: 后台能看见已注销账号

**要交付什么：** 超级用户能按需把已注销的账号显示出来，一眼看出它们已注销、什么时候注销的，并且不对它们提供任何操作按钮。

**被谁阻塞：** 01（注销取代删除）

**状态：** 已完成

- [x] 列表接口默认不含已注销账号；带上「显示已注销」参数时含，且每项带注销时间
- [x] 后台页面有该开关（默认关），打开后出现已注销的行，显示「已注销」与注销时间
- [x] 已注销的行不提供停用/启用、注销、重置密码按钮
- [x] 切换开关会真的重新取数，不是复用旧缓存
- [x] 概况条的「超级用户」数字不含已注销账号
- [x] 提交进仓库的前端静态客户端类型里含注销时间字段、不再含旧的删除文案

**施工记录（2026-02）：**

- 后端 `UserAdminService.list_users(include_deleted=False)` + `GET /admin/users?include_deleted=`；离线 HTTP 用例断言这个参数真的到了 Service 层（把路由改回不传，该用例失败，已实测）。
- 前端查询键改成 `userAdminKeys.users(includeDeleted)`（参数进键），三处缓存写入改成遍历所有已缓存的列表、按「这个键带不带已注销」分派：默认那份把注销的行移出，开关那份把它留在原位并改成「已注销」。注销成功不再把行摘掉。
- 静态客户端用后端 `/openapi.json` 重新生成；顺带带进一处之前的漂移（`KnowledgeBaseUpdateRequest` 的 docstring 描述，后端改了但客户端没重生）。
- 本地可视化工具 `scripts/dev-mocks.mjs` 的账号 mock 同步：加 `deleted_at`、按 `include_deleted` 筛、注销改为盖时间戳而不是移出行（否则开发工具里的行为与后端相反）。
- 能力地图用户管理那一行的能力名与测试列随本工单改。
- 收尾复核时发现 spec 里有一条没有任何工单覆盖的要求：建号邮箱冲突的文案要说明「可能是已注销的账号」。
  已在后端 detail 与前端文案表两处改成同一句话，各配一条断言（邮箱唯一性不变，已注销账号仍占着邮箱）。

**已运行验证：** `npm run test:run`（661 passed）；`npm run typecheck`；`npx eslint`（改动文件，0 warning）；`uv run pytest -q tests/test_user_admin.py`；`RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1 uv run pytest -q -k deregistering tests/test_agent_thread_ownership_integration.py`（真库，含 `include_deleted` 两条口径）。

**验收说明：** 第四条容易漏：现在的列表查询键不带参数、缓存有十秒新鲜期，开关照直加而不改键的话点一下不会发请求，表现是「开关像坏了、列表没变」。同时注销成功后那段「把这一行从列表里滤掉」的逻辑要改成把行转成已注销状态，否则开关开着时「行还在、只是已注销」不成立。能力地图里用户管理那一行的能力名与测试列随本工单一起改。
