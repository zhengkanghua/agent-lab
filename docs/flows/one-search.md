# 一次检索

跨前端 `pages/` → `features/semantic-search/` → `api/`，后端 `api/` → `services/` → `qdrant/`
加两个外部服务。本文只记跨模块顺序、两次请求的分工和失败边界。

## 用户感知的「一次检索」其实是两次请求

```
第一次  POST /document-search   拿到匹配片段和标题，不含正文
第二次  GET  /documents/{id}    用户展开某篇时才要正文
```

第一次请求**完全不碰 PostgreSQL**，只走 Ollama 加 Qdrant。这是刻意的：如果检索时顺手把
每篇正文查出来，20 条结果就是 20 次数据库查询（N+1），而用户通常只展开一两篇。

代价是列表页拿不到正文。要展示摘要就只能用 Qdrant payload 里已有的字段，不能临时回表。

## 第一次请求的链路

```
SearchPage.vue
  └─ useSemanticSearch          归一化 limit 参数
      └─ useSearchRequest       query 生命周期、AbortController、陈旧响应守卫
          └─ api/document-search.ts
              ═══ HTTP ═══
              api/document_search.py          Pydantic 校验
                └─ services/vector_search_service.py
                    ├─ Ollama    query 向量化 + 按索引规格校验向量
                    └─ qdrant/search.py       grouped query，一次只读查询
```

后端两步，顺序固定：**先向量化，再查 Qdrant**。向量化后还要对着当前索引规格
（维度、模型）校验一遍，不合就直接报错——避免用错模型的向量去查，那会返回看似正常
但完全不相关的结果。

## 排序和去重在后端

Qdrant 的 grouped query 按 `document_id` 分组，`document_limit` 控制返回几篇、
`matches_per_document` 控制每篇几个片段。结果按每篇最高分降序。

**前端不重排、不聚合、不二次去重**（`useSemanticSearch.ts` 的模块 docstring 有同样的注释）。前端再排一遍
的话，两边规则一有出入，用户看到的顺序就和后端算出来的不一致，而且很难查。

背景见 [`../adr/0001-backend-owns-result-uniqueness-and-order.md`](../adr/0001-backend-owns-result-uniqueness-and-order.md)。

## 快速连打的陈旧响应

`useSearchRequest.ts` 用两道机制防止旧响应盖掉新结果：

1. **AbortController**：发新请求前 abort 上一个。
2. **请求序号**：`useSearchRequest()` 的 docstring 记了一个 abort 管不到的窗口——请求已经
   resolve、`await` 还没恢复执行的那一瞬间，`abort()` 不再起作用，只能靠序号比对丢弃。

只做第一道会漏。这个窗口很窄但真实存在，用户连续输入时能碰到。

## 失败边界

| 出错的地方 | 用户看到 |
| --- | --- |
| 查询为空或超长 | 前端 `features/semantic-search/model/search-validation.ts` 直接拦，不发请求 |
| 参数不合法 | 422，后端 Pydantic |
| Ollama 挂了或超时 | 503 |
| Qdrant 挂了或响应契约非法 | 503 |
| 索引规格不匹配 | 503（配置问题，不是临时故障，重试无用） |

所有错误码到中文提示的映射只有一处：`frontend/src/api/error-copy.ts`。**新增错误码要在那里加**，
不要在组件里就地拼文案。

后端错误响应固定带 `code`、`detail`、`retryable` 三个字段，且**不回显 query 和上游细节**
（`main.py` 的异常处理器）。所以日志里看不到用户搜了什么，排查时别指望。

## 边界

- 只读检索。没有 LLM 生成回答、对话和流式返回。
- 检索不写任何库，不记录检索历史。
