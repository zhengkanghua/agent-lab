---
status: accepted
---

# Markdown 渲染用 `@crazydos/vue-markdown`，并且必须显式开 `sanitize`

Agent 答案正文与 Markdown 文件由共享组件 `SafeMarkdown.vue` 渲染。三条配置不是默认值，缺一条就有洞：`sanitize` 必须显式写上（这个 prop 默认是 `false`）；不装 `rehype-raw`（装了它裸 HTML 就会被当标签解析）；`sanitizeOptions` 保持不传（库的默认值只在开了 `rehype-raw` 时才有意义）。库的出厂默认挡住了 `<script>` 和 `onerror` 这两种最像 XSS 的输入，所以「随手试一下觉得安全」很容易发生；但 `[x](javascript:alert(1))` 走的是链接语法而不是 HTML，那条保护对它完全无效——而答案正文是模型输出，里面可以出现从外部网页抓来的链接。这三条的危险形态是被顺手删掉：删掉之后界面上没有任何变化，只有测试会红。

范围也是决策的一部分：答案正文和明确标记为 Markdown 的文件过共享管道；普通文本文件、工具入参与返回、用户提问继续用文本插值，用户打的 `**` 就该显示成 `**`。

## Considered Options

**自己写渲染器，或用 `markdown-it` 加 `DOMPurify`。** 两者最后一步都是 `v-html`，撞上 `frontend/AGENTS.md` 的禁令；选 `@crazydos/vue-markdown` 是因为它走 unified 到 Vue vnode、中间不经过 HTML 字符串，不是因为它更流行。

## Consequences

以后不要装 `rehype-raw`。真需要让模型输出的 HTML 生效时，那是一次独立的安全决策，要重走实测并更新本文，不能当作「补个依赖」处理。
