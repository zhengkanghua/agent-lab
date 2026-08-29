# 前端工作约定

适用范围：`frontend/` 下的代码。后端不适用本文，见 `backend/AGENTS.md`。仓库级协作规则见根 `AGENTS.md`。

本文只记录「不知道就会踩坑」的规则。交互与数据边界、开发代理配置、目录职责见 `frontend/README.md`，不在本文重复。

## 验证

修改前端行为后至少运行：

```powershell
npm run typecheck
npm run lint
npm run format:check
npm run test:run
npm run build
```

`vue-tsc` 的 `-b` 不能省。本项目是 solution 风格 tsconfig，根 `tsconfig.json` 只有 `references`；不加 `-b` 读不到子项目，会报 0 个错误并正常退出，属于静默通过。`typecheck` 与 `build` 脚本里已经带上，不要改掉。

## 代码约束

1. `src/api/generated/openapi.ts` 是 `openapi-typescript` 生成物（文件头有生成声明），不手改。后端契约变化后在后端服务运行时重新生成：

   ```powershell
   npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/generated/openapi.ts
   ```

2. 渲染后端返回的正文用 Vue 文本插值，不用 `v-html`。当前 `src/` 下没有任何 `v-html`，保持这个状态。
3. `src/pages` 只做路由级组合，不直接执行 `fetch`；请求收敛在 `src/api`，状态收敛在 `src/features/*`。
4. Playwright route mock 只用于隔离验证前端状态，不能作为后端已更新或部署成功的依据。
