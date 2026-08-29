# 检索结果的唯一性与顺序由后端保证

文档聚合、去重和排序全部由后端的 Qdrant grouped query 完成，前端不做伪分组也不重排。
两边各算一遍必然漂移，所以前端曾经那一遍去重被删掉了。本文记录保证点在哪里、以及为什么删。

## 后端的保证点

在 `backend/src/agent_lab/qdrant/search.py` 的 `search_groups`：

- `group_by="document_id"`，每组只出一条结果
- 用 `seen_document_ids` 显式判重，重复即抛 `QdrantSearchResponseError`
- 排序键 `(-score, str(document_id))`，即最高分降序 + document_id 升序；第二个键是为了
  最高分浮点相等时顺序仍然确定

查这类不变量时别只 grep `schemas/`。校验逻辑在 Qdrant 适配层，不在 Pydantic 模型上。
曾因只搜 `schemas/document_search.py` 没找到列表级校验器，就断言「后端不拒绝重复」，
把一句本来正确的注释改错了（`8747d8d` 引入，`c5cab26` 修回）。

## Consequences

前端那一遍去重过滤不掉任何东西（group_by 已保证唯一，且 `document_id` 是 UUID，序列化恒为
小写），但一旦后端分组真的出问题，它会让页面显示的篇数少于后端返回条数——不报错、不提示，
安静少给，把 bug 藏进显示层，排查时会先怀疑后端。这是留着它的真实代价。

删除这类前端计算前先跑测试，确认有没有测试正在保护错误行为。当时 `SearchResults.spec.ts`
断言「两条同一 document_id 只渲染 1 张卡」，把违规锁进了门禁。正确顺序是先删代码让它变红
（证明去重是活的），再改测试用两篇不同文档验证条数与顺序透传。

chunk 模式下的多 Chunk 断言不要跟着一起删：`POST /vector-search` 是 Chunk 级契约，同一
document 的多个 Chunk 分别出现是合法的，那不是漏了去重。文档级分组走
`POST /document-search`。
