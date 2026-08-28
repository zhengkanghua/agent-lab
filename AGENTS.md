# 平台开发约束

1. `backend/` 与 `frontend/` 分别管理依赖、构建、测试和运行命令，不跨运行时导入模块。
2. 前端只通过 HTTP/OpenAPI 契约访问后端，不直接连接 PostgreSQL、Ollama 或 Qdrant。
3. `frontend/src/api/generated/openapi.ts` 只能由后端 `/openapi.json` 生成，不能手工修改。
4. 浏览器 API 使用同域相对前缀 `/api`；任何 `VITE_*` 值都会进入公开构建产物，不能放密钥。
5. 当前范围是只读语义检索，不提前创建无真实行为的 Chat、RAG、Prompt 或流式模块。
6. 文档聚合、去重和结果排序由 Qdrant grouped query 在后端完成。前端不对有限的 Chunk
   top-k 做伪分组，也不重排后端已排好的顺序——两边各算一遍必然漂移。
7. `POST /vector-search` 是兼容的 Chunk 级契约，允许同一 document 的多个 Chunk 分别出现。
   不修改或删除它的既有语义。
8. 修改前端行为后至少运行 `npm run typecheck`、`npm run lint`、`npm run test:run` 和
   `npm run build`；修改后端行为时继续遵守 `backend/AGENTS.md`。
9. `typecheck` 必须保留 `vue-tsc -b` 的 `-b`。根 `tsconfig.json` 是 solution-style
   （`"files": []` + 只有 `references`），不加 `-b` 时 `vue-tsc` 只检查那个空的根 project，
   不会跟进 references，对任何真实类型错误都报 0 错误——门禁会静默空转而不是失败。
