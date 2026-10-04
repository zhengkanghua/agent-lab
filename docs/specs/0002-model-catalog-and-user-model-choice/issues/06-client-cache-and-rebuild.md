# 06: 客户端缓存与失效重建

**要交付什么：** 被用到的每一个模型只构造一次客户端并复用，不为每次模型调用付一次连接池与 TLS 握手的钱；配置改了之后新值能生效。上一道工单里每次解析都新建客户端——功能是对的，只是慢。

**被谁阻塞：** 05

**状态：** 已完成

- [x] 第二次用同一个模型时，客户端被复用，不重新建立连接
- [x] 那一行的地址、接入类型、凭据、上游模型名、上下文窗口任一项变了 → 重建客户端
- [x] 那一行没了 → 淘汰
- [x] **只是被停用要保留**——接手一条在途运行还要用它跑完
- [x] **可用性判定不走这个缓存**：刚停用的模型立刻就不能被选中，不必等缓存过期

## 验收记录

改了什么：`agent/model_resolution.py` 的 `CatalogModelResolver` 加缓存（以模型 id 为键、带指纹与上次校验时刻、超出上界按最久未用淘汰、取时可注入）；`agent/limits.py` 新增两个常量。

| 勾选项 | 观察 |
|---|---|
| 第二次解析复用同一客户端 | `test_agent_model_resolution.py::test_a_second_resolve_of_the_same_model_reuses_the_same_client`：**同一个对象**（`second is first`）**且目录表只被读了一次**（`reads == 1`）——spec 要求用可观察事实、不去看缓存字典 |
| 五个字段逐个改了都重建 | 参数化 `test_a_changed_field_of_the_row_rebuilds_the_client[base_url/provider/credential_ciphertext/upstream_model_name/context_window]` 五条：先越过信任窗口、再改那一行的一项，断言 `after is not before` 且 `reads == 2`；另两条：`::test_a_row_whose_content_is_unchanged_keeps_its_client`（过期后重读但内容未变→继续复用同一个）、`::test_a_change_inside_the_trust_window_is_not_picked_up_yet`（窗口内一次目录表都不读） |
| 行没了→淘汰 | `::test_a_removed_row_evicts_the_cached_client`：解析失败后再把**同一份**行放回去，断言拿到的**不是**旧对象（若条目没被淘汰，过期路径会因指纹一致而把旧客户端还回来） |
| 只是停用要保留 | `::test_disabling_the_row_keeps_the_cached_client`：越过窗口后把模型行与渠道行的 `enabled` 都改假，仍 `second is first` 且 `reads == 2`（确实重读了，只是不把它当「内容变了」） |
| 可用性判定不走缓存 | `::test_the_availability_gate_rejects_a_just_disabled_model_though_it_is_cached`：同一时刻两个方向的观察——缓存里已经有一条，把模型行停用（**不推进时钟**），真实 `LlmModelSelectionService` 立刻报 `LlmModelUnavailableError`；而同一刻解析包装仍把缓存里那个客户端交给接手的那一轮。那道门一行未改，既有那三条 404/409 用例仍绿 |

**已跑**：`uv run pytest -q` → **1104 passed, 76 skipped**（比上一条 +12）；`uv run alembic heads` → 单一 head。

**未跑**：前端（未碰）；真库集成测试。

**证伪（我自己做的）**：把**启用位塞进指纹** → 那条「停用要保留」立刻红（`AssertionError: 停用不改内容，不能重建客户端`）；还原后 1104 passed。它还自己跑了另一条证伪：把「内容未变继续复用」改成「超时一律重建」→ 对应用例变红。

## 两处我交给实施方定、现在要老板复核的选择

1. **淘汰策略与上界**：`MODEL_CLIENT_CACHE_MAX_ENTRIES = 32`（最久未用先淘汰）。它的注释写的是：「每个条目就是一个客户端，也就带着一套连接池与 TLS 会话，所以这个上界说的是『一个 API 进程最坏同时占多少上游连接池』。32 比一次部署里真正被用过的模型数大一个量级（模型目录通常个位数到几十条），取这个量级是为了让**淘汰在正常情况下永不触发**——它只兜住『目录被填进很多条目、而且每一条都被人选过』时内存不无界增长。」我判可行。
2. **信任窗口** `MODEL_CLIENT_CACHE_TTL_SECONDS = 60.0`（spec 定的 60 秒），但**「当前时间」做成了可注入的取时函数**，否则那 60 秒只能在测试里真等、或去断言内部字段。

## 残余风险

- **改一条已有配置最多 60 秒后全副本生效**（这是换「不需要任何跨进程通知」的那一笔，ADR 0046 写明的代价）；新建条目没有这个延迟（未命中直接查库）。
- 指纹比的是**凭据密文**而不是明文：换凭据必换密文（Fernet 每次密文不同），所以不必在每次校验时解密；代价是「同一把凭据重新加密一次也会被当成变了」——多重建一次，无害。
- 缓存上界是**每进程**的（ADR 0046 已记：每个被用过的模型各占一套连接池）。
