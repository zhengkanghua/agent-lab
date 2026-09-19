---
status: accepted
---

# 检索结果的唯一性与顺序由后端保证

文档聚合、去重和排序全部由后端完成，前端不做伪分组也不重排。**两边各算一遍必然漂移**，所以前端曾经
那一遍去重被删掉了。保证点在 `backend/src/agent_lab/qdrant/search.py` 的 `search_groups`：按
`index_instance_id` 分组让每组只出一条结果，用 `seen_instance_ids` 显式判重（重复即抛
`QdrantSearchResponseError`），排序键取 `(-score, str(document_id))`。取负分数而不是 `reverse=True`，
是因为后者会把 document_id 也一起翻成 Z→A，而混合方向（第一键降序、第二键升序）只能靠给分数取负号
实现；第二个键的意义是确定性：两组最高分浮点相等时按文档 ID 字典序固定顺序，保证同样输入永远产出
同样输出，测试可断言、分页不跳动。

**分组键是「索引实例」而不是文档。** 同一 Document 在 Qdrant 里可能同时存在多份正文实例（正在采用的
候选、已退休的旧版），按 `document_id` 分组会把不同版本的 Chunk 混进同一组，所以改按实例分组，保证
一组内的 Chunk 来自同一份正文。`document_id` 仍是 Payload 字段、仍是最终结果的文档身份，也仍是排序键
的第二段，只是不再是分组键。**随之而来的是文档级唯一性的把关点挪了一层**：`search_groups` 只保证
「同一实例不出两组」，「同一 Document 只出一条结果」由外层的 `AdoptedVectorSearch._query()` 在核验
当前正式指向之后判定，不满足即抛 `SearchVisibilityError` 并触发有界重查——因为跨实例去重必须知道
哪个实例才是当前正式版本，那是 PostgreSQL 才知道的事，Qdrant 适配层拿不到。本文「唯一性与顺序由后端
保证、前端不重算」的立场不变。

**查这类不变量时别只 grep `schemas/`。** 校验逻辑在 Qdrant 适配层，不在 Pydantic 模型上。曾因只搜
`schemas/document_search.py` 没找到列表级校验器，就断言「后端不拒绝重复」，把一句本来正确的注释改错了。

## Considered Options

**前端再做一遍去重。** 曾经就是这么写的。但它过滤不掉任何东西（`group_by` 已保证唯一，且
`document_id` 是 UUID、序列化恒为小写），一旦后端分组真的出问题，它会让页面显示的篇数少于后端返回
条数——不报错、不提示，安静少给，把 bug 藏进显示层，排查时会先怀疑后端。

**排序用 `reverse=True` 加复合键。** 表达更直白，但会把两个键的方向一起翻转，做不到「分数降序、
document_id 升序」。

## Consequences

删除这类前端计算前先跑测试，确认有没有测试正在保护错误行为：当时 `SearchResults.spec.ts` 断言「两条
同一 document_id 只渲染 1 张卡」，把违规锁进了门禁。正确顺序是先删代码让它变红（证明去重是活的），
再改测试用两篇不同文档验证条数与顺序透传。

Chunk 模式下的多 Chunk 断言不要跟着一起删：`POST /vector-search` 是 Chunk 级契约，同一 document 的
多个 Chunk 分别出现是合法的，那不是漏了去重；文档级分组走 `POST /document-search`。检索页重构后前端
已去掉「按片段」模式（见 [ADR 0013](0013-search-page-multi-round-record-stream.md)），后端
`/vector-search` 的 Chunk 级契约与单测保留。
