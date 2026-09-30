# 03: 把「能服务」与「已就绪」分开

**要交付什么：** 进程能立刻服务、但「是否就绪」由一个新端点如实回答：业务库能查、checkpointer 连接池能取到连接、启动后那次上游配置校验的当前结论（未完成或失败即未就绪）三样一起看；上游不回答（拿不到"配置写错"的证据）时仍然报就绪。同时把那次上游自检从启动等待路径上挪走，于是配置写错的进程不再让对话接口返 503，而是提问后收到模型不可用的事件。就绪端点只读、不鉴权、不进对外接口定义。

**被谁阻塞：** 01: 开工前把四个事实量完并落结论

**状态：** 已完成（代码、测试与注释已改完；本地全量离线回归通过）

- [x] 正常进程报就绪；业务库不可用时报未就绪；checkpointer 连接池取不到连接时报未就绪
- [x] 上游不回答时仍报就绪；模型名不在上游列表里时，在后台校验得出结论之前与之后都算未就绪
- [x] 配置写错时对话接口不再返回 503，而是提问后收到模型不可用的事件；Agent 装配失败仍然返回 503（这条既有行为保持不变）
- [x] 就绪端点的响应体只含状态、不含依赖名与错误原文，且不出现在对外接口定义里
- [x] 把上游自检的响应拖到它的超时上限，进程仍然在远小于该上限的时间内开始接受请求（证明启动窗口里不再等它）
- [x] 启动自检那句代码注释同步说明「它与启动其余工作并发、不串行等待」

### 落地位置

- 新增 `src/agent_lab/api/readiness.py`：`ModelCatalogVerdict`（启动后并发跑那次「列模型」校验，三态结论 + 失败异常）、`GET /ready`（`include_in_schema=False`）、`evaluate_readiness`（三样一起看：Agent Runtime 存在 → 校验结论为 ok → 业务库 `SELECT 1` → checkpointer 池取连接并 `SELECT 1`）。
- `src/agent_lab/main.py`：生命周期里第 4 步 `await model_catalog_check()` 改成 `ModelCatalogVerdict().start(...)`（不 await），Agent Runtime 装配不再等它；收尾里 `verdict.close()`；`application.state.model_catalog_verdict` 随其他状态一起在收尾清空；`verify_configured_llm_models` 的 docstring 按要求改写（并发、不串行等待、不要改回 await）。
- `src/agent_lab/api/error_contract.py`：新增 `LlmModelNotListedError → llm_model_not_found`（503、不可重试），与既有的 `NotFoundError` 同码同文案——「同一个码对应同一句话」这条在 Agent 链路上靠人守，所以两行的 detail 逐字相同。
- `src/agent_lab/api/agent_chat.py`：`/agent/chat` 新增依赖 `get_model_catalog_verdict`；在占位之后、把运行交给注册表之前拦一道——结论是「模型名不在上游列表里」时，先 `threads.finish_run` 归还占位，再返回只含一帧 `error` 事件的 SSE 流（`_stream_model_not_listed`，`code/detail/retryable` 全部来自错误表）。
- `src/agent_lab/agent/model_catalog.py`：模块 docstring 按实测改写——结论由就绪端点表达，而「未就绪」在 Swarm 里等于容器被杀重启，所以「配置写错只影响 Agent」这句只在被杀前那几十秒内成立。
- 测试：新增 `tests/test_readiness_api.py`（9 条，三条判据各自单独按下去 + 响应体形状 + 不进 OpenAPI + 启动不被拖慢）；`tests/test_agent_chat_api.py` 里那条钉 503 的用例换成 `test_a_model_name_not_in_the_catalog_arrives_as_an_error_event`（就绪未就绪 + 事件码 + 占位已归还 + 只读链路照常）。“Agent 装配失败仍然 503”那条未动。

### 验证记录

```text
uv run pytest -q tests/test_readiness_api.py                     9 passed
uv run pytest -q tests/test_agent_chat_api.py                   23 passed
uv run pytest -q tests/test_app_helpers.py tests/test_error_contract.py \\
                  tests/test_model_catalog.py tests/test_agent_runtime.py    43 passed
uv run pytest -q tests/test_agent_runs.py tests/test_agent_streaming.py \\
                  tests/test_agent_handover.py tests/test_agent_replay.py \\
                  tests/test_agent_threads_api.py                             80 passed
uv run pytest -q                    （全量离线回归）893 passed, 76 skipped
```

未运行：前端构建与测试（本工单不动前端，且对外 schema 未变——`/ready` 是 `include_in_schema=False`，前端不需要重新生成类型；`frontend/src/features/agent-chat/model/agent-error.ts` 里 `llm_model_not_found` 本来就映射到 `CONFIGURATION_COPY`）。前端回归放在工单 12。

### 与实测行为对上的一条（写给后来人）

在 Swarm 里未就绪 = 容器被杀重启（工单 01 的 C3 实测），所以「配置写错的进程照起、检索照服务」这句话只在被杀之前那几十秒成立。部署场景下这是我们要的（旧任务不被替掉、更新自己回滚）；本机开发不受影响（没有那层健康检查）。已经把这条写进 `api/readiness.py` 与 `agent/model_catalog.py` 的 docstring，工单 10 的手册也要写。
