# 03: 管理员能在渠道下挂可用模型，目录恰好有一个确定的默认

**要交付什么：** 在一个渠道下面挂若干可用模型（上游模型名、展示名、上下文窗口），其中恰好有一个是默认；停用与改挂都不会破坏这条不变量。

**被谁阻塞：** 02（渠道得先能建出来）

**状态：** 已完成

- [x] 新建时窗口留空被拒；填了能保存，并出现在后台页面与用户的选择列表里
- [x] 同一渠道内上游模型名不可重复
- [x] 展示名留空时，选择器上显示的是上游模型名
- [x] 目录里还没有默认时，第一条**变为可用**的模型自动成为默认
- [x] 一次启用多条时，默认落在**最早添加**的那一条
- [x] 新建时不允许给一个不可用的模型打默认标记
- [x] 把某个模型设为默认时，它必须当前可用
- [x] 请求把当前默认的标记置假 → 被拒
- [x] 停用当前默认模型 → 被拒；停用它所属的渠道 → 被拒
- [x] 两个请求同时设默认 → 不会出现两个默认，落败的那一方拿到一个明确的冲突错误
- [x] 停用一条渠道之后，它下面的模型从选择列表里消失；**再把这条渠道启用回来，它下面原先启用的那些模型自动回到可选**（这条用来证伪「逐个改模型启用位」那种写法）
- [x] 把模型改挂到一条停用的渠道 → 被拒
- [x] 窗口那一栏的表单提示说清「填你确认过的最小值」——这个数同时是压缩触发与单次工具输出上限的分母，填小了不是无害的

## 验收记录

新增：表 `llm_models` + 模型 `models/llm_model.py`、迁移 `d2f5a8c31b76`（`down_revision` → 当时的 head `e9b3c7a41d58`）、`repositories/llm_model_repository.py`、`services/llm_model_service.py`、`services/llm_model_errors.py`、`schemas/llm_models.py`、`api/llm_models.py`；`services/llm_provider_service.py` 接上跨表的默认维护（渠道启用那一半）；`main.py` 注册（**不整组加门**，逐条路由挂）；前端 `api/llm-models.ts`、`features/llm-models/`、`pages/LlmModelsPage.vue` 与三处注册点。

| 勾选项 | 观察 |
|---|---|
| 窗口必填 + 出现在两个列表 | `test_llm_models_api.py::test_a_missing_or_non_positive_window_is_rejected`（参数化：缺、0、负数）；`test_llm_model_service.py::test_creating_an_enabled_model_makes_it_the_default_and_lists_it_everywhere`（后台列表与可选列表都看得到它） |
| 同渠道内模型名唯一 | `test_the_same_upstream_model_name_cannot_repeat_inside_one_provider`、`test_renaming_a_model_onto_an_existing_name_is_rejected_too`（都是 409 领域错误） |
| 展示名回落 | `test_llm_models_api.py::test_the_selection_list_falls_back_to_the_upstream_name`、`test_a_configured_display_name_shows_up_as_is` |
| 第一条变为可用的自动当默认 | 三个入口各一条：`test_creating_an_enabled_model_makes_it_the_default_and_lists_it_everywhere`、`test_the_first_model_to_become_available_is_the_default_and_only_one_is_default`、**`test_enabling_a_provider_that_was_created_disabled_backfills_the_default`**（先建停用渠道→里面建启用模型→再启用渠道） |
| 一批多条取最早添加 | `test_the_earliest_added_model_wins_when_several_become_available_at_once`（夹具显式写 `created_at`，绕开 SQLite 秒级精度），同刻两条按 id：`test_the_smaller_id_wins_when_two_models_are_added_at_the_same_moment` |
| 不可用的不允许打默认 | `test_a_default_flag_is_rejected_when_the_model_is_not_available`、`test_setting_the_default_requires_the_model_to_be_available`、`test_a_model_under_a_disabled_provider_cannot_be_made_the_default` |
| 默认不能置假 / 不能被停用 | `test_the_default_flag_cannot_be_cleared_by_a_request`、`test_the_default_model_cannot_be_disabled_and_neither_can_its_provider`（后者同时覆盖「停渠道被拒」：`LlmProviderInUseAsDefaultError`） |
| 同时设默认不会出两个 | 部分唯一索引的结构断言：`test_llm_models_migration.py::test_the_model_declares_the_partial_unique_index_over_the_default_flag` 与 `test_upgrade_creates_the_partial_unique_index_with_the_same_predicate`（谓词与 ORM 逐字对比）；写路径 `test_setting_the_default_clears_the_previous_one_in_the_same_transaction`；落败方的 409 契约 `test_llm_models_api.py::test_the_default_conflict_is_a_clear_409_that_leaks_nothing` |
| 停用渠道→消失；启用回来→自动可选 | `test_disabling_a_provider_hides_its_models_and_enabling_it_brings_them_back`（可用性是 join 查出来的，见 `llm_model_repository.py` 的 `list_available_models`；仓库里**没有**任何地方在停用渠道时改模型的 `enabled`） |
| 改挂到停用渠道被拒 | `test_a_model_cannot_be_moved_to_a_disabled_or_unknown_provider` |
| 窗口表单提示 | `LlmModelDirectory.vue` 的 `hint="填你确认过的最小值，不是「保守就行」：压缩什么时候触发按它的比例算，一轮里单次工具输出能放多长也取它的比例。填大了可能超过上游真实窗口、整轮失败；填小了会提前压缩并截断工具返回的内容。"` |

**已跑**：`uv run pytest -q` → **999 passed, 76 skipped**；`uv run alembic heads` → 单一 head `d2f5a8c31b76`；前端 `npm run lint` 无告警、`npm run test:run` → **90 files / 841 tests passed**、`npm run build`（含 `vue-tsc -b`）通过；`openapi.ts` 的 diff 数字是 1773/895，看着像大面积删改，但**逐行排序后对比旧文件里没有一行丢失**（`comm -23` 结果为 0 行），且从当前后端重新生成的结果与它**逐字一致**——那 895 行只是生成器因新增 schema 而重排。

**未跑**：真库上的 `alembic check`；被环境变量门控的集成测试；真实并发（见下面的取舍）。

## 主代理在核验时自己改的两处（都已证伪）

1. **补默认撞上并发设默认时给错了错**。「自动补默认」那条路的 `commit` 撞上部分唯一索引时直接把 `IntegrityError` 放了出去，顺着 `SQLAlchemyError` 变成 503「模型目录存储当前不可用」——把管理员引向「去查数据库」的错误结论，而实际只是另一个请求刚改过默认。已在 `ensure_default_model` 里翻成 `LlmModelDefaultConflictError`（与显式设默认同一个错误）。**注意为何不能吞掉**：那次 `commit` 失败会把调用方那一笔一起回滚，而其中一条调用路是「启用渠道」——吞掉等于「点了启用却没启用」。证伪：还原成不翻错误，新用例红（`sqlalchemy.exc.IntegrityError: duplicate key`）；改回后 58 passed。
2. **渠道路由不认可用模型那一侧的领域错误**。启停渠道会碰到「目录里恰好有一个默认」这条不变量，而它的守卫在 `LlmModelService`；`api/llm_providers.py` 的 route class 原来只捕 `LlmProviderDomainError`，于是这类错误变成未分类的 500。已扩成 `(LlmProviderDomainError, LlmModelDomainError)` 共用一个映射（两个类都有 `code`/`detail`/`status_code`）。证伪：还原成只认提供方基类，新用例直接抛 `LlmModelDefaultConflictError`（在 HTTP 层就是 500）；改回后通过。

## 施工事故

执行子代理**跑满 30 分钟超时**，停在最后一步（正准备跑前端全套验证）。这次没留调试文件；后端新测试它已经写完且当时就是绿的。我没重派：剩下的就是**跑验证**，而那本来就不外包。我自己跑完两端验证，并在核验中发现并修掉了上面两处。

## 残余风险与偏差

- **比 spec 更严一处（有意，已登记）**：规则 5 只说「**可用**模型的所属渠道可以改，但改后必须仍可用」，实现是「只要改挂目标渠道停用就拒」——包括**自身已停用**的模型也不能停放到一条停用渠道下。影响面：管理员不能先把停用的条目挂在停用渠道下以后一起启用；权衡后保留（与「目标渠道必须启用」同一条判据，少一个分叉）。
- **真并发没在离线套件里验**：`postgresql_where` 是 PG 方言，SQLite 上建不出那条部分索引（夹具里把它摘掉了，注释写明原因）。所以并发那一半靠：模型与迁移的**结构断言**（含谓词原文逐字对比）+ 409 契约 + 真库上的 `alembic check`。要真并发就单独立一条环境变量门控的真库集成测试。
- 「用户的选择列表」本工单只做到**后端接口**（`GET /llm-models/available`，任何已登录账号可读，只回自身启用且渠道启用的条目）；会话里那个选择器、以及「选择被记住、逐轮可见、失效时如实提示」是下一条工单的验收面。
- `config/llm.py` 里 `LlmProvider` 那句「唯一读取本枚举的地方是 build_chat_model」已按实际改准（上一轮工单落地后就已失真）。
