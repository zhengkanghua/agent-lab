# 正文过期由前端比对两个 content_hash 判定

检索结果带的 `content_hash` 是 Qdrant 已索引版本，全文详情带的是 PostgreSQL 当前版本，两者不等即表示
正文在被索引之后又改过。这个判定放在前端：由消费方自己比对两个值，不等就在阅读器里提示「正文已更新」。
后端不判过期、不返回过期标志、也不拒绝返回过期结果。

## Considered Options

**后端返回 `stale` 标志位。** `documents` 表同一行里既有 `content_hash` 也有 `indexed_content_hash`，
单行就能判过期，看着比前端比对更省事。但检索路径拿不到这一行：`services/vector_search_service.py` 的职责
是「不读取或修改 PostgreSQL」，检索是纯 Qdrant 读取。加标志位等于让每条检索结果都回查一次 PostgreSQL，
破掉那条模块边界，换来的只是把一次比较从前端移到后端。

**后端拒绝返回过期结果。** 副作用是刚更新过正文的新闻在索引跟上之前会彻底搜不到，用户无从判断是没有这篇
还是索引在追。给出旧版本并明确提示，比静默少给更可用。

## Consequences

判定责任在消费方：每个要打开全文的入口都得自己比一遍 `content_hash`。目前只有一个消费方
（`features/semantic-search/composables/useDocumentReader.ts` 的 `contentHashMismatch`，渲染在
`components/DocumentReader.vue`）。第二个入口出现时要么复用它，要么重新实现一遍——漏掉不会报错，只会安静地
不提示，用户看着旧正文以为是最新的。
