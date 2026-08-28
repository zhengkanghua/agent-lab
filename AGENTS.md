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
10. skill 以 `.codex/skills/` 为唯一源，`.claude/skills/` 是它的副本。改完源必须同步副本，
    两份内容保持一致；只改一侧会让 Codex 和 Claude Code 读到不同版本的同名 skill。
    同步与校验（`diff` 无输出即一致）：

    ```bash
    # <name> 为 skill 目录名
    rm -rf .claude/skills/<name> && cp -r .codex/skills/<name> .claude/skills/<name>
    diff -r .codex/skills/<name> .claude/skills/<name>
    ```

    新增 skill 时两侧一起建。删除 skill 时两侧一起删。
11. 探索代码前先读仓库根 `CONTEXT.md`（术语表）和 `docs/adr/`（决策记录）。两者都不存在就直接
    跳过，不要提示缺失、也不要提议预先创建——它们由 `/grill-with-docs` 在术语或决策真正落地时
    懒创建。输出里提到领域概念时沿用 `CONTEXT.md` 的既定说法，不要换同义词。
    若结论与某条 ADR 冲突，显式指出是哪条，不要静默绕过。
12. `CONTEXT.md` 只是术语表：一个词条一句定义，不放实现细节、需求、规范或待办。决策和取舍写进
    `docs/adr/`，文件名 `NNNN-slug.md`，编号扫目录内最大号 +1。混着写会让它退化成第二份
    `AGENTS.md`，两份都没人信。
13. 用中文沟通：回复、报告，以及规格、设计、需求文档和 ADR 都用中文写。代码标识符、命令、
    文件路径和工具原始输出保持原样，不翻译。ADR 文件名用 ASCII kebab-case，正文用中文。
