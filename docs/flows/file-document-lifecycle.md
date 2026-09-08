# 文件资料的保存、索引与删除

超级用户在 `/admin/files` 选择启用 KnowledgeBase，上传文本或 Markdown。页面通过同域 `/api/file-documents` 进入文件应用用例，解析通过后在 PostgreSQL 创建独立 Document；同名上传不会覆盖其他记录。Markdown 保存可读原文，索引阶段派生文本，不下载外链图片。

保存返回后，页面展示待处理状态。既有索引任务认领 Document、构造快照、生成 Chunk 并写 Qdrant，最后按 revision 条件确认成功。用户在索引完成后从普通检索或 Agent 取得资料；此前可以直接从文件列表读取当前全文。等待、处理中或失败均不能显示成已可检索。

替换必须从列表选定 Document，以其 ID 和 revision 提交新文件。目标 ID 和归属保持；完全相同的内容及可索引元数据不推进 revision，也不重新排队。并发冲突时后端拒绝覆盖，页面关闭旧编辑对象、刷新列表并要求重新选择；解析失败保留原记录。索引失败重试只重新排队原 Document。

删除由明确 ID 发起，复用持久写协调。先记录人工删除待办，再向 Qdrant 确认删除该 Document 的 Point，最后按目标 revision 删除 PostgreSQL 记录并完成待办。远端失败或数据库收尾失败时保留恢复信息，页面仍显示记录和继续删除入口；只有两侧都确认后才报告成功。定时清理不接管人工待办，也不扩大默认目标。

| 失败位置 | 可观察结果与恢复 |
| --- | --- |
| 文件格式、编码、正文或大小不合法 | 保存前拒绝，既有文档不变 |
| 未登录或非超级用户 | 服务端拒绝管理请求 |
| 目标知识库停用 | 禁止新增、替换和读取全文，已有资料保留供维护 |
| 上传响应超时 | 结果未确认；先刷新列表，页面不自动重复上传 |
| 索引失败 | 保留 Document 和错误状态，可重试同一记录 |
| 替换版本冲突 | 重新读取当前记录后再决定是否替换 |
| Qdrant 或 PostgreSQL 删除未确认 | 保留待办与记录，可从同一目标继续删除 |

接口、参数与错误码以 OpenAPI 和 `backend/src/agent_lab/api/file_documents.py` 为准；验证入口为 `test_file_documents.py`、`test_file_documents_integration.py` 和前端 `FileDocumentDirectory.spec.ts`。集成测试创建隔离资源，需要明确服务授权，离线替身不代表真实跨存储验收。
