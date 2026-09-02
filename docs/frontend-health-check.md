# 前端健康检查清单（段三）

## 1. AccountPage 使用了不存在的 design token

**影响范围**: `frontend/src/pages/AccountPage.vue`、`frontend/src/features/account/components/PasswordChangeForm.vue`

**预估成本**: 1.5 小时

**验证方法**: 
1. 在浏览器开发者工具中打开 `/account` 页面
2. 检查 Computed 样式，查找 `var(--space-*)` 等变量的计算值
3. 如果显示为无效或回退到初始值，说明 token 不存在

**问题描述**:
AccountPage 和 PasswordChangeForm 引用了大量 `tokens.css` 中未定义的变量：
- 间距系统：`--space-2`、`--space-3`、`--space-4`、`--space-5`、`--space-6`、`--space-8`
- 字号系统：`--text-sm`、`--text-base`、`--text-lg`、`--text-xl`、`--text-2xl`
- 表面层：`--surface-secondary`、`--surface-tertiary`、`--surface-hover`
- 语义色：`--accent-primary`、`--surface-error`、`--text-error`、`--surface-success`、`--text-success`
- 圆角：`--radius-full`（现有的是 `--radius-pill`）

实际 `tokens.css` 只定义了：
- 颜色语义层：`--surface-base/raised/sunken/overlay/scrim/inverse`、`--text-primary/secondary/muted`、`--accent/accent-hover/accent-soft`
- 圆角：`--radius-sm/md/lg/pill`
- 字体：`--body-font/mono-font`
- 布局：`--content-width/reading-width`

**修复方向**:
1. **方案 A（推荐）**：在 `tokens.css` 中补全缺失的 token，按现有命名规则建立完整的间距、字号、状态色体系
2. **方案 B**：改写 AccountPage 和 PasswordChangeForm，将所有 `var(--space-*)` 替换为硬编码 px 或现有 token

---

## 2. 全局缺少间距 token，大量硬编码 px 值

**影响范围**: 所有页面和组件（28 个 .vue 文件）

**预估成本**: 3 小时

**验证方法**:
```bash
grep -rn '\bpadding:\|gap:\|margin-' frontend/src/**/*.vue | grep -E '\d+px' | wc -l
```
统计硬编码间距的数量（当前约 200+ 处）

**问题描述**:
项目建立了完整的颜色 token 体系（双层语义映射），但间距全部硬编码：
- `padding: 10px 12px`、`gap: 7px`、`margin-top: 13px` 等散落在所有文件中
- 同一语义间距在不同文件中取值不一致（例如「卡片内边距」在不同组件中是 `10px 12px`、`12px 13px`、`12px 14px`）
- 修改设计时需要逐文件搜索替换，无法通过 token 统一调整

tokens.css 注释声称"非颜色 token 不分层：它们没有主题维度"，但间距同样需要 token 来保证一致性与可维护性。

**修复方向**:
1. 在 `tokens.css` 中建立 `--space-*` 阶梯（例如 2px/4px/6px/8px/12px/16px/24px/32px）
2. 编写迁移脚本或手动替换高频硬编码值为对应 token
3. 在 `AGENTS.md` 中明确"新增组件必须使用间距 token，不得硬编码 px"

---

## 3. 窄屏下邮箱地址可能溢出

**影响范围**: `frontend/src/layouts/AppShell.vue` 的账号标识

**预估成本**: 0.5 小时

**验证方法**:
1. 登录后打开开发者工具，切换到 Mobile 视图（375px 宽度）
2. 查看长邮箱地址（例如 `verylongusername@example.com`）是否被截断或溢出

**问题描述**:
AppShell.vue:277 的 `.account-identity` 设置了 `max-width: 220px` + `overflow: hidden` + `text-overflow: ellipsis`（第 295-298 行），但需要确认在实际长邮箱下是否生效，以及 560px/720px 断点下是否有足够空间。

**修复方向**:
在窄屏断点（≤560px）时进一步收窄容器宽度，或调整 `.account-identity span` 的 `min-width: 0` 以确保 flex 子项能被压缩。

---

## 4. 表格列在 1040px 断点处突然折叠，中间分辨率缺失过渡

**影响范围**: `frontend/src/features/user-admin/components/UserAccountRow.vue`、`UserDirectoryTable.vue`

**预估成本**: 1 小时

**验证方法**:
1. 打开 `/admin/users`，窗口宽度从 1200px 逐步拖窄到 700px
2. 观察 1040px 和 720px 两个断点处表格布局的变化
3. 检查在 720px~1040px 之间列内容是否挤压、文字是否重叠

**问题描述**:
UserAccountRow 在三个断点间切换列宽定义：
- `>1040px`: 5 列网格 `minmax(235px, 1.7fr) minmax(115px, 0.7fr) ...`
- `≤1040px`: 3 列网格 `minmax(260px, 1.4fr) repeat(2, minmax(120px, 0.7fr))`
- `≤720px`: 2 列网格，部分内容折行

但在 840px~1040px 之间，5 列布局的 minmax 约束可能导致内容挤压（最小宽度加起来 900px，而容器只有 840px）。

**修复方向**:
在 900px 断点增加一个中间态，或调整 1040px 断点的 minmax 下限，确保列最小宽度之和不超过容器。

---

## 5. 字体大小未使用流式缩放（clamp），在超宽/窄屏下缺乏弹性

**影响范围**: 所有页面的标题和正文

**预估成本**: 2 小时

**验证方法**:
1. 在 320px（最小支持宽度）和 2560px（4K 显示器）宽度下查看页面
2. 检查标题（h1）字号是否合理（320px 下不宜过大，2560px 下不宜过小）
3. 对比现有固定 rem 值与理想 clamp() 的效果

**问题描述**:
tokens.css 注释提到"typography — set the whole scale fluid: clamp() from display through body"，但实际代码中没有定义任何 clamp() 字号，所有字号都是固定 rem 或 px：
- SearchPage h1: `font-size: 2.25rem`（固定）
- AgentChatPage h1: 同一标题层级分别写了 `2.45rem`（UserAdminPage）和 `2.25rem`（SearchPage），缺乏统一

design_sense 要求的流式缩放未落地。

**修复方向**:
1. 在 `tokens.css` 中定义 `--text-display`、`--text-heading-1`、`--text-body` 等字号 token，使用 `clamp(min, preferred, max)` 语法
2. 将所有组件中的硬编码字号替换为 token 引用
3. 参考公式：`clamp(1.95rem, 1.6rem + 1.5vw, 2.45rem)` 确保窄屏可读、宽屏舒展

---

## 6. 缺少键盘焦点指示器的自定义组件

**影响范围**: `UserAccountRow.vue` 的开关按钮（toggle）

**预估成本**: 0.5 小时

**验证方法**:
1. 打开 `/admin/users`，使用 Tab 键遍历所有交互元素
2. 观察自定义开关（.toggle-switch）在获得焦点时是否有明确的视觉指示
3. 使用 axe DevTools 或 Lighthouse 检查「Focusable elements must have focus styles」

**问题描述**:
UserAccountRow.vue:301-302 为开关按钮的 `:focus-visible` 定义了 `outline: 3px solid var(--accent-ring)`，但该元素是自定义控件（通过 checkbox + CSS 模拟），需要确认：
1. 焦点环的偏移量是否足够（`outline-offset: 2px`）
2. 在所有主题下对比度是否达到 WCAG 3:1
3. 使用键盘操作（空格切换）时状态是否正确更新

**修复方向**:
在浏览器中实测，如果焦点环不明显，调整 `outline-offset` 或增加内发光（`box-shadow`）作为补充指示。

---

## 7. 颜色对比度可能不达标的小字号文本

**影响范围**: 所有使用 `--text-muted` 的说明文字（font-size < 0.75rem）

**预估成本**: 1 小时

**验证方法**:
1. 使用 Chrome DevTools 的「CSS Overview」面板生成对比度报告
2. 或使用 Lighthouse 的 Accessibility 审计
3. 重点检查 `--text-muted`（当前为 `--neutral-500`）在 `--surface-base` 上的对比度是否 ≥4.5:1（小字号要求）

**问题描述**:
tokens.css 定义了三档文字颜色：
- `--text-primary`: `--neutral-900`
- `--text-secondary`: `--neutral-700`
- `--text-muted`: `--neutral-500`

在 0.7rem ~ 0.78rem 的说明文字（例如 ThreadSidebar.vue:143 的空态说明、AgentChatPage.vue:375 的底栏细则）上使用 `--text-muted`，颜色为 `#7b8481`，在白底上对比度约 3.8:1，低于 WCAG AA 小字号标准（4.5:1）。

**修复方向**:
1. 将小于 0.8rem 的文字强制使用 `--text-secondary`（对比度更高）
2. 或调整 `--neutral-500` 的明度，使其对比度达标（会影响所有使用该 token 的地方）

---

## 8. 深色模式尚未实现，但已埋 token 基础

**影响范围**: 整个项目

**预估成本**: 6 小时（完整深色模式）

**验证方法**:
检查 `tokens.css` 是否有 `@media (prefers-color-scheme: dark)` 或 `[data-theme="dark"]` 选择器。

**问题描述**:
tokens.css 注释声称"分层的理由是深色模式"（第 7 行），并建立了完整的语义层，但实际没有定义深色模式的 token 映射。当前 `color-scheme: light` 写死在 :root 上，系统级深色模式不生效。

这不算 bug（产品可能不需要深色模式），但属于"规划了基础设施、未实际落地"的技术债。

**修复方向**:
1. 如果不需要深色模式，删除 tokens.css 中关于"深色模式"的所有注释，避免误导
2. 如果需要，补全 `@media (prefers-color-scheme: dark)` 块，重映射语义 token 到深色色阶（`--neutral-50` 变暗、`--neutral-950` 变亮）

---

## 9. ARIA landmark 缺失或不一致

**影响范围**: `SearchPage.vue`、`AgentChatPage.vue`、`AccountPage.vue`

**预估成本**: 0.5 小时

**验证方法**:
1. 使用 Landmarks 浏览器扩展或屏幕阅读器（NVDA/JAWS）遍历页面地标
2. 检查是否存在「未标注的 main」或「多个 main 但 aria-label 相同」

**问题描述**:
三个主要页面都有 `<main id="..." class="...">` 元素：
- SearchPage: `id="search-workspace"`
- AgentChatPage: `id="agent-workspace"`
- AccountPage: 没有 main landmark（整个页面是一个 `.account-page` div）

AppShell 传入 `main-id` 和 `skip-label`，但 AccountPage 没有使用 AppShell，landmark 语义不一致。

**修复方向**:
1. 给 AccountPage 补上 `<main>` landmark 或让它也走 AppShell
2. 确保每个页面有且仅有一个 `<main>`，且通过 `aria-labelledby` 关联页面标题

---

## 10. BaseField 的 hint 插槽未在实际组件中使用

**影响范围**: `BaseField.vue` 及其调用方

**预估成本**: 0.3 小时（确认无副作用后可标记为「设计意图」）

**验证方法**:
```bash
grep -rn 'template #hint' frontend/src
```
检查是否有组件使用了 BaseField 的 hint 插槽。

**问题描述**:
BaseField.vue:73-76 定义了 `<slot name="hint">` 用于"说明本身要带状态"的场合（注释举例：剩余字数随接近上限变色），但实际项目中所有表单字段都使用 `hint` prop 传入静态字符串，未使用插槽。

这可能是「预留能力但暂未用到」，不一定是问题，但会增加组件复杂度。

**修复方向**:
1. 如果确认未来不需要动态 hint，删除插槽相关代码，简化组件
2. 如果保留，在 AgentComposer 的"剩余字符数"提示中实际使用这个插槽作为示例

---

## 11. 浮层（popover）在窄屏下可能超出视口

**影响范围**: `BasePopover.vue` 及其调用方（AppShell 用户菜单、SearchComposer 参数调整）

**预估成本**: 1 小时

**验证方法**:
1. 在 375px 宽度下打开用户菜单和参数调整浮层
2. 检查浮层是否完全可见，或是否被视口边缘裁切
3. 测试触摸设备上的点击外部关闭是否正常

**问题描述**:
BasePopover.vue:142 定义了 `max-width: min(380px, calc(100vw - 32px))`，在窄屏下会收窄浮层。但：
1. 注释承认"没有引入 floating-ui，所以不具备碰到视口边缘自动翻转的能力"（第 15-17 行）
2. placement 只支持四个方向（top-start/end、bottom-start/end），没有自动检测逻辑
3. 如果触发元素本身就在视口边缘（例如顶栏右上角的用户头像），`placement="bottom-end"` 的浮层可能超出右边界

**修复方向**:
1. 在 BasePopover 内增加简单的边界检测：计算触发元素位置后动态调整 placement
2. 或引入轻量级定位库（@floating-ui/dom 约 3KB gzipped）
3. 临时方案：在调用方手动指定适配的 placement（窄屏时改用 bottom-start）

---

## 12. 长会话标题在列表中溢出

**影响范围**: `ThreadListItem.vue`

**预估成本**: 0.3 小时

**验证方法**:
1. 创建一个标题超过 60 字的会话（后端已按 60 字截断，但仍可能溢出窄容器）
2. 在 244px 宽的侧边栏中观察标题是否换行或溢出

**问题描述**:
ThreadListItem.vue:116-121 对 `.thread-title` 使用了 `overflow: hidden` + `text-overflow: ellipsis`，但没有配合 `white-space: nowrap`，导致文本仍会换行，ellipsis 不生效。

**修复方向**:
为 `.thread-title` 补上 `white-space: nowrap`，确保长标题单行截断而非换行。

---

## 13. 表单输入框缺少 autocomplete 属性

**影响范围**: 部分表单字段

**预估成本**: 0.5 小时

**验证方法**:
使用 Lighthouse 的 Accessibility 审计，检查「Input elements should have autocomplete attributes」警告。

**问题描述**:
PasswordChangeForm.vue 正确使用了 `autocomplete="current-password"` 和 `autocomplete="new-password"`（第 30、38、48 行），但需检查：
1. LoginPage 的邮箱和密码输入框是否有 `autocomplete="username"` 和 `autocomplete="current-password"`
2. UserCreateForm 的邮箱输入框是否应标注 `autocomplete="email"`

缺少 autocomplete 会导致浏览器无法正确提供自动填充建议，降低可用性。

**修复方向**:
逐个表单检查，补全符合 WHATWG 规范的 autocomplete 属性值。

---

## 优先级建议

### 高优先级（影响功能或品牌一致性）
1. **第 1 条** - AccountPage token 不存在（会导致样式失效）
2. **第 3 条** - 邮箱溢出（影响实际使用）
3. **第 12 条** - 会话标题溢出（影响实际使用）

### 中优先级（改善体验）
4. **第 2 条** - 间距 token 缺失（技术债，长期维护成本高）
5. **第 5 条** - 字体流式缩放（提升响应式体验）
6. **第 7 条** - 颜色对比度（无障碍合规）
7. **第 11 条** - 浮层边界（窄屏可用性）

### 低优先级（优化或预防性）
8. **第 4 条** - 表格中间断点
9. **第 6 条** - 焦点指示器
10. **第 9 条** - landmark 一致性
11. **第 13 条** - autocomplete
12. **第 10 条** - 未使用的插槽（代码整洁）
13. **第 8 条** - 深色模式（取决于产品需求）

---

**建议**: 先修复第 1、3、12 条（合计 2.3 小时），它们会影响当前功能；再按性价比处理中优先级项目。
