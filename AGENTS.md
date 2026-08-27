# 平台开发约束

1. `backend/` 与 `frontend/` 分别管理依赖、构建、测试和运行命令，不跨运行时导入模块。
2. 前端只通过 HTTP/OpenAPI 契约访问后端，不直接连接 PostgreSQL、Ollama 或 Qdrant。
3. `frontend/src/api/generated/openapi.ts` 只能由后端 `/openapi.json` 生成，不能手工修改。
4. 浏览器 API 使用同域相对前缀 `/api`；任何 `VITE_*` 值都会进入公开构建产物，不能放密钥。
5. 当前范围是只读语义检索，不提前创建无真实行为的 Chat、RAG、Prompt 或流式模块。
6. 修改前端行为后至少运行 `npm run typecheck`、`npm run lint`、`npm run test:run` 和
   `npm run build`；修改后端行为时继续遵守 `backend/AGENTS.md`。
