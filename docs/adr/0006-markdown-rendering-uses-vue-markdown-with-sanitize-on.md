# Markdown 渲染用 `@crazydos/vue-markdown`，并且必须显式开 `sanitize`

Agent 答案正文与 Markdown 文件由共享 `frontend/src/shared/ui/SafeMarkdown.vue` 渲染，它包
`@crazydos/vue-markdown`。2026-09-08 第二阶段将原 `MarkdownAnswer` 扩展为共享组件，安全管道保持一致。

三条配置不是默认值，缺一条就有洞：

- **`sanitize` 必须显式写上。** 这个 prop 默认是 `false`。
- **不装 `rehype-raw`。** 装了它裸 HTML 就会被当标签解析。
- **`sanitizeOptions` 保持不传。** 库的默认值是 `{ allowDangerousHtml: true }`，只在开了
  `rehype-raw` 时才有意义；这里没有 raw 阶段，裸 HTML 到不了 sanitize 就已经是文本了。

`sanitize` 那条是实测出来的，不是从文档推的。用两个一次性 probe spec 量过库的出厂行为：

| 输入                           | 出厂默认（`sanitize` 不传） | 开 `sanitize` |
| ------------------------------ | --------------------------- | ------------- |
| `<script>alert(1)</script>`    | 转义成文本，安全            | 同样安全      |
| `<img src=x onerror=alert(1)>` | 转义成文本，安全            | 同样安全      |
| `[x](javascript:alert(1))`     | **渲染出可点的危险 href**   | href 被剥掉   |

前两行安全是因为没装 `rehype-raw`——裸 HTML 根本没进解析管道。但第三行走的是**链接语法**，不是 HTML，
所以「裸 HTML 会被转义」这条保护对它完全无效。答案正文是模型输出，模型输出里可以出现从外部网页抓来的
链接文本，这就是一条真实的注入路径。开 `sanitize` 才堵住。

另外外链属性用的是 `customAttrs` 的**函数形式**（`a: (node) => …`，按 `node.properties.href` 的 origin
判断），不是对象形式。对象形式（`{ a: { target: '_blank' } }`）会无条件盖到所有 `<a>` 上，站内的 `/agent`
也会另开标签页。函数形式才能按 origin 分流。这一条踩过，现在由 `MarkdownAnswer.spec.ts` 里的
`站内相对链接不开新标签页` 钉住。

范围也是决策的一部分：答案正文和明确标记为 Markdown 的文件过共享解析管道；普通文本文件、工具入参、工具返回内容、用户提问继续用 Vue 的
文本插值。工具内容按原文呈现；用户提问是
他自己敲的原文，他打的 `**` 就该显示成 `**`。这个不对称在 `AgentTurnCard.spec.ts` 里用同一轮的
`**不是粗体**` / `**是粗体**` 断成两侧。

Markdown 图片不发起外部请求，显示替代文本；索引解析同样不下载图片。引用只在 Markdown 文本节点中
转换为内部入口，白名单来自本次运行已核验的证据；代码块、已有链接和模型伪造的内部 href 不获得引用能力。
这层转换复用成熟解析器，不另造 Markdown 引擎。

## 考虑过的方案

**自己写一个 Markdown 渲染器。** 最先被否掉，老板直接定的：不重复造轮子。补充一条技术理由：自己写的
版本要么用 `v-html`（`frontend/AGENTS.md` 明令禁止，且当前 `src/` 下一处都没有，要保持），要么手写
AST 到 `h()` 的映射——后者等于把上面那张表里的每一格都自己实现一遍，包括 `javascript:` 那格。

**用 `markdown-it` + `DOMPurify` 自己接。** 生态更大、配置更熟。但它输出 HTML 字符串，最后一步必然是
`v-html`，和上面同一条禁令撞上。`@crazydos/vue-markdown` 走 unified → Vue vnode，中间不经过 HTML 字符串，
这是选它的主要原因，不是它更流行。

**信库的默认值，不显式写 `sanitize`。** 上面那张表就是为了回答这个。默认值挡住了两种最像 XSS 的输入
（`<script>` 和 `onerror`），所以「随手试一下觉得安全」是很容易发生的误判，而 `javascript:` 链接照样过。
这也是为什么这条要写进 ADR 而不只写注释：它是**看起来已经安全**的那类问题。

**只在组件里写注释，不立 ADR。** 注释写了（现为 `SafeMarkdown.vue` 文件头），但注释只在有人打开那个文件时
才会被看见。而这三条配置的危险形态是「被顺手删掉」——`sanitize` 看着像个多余的 prop，`rehype-raw` 看着
像个能让 HTML 生效的便利依赖。删掉之后界面上**不会有任何变化**，只有 `MarkdownAnswer.spec.ts` 会红。

## 后果

改 `SafeMarkdown.vue` 的渲染配置前先看 `MarkdownAnswer.spec.ts`，那份 spec 的重点是「配置没被改掉」
而不是「Markdown 能渲染」。其中安全配置那一组（转义、`javascript:`、`data:`）如果红了，不要改断言去迁就
代码，那是提示配置被动过了。

**以后不要装 `rehype-raw`。** 如果某天真需要让模型输出的 HTML 生效（例如渲染表格以外的富结构），那是一次
独立的安全决策，要重新走一遍上面那张表并更新本文，不能作为「补个依赖」处理。

共享阅读器接入后，检索与文件页面也会加载 Markdown 管道。是否延迟加载以构建产物和实际首屏表现为据；
不为了减少依赖体积取消安全配置或换成手写解析器。

GFM 支持来自 `remark-gfm`，这是随本决策一起进来的第二个依赖。表格和删除线属于模型的常用输出，缺了它们
会以原始符号显形。加插件是安全的：`remark` 阶段处理的是 Markdown 语法，不引入 HTML 通道。
