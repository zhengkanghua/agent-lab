# 格式契约（机器可执行的那一份）

[SKILL.md](./SKILL.md) 讲的是**为什么**要这样写领域模型，[ADR-FORMAT.md](./ADR-FORMAT.md) 与
[CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md) 讲的是**怎么写**。本文件是第三件事：把「写成什么样算合规」
变成机器能判断的规则，好让仓库门禁（`scripts/check-domain-docs.mjs`）和四个 harness 的 hook
按同一份定义执行。

**本文件是规则的唯一定义处。** 门禁脚本从下面那个 ```json 块读数据，逻辑写在脚本里；散文只解释
每条规则拦的是什么故障。改规则改这里一处，四个 harness 和 git hook 一起生效。散文与本节冲突时，
以本节为准——因为门禁实际执行的是它。

## 为什么需要它

这三条不是设想出来的，是仓库里已经发生的事：

**状态变更没有机械表达。** 26 份 ADR 里 0 份用 frontmatter，取而代之的是四种互不兼容的写法：
`~~划线~~` 加顶部横幅（0018，7 处）、`## 追加（日期）` 节（0007、0011）、「位置更新」横幅（0025）、
「部分被取代……原文保留不改」（0027）。读者无法机械判断一份 ADR 今天还有效没有，只能通读全文。
skill 给的正解一直是 frontmatter 里的 `Status`。

**章节名各自发明。** 统计 26 份的二级标题：`Considered Options` 16 次、`Consequences` 19 次，
另有 `决策 (Decision)`、`后果`、`后果 (Consequences)`、`考虑过的方案`、`上下文 (Context)`、
`Context` 各若干，以及 37 个自造章节名（`拆掉了什么：16 处约束`、`设计消融`、`迁移与验收要求`……）。
同一套节名出现三种语言写法，说明没有人在守格式；后果是「这份 ADR 的代价写在哪个标题下」不可预测。

**编号是引用锚点。** `docs/adr/` 的编号被全仓 20 多个文件引用，重复编号或格式不符会让引用指向
错误的对象。0016 的标题还写着 `# ADR 0016: …`，与文件名重复。

## 契约

```json
{
  "version": 1,
  "adr": {
    "dir": "docs/adr",
    "filenamePattern": "^[0-9]{4}-[a-z0-9]+(-[a-z0-9]+)*\\.md$",
    "frontmatter": {
      "required": ["status"],
      "optional": ["superseded-by"],
      "statusValues": ["proposed", "accepted", "deprecated", "superseded"]
    },
    "title": {
      "h1Count": 1,
      "forbiddenPrefixPattern": "^ADR\\s*[0-9]{4}\\s*[:：]"
    },
    "sections": {
      "allowed": ["Considered Options", "Consequences"]
    },
    "limits": {
      "warnLines": 60
    }
  },
  "context": {
    "file": "CONTEXT.md",
    "entryPattern": "^\\*\\*(.+?)\\*\\*[：:]\\s*$",
    "limits": {
      "warnDefinitionLines": 5
    }
  }
}
```

## 每条规则拦的是什么

**`frontmatter.required: ["status"]`** —— 拦「读者不知道这份 ADR 还有效没有」。取值限定四个：
`proposed`（还没定）、`accepted`（当前有效）、`deprecated`（不再推荐但仍记录）、
`superseded`（被取代）。**部分被取代仍然写 `accepted`**，另用 `superseded-by` 列出取代它的编号，
正文里说明哪几处失效——`superseded` 留给整份失效的情况。

**`frontmatter.optional: ["superseded-by"]`** —— 值是被取代它的 ADR 编号数组，每个编号必须
**真实存在于 `docs/adr/`**。这条交叉校验拦的是悬空引用：删掉一份 ADR 时忘了改指向它的那些。

**`title.h1Count` / `title.forbiddenPrefixPattern`** —— 拦「标题与文件名重复」「一篇里两个 H1」。
标题写决策本身，不写编号。

**`sections.allowed`** —— 只允许 `## Considered Options` 与 `## Consequences`，两者都可选。
skill 的判据是「标题 + 1–3 句话」，可选节只在**确实加价值**时才写：被否掉的替代方案值得记住时写
Considered Options，有非显然的下游影响时写 Consequences。多数 ADR 一个可选节都不需要。其余内容
（背景、为什么、术语澄清）写进正文段落，不另开标题——自造标题是上面统计里那 37 个的来源。

**`limits.warnLines`** —— 只警告不拦。ADR 的价值在记住「为什么」，压到某个行数以下会逼着人删掉
原因；但长到 150 行以上时，决策本身已经被施工细节淹没了（0019 现在 153 行）。

**`context.entryPattern`** —— 词条形如 `**术语**：`，一行词条名、随后是定义。术语表是词汇表，
不是规格：定义只说「是什么」，决策与取舍属于 ADR。

## 门禁怎么用它

写入 `CONTEXT.md` 或 `docs/adr/` 下任何文件前，门禁会读本文件并按上面的规则校验**将要写入的内容**。
不合规时拒绝写入，并把违规的那条规则连同 `SKILL.md` 的路径一起回给模型——被拒绝的模型应该做的
第一件事是读 skill，而不是猜格式。

`scripts/check-domain-docs.mjs` 是同一个校验的独立入口，供本地提交前检查和人工排查使用。
