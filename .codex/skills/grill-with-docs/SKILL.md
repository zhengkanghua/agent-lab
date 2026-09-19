---
name: grill-with-docs
description: A relentless interview to sharpen a plan or design, which also creates docs (ADR's and glossary) as we go.
disable-model-invocation: true
---

这个 skill 是「访谈 + 写文档」的组合入口：把 `grilling` 与 `domain-modeling` 两个 skill 一起用。

**先把两份都读进来**（按当前 harness 的方式：读下面的文件，或用 `/skill:<name>`）：

- [`../grilling/SKILL.md`](../grilling/SKILL.md)——怎么访谈：设计树、逐轮推进 frontier、事实由你自
  己查而不是问用户。
- [`../domain-modeling/SKILL.md`](../domain-modeling/SKILL.md)——什么时候写词条、什么时候立 ADR、
  写成什么样；格式细节见同目录的 `CONTEXT-FORMAT.md`、`ADR-FORMAT.md` 与 `FORMAT-CONTRACT.md`。

**两个 skill 的节奏不同，不要混着走。** `grilling` 是「一轮把所有已解锁的问题问完，等回答再推下一轮」；
`domain-modeling` 是「术语或决策一旦落地就立刻写，不攒批」。所以：访谈推进用前者的节奏，写文档用后者的
节奏——某个问题一有结论就当场落到 `CONTEXT.md` 或 `docs/adr/`，不要等整棵树走完再回头补。

**写入会被格式门禁校验。** `CONTEXT.md` 与 `docs/adr/**` 的写入由 `scripts/check-domain-docs.mjs` 按
`FORMAT-CONTRACT.md` 校验，不合规会被拒绝并回给你违反了哪条规则。被拦住时去读 skill，不要猜格式重试。
访谈结束前确认没有留下「讨论过但没写下来」的决策。
