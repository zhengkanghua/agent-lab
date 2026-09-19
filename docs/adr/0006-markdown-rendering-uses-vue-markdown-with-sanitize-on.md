---
status: accepted
---

# Markdown 渲染用 `@crazydos/vue-markdown`，并且必须显式开 `sanitize`

Agent 答案正文与 Markdown 文件由共享组件 `frontend/src/shared/ui/SafeMarkdown.vue` 渲染，它包
`@crazydos/vue-markdown`。**三条配置不是默认值，缺一条就有洞**：`sanitize` 必须显式写上（这个 prop
默认是 `false`）；不装 `rehype-raw`（装了它裸 HTML 就会被当标签解析）；`sanitizeOptions` 保持不传
（库的默认值是 `{ allowDangerousHtml: true }`，只在开了 `rehype-raw` 时才有意义，这里没有 raw 阶段）。
这三条与它们的实测依据写在 `SafeMarkdown.vue` 文件头，改动配置前先读它。

**为什么 `sanitize` 那条要写下来而不只写注释。** 它是实测出来的，不是从文档推的：库的出厂默认挡住了
两种最像 XSS 的输入（`<script>`、`onerror` 都被转义成文本，因为没装 `rehype-raw`，裸 HTML 根本没进
解析管道），所以「随手试一下觉得安全」是很容易发生的误判；但 `[x](javascript:alert(1))` 走的是**链接
语法**而不是 HTML，那条「裸 HTML 会被转义」的保护对它完全无效，href 会被原样渲染成可点的危险链接。
答案正文是模型输出，模型输出里可以出现从外部网页抓来的链接文本，这是真实注入路径。而这三条配置的
危险形态是**被顺手删掉**——`sanitize` 看着像个多余的 prop，`rehype-raw` 看着像个能让 HTML 生效的便利
依赖；删掉之后界面上不会有任何变化，只有测试会红。

范围也是决策的一部分：答案正文和明确标记为 Markdown 的文件过共享解析管道；普通文本文件、工具入参、
工具返回内容、用户提问继续用 Vue 的文本插值。工具内容按原文呈现；用户提问是他自己敲的原文，他打的
`**` 就该显示成 `**`。Markdown 图片不发起外部请求，显示替代文本；索引解析同样不下载图片。GFM 支持
来自 `remark-gfm`（表格和删除线属于模型的常用输出，缺了会以原始符号显形）。

## Considered Options

**自己写一个 Markdown 渲染器。** 最先被否掉，老板直接定的：不重复造轮子。补充一条技术理由：自己写的
版本要么用 `v-html`（`frontend/AGENTS.md` 明令禁止，且当前 `src/` 下一处都没有，要保持），要么手写
AST 到 `h()` 的映射——后者等于把每种注入输入的判定都自己实现一遍。

**用 `markdown-it` + `DOMPurify` 自己接。** 生态更大、配置更熟。但它输出 HTML 字符串，最后一步必然是
`v-html`，和上面同一条禁令撞上。`@crazydos/vue-markdown` 走 unified → Vue vnode，中间不经过 HTML
字符串，这是选它的主要原因，不是它更流行。

**信库的默认值，不显式写 `sanitize`。** 见上：默认值挡住了最像 XSS 的两种输入，却挡不住 `javascript:`
链接。这正是「看起来已经安全」的那类问题。

**只在组件里写注释，不立 ADR。** 注释只在有人打开那个文件时才会被看见，而这三条配置的危险形态是被人
在改别的东西时顺手删掉——那时他不会恰好打开这个文件。

## Consequences

改 `SafeMarkdown.vue` 的渲染配置前先看它的 spec。其中安全配置那一组（转义、`javascript:`、`data:`）
如果红了，不要改断言去迁就代码，那是提示配置被动过了。

**以后不要装 `rehype-raw`。** 如果某天真需要让模型输出的 HTML 生效，那是一次独立的安全决策，要重新
走一遍上面的实测并更新本文，不能作为「补个依赖」处理。

共享阅读器接入后，检索与文件页面也会加载 Markdown 管道。是否延迟加载以构建产物和实际首屏表现为据；
不为了减少依赖体积取消安全配置或换成手写解析器。
