# 02: 管理员能在后台配好一条上游渠道

**要交付什么：** 超级用户能在后台新增、修改、停用一条上游渠道（显示名、接入类型、地址、凭据）。凭据填一次就够，此后任何读取接口都拿不回来。

**被谁阻塞：** 无（可立刻开工）

**状态：** 已完成

- [x] 在后台新增一条渠道，保存后列表里能看到它
- [x] 读取接口只回「已配置／未配置」——响应里既没有凭据明文，也没有密文
- [x] 编辑时留空凭据 → 原凭据不变；把一条没有凭据的渠道改成需要凭据的接入类型、且不同时补上凭据 → 保存被拒
- [x] 库里存的是密文；明文不进日志、不进异常消息

## 验收记录

新增：`models/llm_provider.py`（表 `llm_providers`）、`repositories/llm_provider_repository.py`、`services/llm_provider_service.py`、`services/llm_credential_cipher.py`、`services/llm_provider_errors.py`、`config/llm_credential.py`、`schemas/llm_providers.py`、`api/llm_providers.py`、迁移 `c4a8f1d6b2e7_新增上游渠道表`；前端 `api/llm-providers.ts`、`features/llm-providers/`、`pages/LlmProvidersPage.vue`，并接入 `AdminPage.vue` / `AdminShell.vue`。

| 勾选项 | 观察 |
|---|---|
| 新增后列表看得到 | Service 层用**真实 ORM + 真实事务（内存 SQLite）**走 `create → list` 读回同一行（`test_llm_provider_service.py::test_a_created_provider_shows_up_in_the_listing_without_its_credential`）；HTTP 层 `test_llm_providers_api.py::TestWriteContract::test_create_passes_the_plaintext_to_the_service_and_returns_201`（201， 响应字段集合恰为契约上那 8 个）；前端 `useLlmProviders.spec.ts` 与 `api/llm-providers.spec.ts` |
| 读接口无明文也无密文 | `TestReadContract::test_list_reports_whether_credential_is_configured`：断言响应字段集合恰为 `{id,name,provider,base_url,enabled,credential_configured,created_at,updated_at}`，且测试里预置的假明文与假密文都**不在** `response.text` 里；响应视图是逐字段构造（`api/llm_providers.py` 的 `_provider_response`），不是 `model_validate(record)` |
| 留空不改／改类型不补凭据被拒 | `test_update_without_credential_keeps_the_stored_value`（改名字与地址后密文逐字不变）、`test_a_provider_without_credential_cannot_become_credential_requiring`（被拒后重新读库那行仍是 `ollama` 且无凭据；同一次保存里补上凭据则通过）、HTTP 422 `llm_provider_credential_required`；前端同样的判定在表单里先拦一道 |
| 库里是密文、明文不外泄 | `test_create_stores_ciphertext_and_never_the_plaintext`、`test_the_same_plaintext_does_not_land_as_the_same_stored_value`（同明文两次落库不相等，不依赖具体加密方案）、`test_the_plaintext_never_reaches_logs_repr_or_exception_messages`（`caplog` 开到 DEBUG） |

**已跑**：`uv run pytest -q` → **920 passed, 76 skipped**；`uv run alembic heads` → 单一 head `c4a8f1d6b2e7`；前端 `npm run lint` 无告警、`npm run test:run` → 87 files / 809 tests passed、`npm run build`（含 `vue-tsc -b`）通过；`openapi.ts` 的 diff 是 **383 insertions / 0 deletions**（纯新增），且与后端重新生成的产物逐字一致。

**未跑**：真实 PostgreSQL 上的 `alembic check`；真实上游连接；被环境变量门控的集成测试。

## 审查发现并修掉的一个缺陷（主代理自己改的）

原实现只在**编辑**那条路上把「纯空白凭据」整成了「没给凭据」，**新建**那条路没有。后果：`POST` 带 `credential: ""` 且 `provider=openai_compatible` 会被当成「一份空凭据」加密存下，列表上显示「已配置」——而它其实没有凭据。这正是 spec 0002 那句「接入类型要求凭据而凭据为空时，保存被拒绝……否则这条渠道会在第一次被选中时以「构造期失败」的形态炸出来」要挡掉的形状。前端表单恰好会省略空字段，所以从我们的界面走不到，但**契约本身就写在 HTTP 上**，而且两条路的语义不一致。

修法：把规整抽成一个共用函数，两个请求模型各挂一条 validator。新增参数化用例 `test_a_blank_credential_is_no_credential_on_create`（两种空串 × 两种接入类型）；**证伪**：把那半段 validator 去掉，这四条立刻红（`assert 'gAAAAA…' is None`），加回后 4 passed。整个修复**没有改对外契约**（重新生成的 `openapi.ts` 与原文件逐字相同）。

## 自行判定的两处判断（需老板复核）

1. **新增两个对外错误码**（spec 只说了「面向用户的文案只剩两类」，那是聊天页的；管理页的失败码是另一个面）：`llm_catalog_unavailable`（503，凭据主密钥未配置或不是合法 Fernet 密钥）与 `llm_catalog_database_unavailable`（503，存储不可用，照 `user_admin_database_unavailable` 那份逐链路各一个码的惯例）。不新增面向聊天用户的文案。
2. **主密钥缺失不阻断启动**，只在真的需要加密那一次返回 503（保住「只想用检索可以完全不管 LLM 配置」这条既有约定，见 `backend/README.md`）。

## 残余风险

- 前端在提交前会 `credential.trim()`，后端刻意不 trim 有内容的凭据（密钥可能以空白开头/结尾）。两者在「密钥真的带空白」时会不一致；权衡后保留前端 trim（贴进来的密钥带尾随换行远比带真空白常见），已在此登记。
- 凭据写入路径靠「不要提交未校验的字段」保证回滚（`get_db_session` 在异常时回滚），已有测试钉住被拒后库里没改动；但这不是数据库约束，将来若新增第二条写入口需重新确认。
- 前端 `staleTime: 10s` 是管理列表自己的缓存；「选择器每次打开重新拉取目录」是后面那条会话选模型工单的验收面，不要拿这一条顶替。
