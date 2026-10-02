# 前端工作约定

适用范围：`frontend/` 下的代码。后端不适用本文，见 `backend/AGENTS.md`。仓库级协作规则见根 `AGENTS.md`。

本文只记录「不知道就会踩坑」的规则。交互与数据边界、开发代理配置、目录边界见 `frontend/README.md`，不在本文重复。

## 验证

验证范围遵循根 `AGENTS.md` 的「工程取舍」，按阶段选择：开发调试只跑相关测试文件或目录，连续修改可用 `npm test -- <测试路径>` 保持 watch，lint 和格式检查优先指定改动文件；需求完成时验证受影响的功能和调用方，类型变化运行 `npm run typecheck`，公共依赖、装配或构建配置变化时扩大范围；需要完整回归或准备发布时跑 `README.md`「验证」一节列出的全套命令，`build` 已包含类型检查，同一份代码无需再单独运行 `typecheck`。

`vue-tsc` 的 `-b` 不能省。本项目是 solution 风格 tsconfig，根 `tsconfig.json` 只有 `references`；不加 `-b` 读不到子项目，会报 0 个错误并正常退出，属于静默通过。`typecheck` 与 `build` 脚本里已经带上，不要改掉。

## 代码约束

1. `src/api/generated/openapi.ts` 是 `openapi-typescript` 生成物（文件头有生成声明），不手改。后端契约变化后按 `README.md`「验证」一节末尾的命令，在后端服务运行时重新生成。

2. 渲染后端返回的正文用 Vue 文本插值，不用 `v-html`。当前 `src/` 下没有任何 `v-html`，保持这个状态。
3. `src/pages` 只做路由级组合，不直接执行 `fetch`；请求收敛在 `src/api`，状态收敛在 `src/features/*`。
4. Playwright route mock 只用于隔离验证前端状态，不能作为后端已更新或部署成功的依据。
5. **凡挂到组件树之外的内容（Vue Teleport、手动 `position: fixed` 到 `body`），样式一律写全局（不 scoped 的 `<style>` 块或 `:global()`）。** scoped 规则靠 `data-v-*` 属性匹配，Teleport 的内容元素拿不到这个属性，整条规则会静默失配——表现是「元素在 DOM 里、就是看不见」（2026-09「更多设置打不开」的根因，当时由第三方 popover 的 Portal 触发；该依赖已随输入区瘦身移除，但规则本身对 DocumentReader 的 Teleport 阅读层仍然成立）。同理，`fixed + transform` 的宿主自成层叠上下文，内容元素上的 `z-index` 出不了那个宿主。

6. **`@layer components` 里的共享规则压不过组件自己的 scoped 样式。** 组件 scoped 样式不分层，未分层的规则恒定胜过任何分层规则——所以「把 `BaseIconButton` 在桌面端藏起来」这种覆盖，写在 `styles/components/*.css` 里**不会生效**（2026-10 实测：`.drawer-close { display: none }` 搬进共享层后，侧栏在桌面端凭空多出一枚关闭键，而条条检查皆绿）。这类覆盖只有两个正当写法：写进调用方自己的 scoped 块，或者干脆用 `v-if` 控制元素是否存在。

7. **模板里用到 PascalCase 标签就必须 import，漏了没有任何检查会拦。** eslint 不解析模板标签，`vue-tsc` 把未知标签当原生元素，两边都过；只有运行时 Vue 打一条 `[Vue warn]`，界面照常渲染、那个组件整个静默失效（2026-10：`JobForm` 改成 `BaseField` 时少一行 import，12 个字段的标签与 aria 接线消失，700 个测试全绿）。守它的是 `src/template-components.node.spec.ts`，它读源码比对，全局注册的组件（Vue 内置、vue-router 的 `RouterLink`/`RouterView`）在表里放行。
