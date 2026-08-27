# 阶段 1：通过 Ollama 生成 Embedding（已完成）

本文记录阶段 1 的实现和边界。阶段 2 已把本阶段的内存向量交给 Qdrant 保存；当前
Qdrant 概念和索引状态请继续阅读 [`02_qdrant_concepts.md`](02_qdrant_concepts.md)
与 [`03_document_indexing_pipeline.md`](03_document_indexing_pipeline.md)。阶段 3 已用
本阶段的 ``embed_query`` 接入只读 Vector Search，见
[`04_vector_search.md`](04_vector_search.md)。

## 本阶段目标和非目标

本阶段把阶段 0 得到的 LangChain Chunk ``Document.page_content`` 交给远程 Ollama
中的 ``bge-m3:567m``，得到 ``list[float]`` 向量，并在内存中保留 Chunk ID 与向量
的对应关系：

```text
DocumentRecord
  -> DocumentBuilder
  -> LangChain Document
  -> DocumentChunker
  -> LangChain Chunk Document.page_content
  -> OllamaEmbeddingProvider
  -> Ollama / bge-m3:567m
  -> ChunkEmbedding(chunk_id, embedding)
```

这里特意止于内存结果。本阶段不安装或连接向量数据库，不创建 collection，不保存
向量，不实现相似度检索，不改变 ``processing_status``，也不接入生成式 LLM、
Retriever、Agent 或 RAG 问答。把模型调用与存储拆开后，响应错误不会污染未来的
向量库，也能分别判断问题来自切分、Embedding 还是存储。

## Embedding 的直观解释

Embedding 可以理解为模型给一段文本生成一个“语义坐标”。例如“央行下调利率”和
“货币政策进一步宽松”使用同一模型计算后，向量通常比“今天的足球赛结果”更接近。
这种接近是模型从训练数据中学到的统计关系，不是关键词完全相同才成立。

Embedding 不是摘要，也不是答案。它输出的是数值数组，不能复述新闻内容，不能证明
事实正确，也不会自行回答用户问题。后续检索只是使用这些数值找到可能相关的 Chunk。

## 文本、token、向量和维度

- 文本是系统送入模型的原始字符串。当前 document embedding 只使用 Chunk 的
  ``page_content``，不会自动拼入 ID、URL 或 Metadata。
- token 是模型处理文本时使用的片段单位。一个 token 不一定是一个汉字或一个英文
  单词；阶段 0 按 token 控制 Chunk 大小，是为了限制模型单次输入规模。
- 向量是按固定顺序排列的一组数，例如 ``[0.013, -0.208, ...]``。这里只展示形式，
  代码和日志不应输出完整生产向量。
- 维度是一个向量包含的坐标数量，也就是 ``len(vector)``。同一向量空间中的所有
  向量必须同维，才能执行距离或相似度计算。

模型资料可能描述常见维度，但部署模型的 tag、量化或服务配置可能变化。因此
``probe_dimension()`` 使用一次真实响应的长度探测维度，Provider 也记录首次成功返回
的维度；代码绝不把 BGE-M3 的维度硬编码成永久事实。

## BGE-M3、Ollama 与 LangChain 各自做什么

``bge-m3:567m`` 是当前实际执行文本到向量映射的 Embedding 模型。所有 document 和
query 都必须使用这个相同模型及兼容处理规则，才能落在同一个向量空间。若文档用
模型 A、查询用模型 B，即使向量长度碰巧相同，坐标含义也通常不同，距离不可比较。

Ollama 是模型运行与 HTTP 服务层。项目把文本和模型名发送给 Ollama，由它加载模型、
执行推理并返回向量。Ollama 在这里不负责 Chunk、业务 Metadata、数据库事务或检索。

官方 ``langchain-ollama`` 的 ``OllamaEmbeddings`` 是 LangChain 与 Ollama HTTP API
之间的集成。当前锁定版本提供原生 ``aembed_query`` 和 ``aembed_documents``，底层
使用异步 Ollama 客户端。项目的 ``OllamaEmbeddingProvider`` 再封装配置、批处理、
输入验证、错误分类、响应验证和 Chunk ID 映射，避免业务 Service 到处创建客户端。

## document embedding 与 query embedding

document embedding 表示将被搜索的资料，本项目对应新闻 Chunk；query embedding
表示用户未来输入的检索问题。它们的角色不同，所以 LangChain 提供两个方法：

```text
await provider.embed_documents([chunk_1.page_content, chunk_2.page_content])
await provider.embed_query("哪些新闻提到利率调整？")
```

当前 Ollama 集成最终都调用同一模型的 embed API。区分方法仍然有价值：调用方的意图
更清楚，也为模型将来支持 query/document 特定前缀保留正确边界。本阶段没有自动添加
前缀，因为当前部署没有给出这种契约，擅自添加会改变向量空间。

相似文本的向量“通常”更接近，是模型训练目标带来的结果，不是严格规则。语言歧义、
否定、数字、领域术语、长文本截断和模型能力都会影响距离。因此向量不能直接当作
人类可读含义：单个坐标通常没有稳定的自然语言解释，必须把整组坐标作为整体比较。

## batch size、timeout 与远程负载

``OLLAMA_EMBEDDING_BATCH_SIZE`` 决定一个 HTTP 请求最多携带多少条 document 文本。
Provider 使用它把输入顺序切成连续批次，再按原顺序合并结果：

```text
[0, 1, 2, 3, 4]  batch_size=2
    -> request [0, 1]
    -> request [2, 3]
    -> request [4]
    -> result  [0, 1, 2, 3, 4]
```

较大批次通常能减少 HTTP 往返、提高吞吐，但会增加单次计算时间、服务端内存或显存
占用、反向代理请求体大小以及超时概率。较小批次对单次请求更保守，却增加网络开销和
总请求数。``OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS`` 是单批请求的等待上限，不是
整个多批任务的总时限。默认 16 和 120 秒只是初始值，应根据真实文本长度和服务负载
测量后调整。

当前官方调用链没有为本方法暴露需要项目叠加的自动重试策略，Provider 也没有自行
重试。这样认证失败、模型不存在和非法输入不会重复请求，暂时性错误也不会被多层重试
放大。若生产观测证明需要重试，应只覆盖连接中断或明确的暂时性 5xx，并设置有限次数
与退避上限，同时先核实客户端和反向代理是否已重试。

## 为什么必须验证响应

“HTTP 成功”不代表响应可以安全进入向量系统，Provider 依次检查：

1. 返回向量数量必须等于输入文本数量，否则无法保持一一映射。
2. 每个向量必须非空；空数组没有可用维度，也无法计算距离。
3. 每个坐标必须是数值，且拒绝容易被 Python 当作整数的布尔值。
4. 每个坐标必须有限，拒绝 ``NaN``、正 ``Infinity`` 和负 ``Infinity``；这些值会
   污染距离计算、排序和序列化。
5. 同一批内所有向量维度必须一致。
6. 不同批次以及同一 Provider 生命周期内的维度也必须一致，避免模型配置漂移后把
   两个向量空间混在一起。

验证成功后只返回内存对象。阶段 1 不保存向量，是因为还没有定义 collection、距离
算法、payload 和幂等写入契约；临时写文件或 PostgreSQL 新表只会产生另一份无生命周期
管理的数据副本。

## 代码调用流程

```text
调用方
  |
  | Sequence[LangChain Chunk Document]
  v
OllamaEmbeddingProvider.embed_chunks()
  |-- 先检查每个 Chunk.id
  |-- 只提取 page_content
  v
embed_documents()
  |-- 在网络前拒绝空/纯空白正文
  |-- 空列表立即返回 []
  |-- 按 OLLAMA_EMBEDDING_BATCH_SIZE 顺序分批
  v
OllamaEmbeddings.aembed_documents()
  |
  v
远程 Ollama /api/embed -> bge-m3:567m
  |
  v
数量、非空、有限数值、同批/跨批维度校验
  |
  v
list[ChunkEmbedding]（仅内存，不持久化）
```

``embed_query()`` 使用相同配置和验证规则处理一条 query。``probe_dimension()`` 通过
一次真实 query 调用返回 ``len(vector)``，而不是读取常量。

## 配置与密钥安全

示例配置位于 ``.env.example``：

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| ``OLLAMA_BASE_URL`` | ``https://ollama.example.com`` | Ollama HTTP API 根地址 |
| ``OLLAMA_EMBEDDING_MODEL`` | ``bge-m3:567m`` | query/document 共用模型 |
| ``OLLAMA_API_KEY`` | 空 | 可选的反向代理密钥 |
| ``OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS`` | ``120`` | 单批请求超时秒数，必须大于 0 |
| ``OLLAMA_EMBEDDING_BATCH_SIZE`` | ``16`` | 每批最大文本数，必须大于 0 |

独立 ``OllamaEmbeddingSettings`` 使用 ``AnyHttpUrl`` 校验 URL，清理并校验非空模型名，
使用正数约束校验 timeout 和 batch size。``OLLAMA_API_KEY`` 使用 ``SecretStr``，空值
合法。不要读取、打印或提交本地 ``.env``；``.gitignore`` 已忽略它。

Ollama 原生 API 没有规定 API Key 认证协议。当前反向代理方式无法从项目本身确认，
所以非空密钥暂按常见 Bearer ``Authorization`` header 处理，且构造集中在
``config/ollama_embedding.py::build_ollama_headers``。若代理实际要求 ``X-API-Key``
或其他格式，只应修改这一处。Provider 会清除官方集成对象中可被 ``repr`` 显示的
header 参数副本；错误映射也不转发服务端可能包含密钥的正文。

## 离线测试

默认测试注入 fake Embeddings，不连接网络，也不要求 API Key：

```powershell
uv sync --all-groups
uv run pytest -q
```

测试会记录 fake 收到的批次，验证空输入零调用、配置 batch size 真正生效、跨批顺序
不变，并注入数量错误、空向量、非数字、NaN/Infinity、同批/跨批维度差异、超时、
连接失败、认证失败和模型不存在。认证异常断言密钥不出现在 ``str`` 或 ``repr`` 中。
现有 document pipeline 测试也同时运行，用于发现阶段 0 回归。

## 可选真实服务验证

只有明确设置开关时，集成测试才读取本地 ``.env`` 并访问 Ollama：

```powershell
$env:RUN_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_ollama_embedding_integration.py
```

它只发送“这是一个向量测试”等短小无敏感文本，分别调用 query 和批量 document 接口，
验证向量非空、全部有限且维度一致。测试不假设固定维度，不打印密钥或完整向量，不会
修改服务端数据。若服务要求尚未配置的认证，预期结果是明确认证失败，而不是伪造成功。

## 常见故障与排查

### 配置校验失败

检查 URL 是否含 ``http://`` 或 ``https://``，模型名称是否为空，timeout 与 batch size
是否为正数。配置错误发生在网络请求之前。

### 认证失败

401/403 会转换为 ``OllamaAuthenticationError``。确认密钥是否已放入本地 ``.env``，
再向反向代理维护者确认 header 名称和格式。不要把密钥粘贴到日志、Issue 或测试断言。

### 连接失败

``OllamaConnectionError`` 表示 DNS、TCP、TLS、代理或服务可达性问题。先检查
``OLLAMA_BASE_URL``，再从相同运行环境验证域名解析和 HTTPS 证书；浏览器能访问不代表
服务进程所在网络也能访问。

### 请求超时

``OllamaTimeoutError`` 表示单批超过 timeout。检查服务负载和文本长度，可先减小 batch
size，再基于观测适度增加 timeout。不要无限增大超时掩盖服务端资源不足。

### 模型不存在

404 会转换为 ``OllamaModelNotFoundError``。确认 ``bge-m3:567m`` 的 tag 完全一致，
并确认请求到的是预期 Ollama 实例。认证代理也可能用 404 隐藏资源，此时需结合代理
配置判断。

### 数量、数值或维度错误

这类错误说明响应不满足向量契约。记录错误类别、批次大小、模型名和时间即可，不要
记录完整文本、完整向量或密钥。确认代理是否改写响应、服务是否滚动切换模型；修复前
不要跳过校验或保存部分结果。

## 本阶段完成标准

- 独立 Settings 校验 URL、模型、可选秘密、timeout 和 batch size。
- 官方 Ollama 集成使用原生异步 API，客户端只在 Provider 中创建。
- query、document、Chunk 和维度探测入口均可用。
- 空文本在网络前拒绝，空列表零调用，配置批次真实生效且顺序稳定。
- 数量、空向量、非有限数值及同批/跨批维度全部验证。
- 认证、连接、超时、模型不存在和响应契约错误可区分且不泄露密钥。
- 默认测试完全离线，可选真实测试只读、显式开启且不假设固定维度。
- 向量只在内存返回，没有新增数据库表或后续阶段组件。

## 阶段 2 已确认的衔接方案

Qdrant 阶段使用本阶段真实探测到的 1024 维向量，配置 ``Cosine`` Distance metric，
复用稳定 Chunk UUID 作为 Point ID，并通过 current Alias 写入物理 Collection。新闻
时间 ``published_at``、正文 hash、来源和 Chunk 关系进入 Payload；完整单篇 upsert、
旧 Chunk 清理、revision 和 ``processing_status`` 说明见
[`03_document_indexing_pipeline.md`](03_document_indexing_pipeline.md)。
