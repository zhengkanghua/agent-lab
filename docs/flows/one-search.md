# 一次检索

跨前端 `pages/` → `features/semantic-search/` → `api/`，后端 `api/` → `services/` → `qdrant/`
以及 PostgreSQL、Ollama 和 Qdrant。本文只记跨模块顺序、两次请求的分工和失败边界。

## 用户感知的「一次检索」其实是两次请求

```
第一次  POST /document-search   拿到匹配片段和标题，不含正文
第二次  GET  /documents/{id}    用户展开某篇时才要正文
```

第一次请求先用 PostgreSQL 解析实际启用 KnowledgeBase 范围，再走 Ollama 和 Qdrant。
搜索读取可用版本快照，并在向量查询后批量核验文档状态；不逐篇回查正文。用户打开全文时才读取已采用正文。

代价是列表页拿不到正文。要展示摘要就只能用 Qdrant payload 里已有的字段，不能临时回表。

## 第一次请求的链路

```
SearchPage.vue
  ├─ SearchComposer.vue        顶部常驻输入条（数量参数的默认值在设置中心维护，入口图标链回设置）
  ├─ SearchRecordTurn.vue      单条检索记录（折叠标题行 + 展开内容）
  └─ useSearchStream           多轮累积、单活动请求、陈旧响应守卫
      └─ api/document-search.ts
          ═══ HTTP ═══
          api/document_search.py          Pydantic 校验
            └─ services/vector_search_service.py
                ├─ KnowledgeBaseScope    解析所有启用库或非空集合；旧 HTTP 缺省 news
                ├─ Ollama    query 向量化 + 按索引规格校验向量
                └─ knowledge/visibility.py    可用实例过滤及查询后状态核验
                    └─ qdrant/search.py       grouped query；状态变化时有界重查
```

检索页重构后把每次搜索追加成一条「检索记录」形成向下长的检索流（最新贴顶、旧记录折叠、
刷新即清空），但**单条记录的那次 `/document-search` 请求链路不变**——本图即单次检索的请求
链。不再有「按片段」模式，前端只走按 Document 分组。

新页面明确发送 `scope`，新响应包含实际范围快照及结果；旧 HTTP 不带 scope 时保持 news 缺省
和数组响应。每条检索记录保留提交选择、实际集合和名称，随后改选或改名不重写已有记录。
Agent 复用范围解析，但选择保存在会话中，每次运行冻结后只允许 Tool 进一步缩小。

后端先核验知识库并向量化，再按可用版本查询 Qdrant、批量核验结果。向量化后还要对着当前索引规格
（维度、模型）校验一遍，不合就直接报错——避免用错模型的向量去查，那会返回看似正常
但完全不相关的结果。

候选写入、采用、拒绝、删除和重建可能与查询并发。后端只返回当前可用的已采用实例；状态漂移时
重新查询，无法取得一致结果时返回可重试失败。不能仅在结果末尾删掉无效项而让它们占用文档名额。

## 排序和去重在后端

Qdrant 的 grouped query 先按独立索引实例分组，后端核验当前可用性后投影成每篇 Document 的结果。
`document_limit` 控制返回几篇，`matches_per_document` 控制每篇几个片段。结果按每篇最高分降序。

**前端不重排、不聚合、不二次去重**（`features/semantic-search/model/search-result.ts` 的
`toNewsDocumentResults` 注释有同样的说明）。前端再排一遍的话，两边规则一有出入，用户看到的
顺序就和后端算出来的不一致，而且很难查。

背景见 [`../adr/0001-backend-owns-result-uniqueness-and-order.md`](../adr/0001-backend-owns-result-uniqueness-and-order.md)。

## 快速连打的陈旧响应

`useSearchStream` 用两道机制防止旧响应盖掉新结果，并**只允许一条在途搜索**：

1. **AbortController**：提交新搜索前 abort 上一条；被中途放弃的 loading 占位轮会从检索流里
   移除，不留在界面上空转。
2. **请求序号**：记了一个 abort 管不到的窗口——请求已经 resolve、`await` 还没恢复执行的那
   一瞬间，`abort()` 不再起作用，只能靠序号比对丢弃。

只做第一道会漏。这个窗口很窄但真实存在，用户连续换词输入时能碰到。

## 失败边界

| 出错的地方 | 用户看到 |
| --- | --- |
| 查询为空或超长 | 前端 `features/semantic-search/model/search-validation.ts` 直接拦，不发请求 |
| 参数不合法 | 422，后端 Pydantic |
| 空选择、目录加载失败 | 页面阻止提交并提供重新选择或刷新入口 |
| KnowledgeBase 不存在或停用 | 分别为 404 或 409，不请求向量服务 |
| 没有启用 KnowledgeBase | 409，不请求向量服务 |
| Ollama 挂了或超时 | 503 |
| Qdrant 挂了或响应契约非法 | 503 |
| 索引规格不匹配 | 503（配置问题，不是临时故障，重试无用） |

错误码到中文提示的**判定顺序**只有一处：`frontend/src/api/error-copy.ts` 的 `resolveErrorCopy`
（code → status → 兜底）。**文案表在各自领域文件里**，检索这条链路的是
`features/semantic-search/model/search-error.ts`。新增错误码时往表里加，不要在组件里就地拼文案，
也不要另写一条 if-else 判定链。

后端错误响应固定带 `code`、`detail`、`retryable` 三个字段，且**不回显 query 和上游细节**
（`main.py` 的异常处理器）。所以日志里看不到用户搜了什么，排查时别指望。

## 边界

- 本文这条链路只读检索，不生成回答：`/document-search` 只返回检索到的原文片段，前端也只在
  页内做多轮检索流展示。`/vector-search` 后端仍保留，但前端检索页不再调用它。模型作答是另一条
  链路（`POST /agent/chat`，SSE），它复用同一个 `VectorSearchService.search_documents` 作为工具，但走不同的
  路由、不同的权限（仅超级用户）和不同的响应形状。
- 检索页的多轮「检索流」是纯页面状态：记录只在内存里向下累积，刷新或离开即清空，不写后端、
  不留库。Agent 的 Tool 不修改 Document 或 Qdrant；会话归属与选择范围写 `agent_threads`，
  消息和证据关系写 checkpointer（ADR 0003、0004、0021）。
