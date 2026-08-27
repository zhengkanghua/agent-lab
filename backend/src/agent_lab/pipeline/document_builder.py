"""把 PostgreSQL 文档转换成 LangChain ``Document``。

就是将数据库自然语言内容转化为一个完整的Langchain文档

LangChain 的 ``Document`` 是进入 RAG 流程的「内存对象」——它不是数据库表，只是
在内存里把「正文 + 元信息」打包好，交给后续的 Chunk、Embedding、向量存储步骤用。
本模块不持久化业务状态。
"""

from langchain_core.documents import Document
from sqlalchemy import inspect

from agent_lab.models.document import DocumentRecord


class DocumentBuilder:
    """按统一规则把 ORM 记录转成 LangChain ``Document``。

    为什么需要这一层：如果每个调用方都自己拼 Metadata，很容易出现字段名、值类型
    或 ID 规则不一致的脏向量数据。这一个适配器把「ORM → RAG」的转换规则集中，
    保证全网只用一套规则。本类不访问数据库。

    LangChain ``Document`` 的三个核心部分：
    - ``page_content``：真正参与切分和 Embedding 的清洗后正文；
    - ``metadata``：给检索过滤、结果展示、回查原记录的结构化字段；
    - ``id``：复用 PostgreSQL 文档 UUID，建立「数据库记录 ↔ RAG 文档」的稳定关联。
    """

    def build(self, record: DocumentRecord) -> Document:
        """把一条已加载来源信息的 ORM 记录转换成 LangChain Document。

        调用方必须提前 eager-load ``record.source``。这个同步构建器不会发起
        数据库查询，也不会计算 Embedding。

        Args:
            record: ``documents`` 表对应的 ORM 实体。``source`` relationship 必须
                已加载并且非空，正文必须至少包含一个非空白字符。

        Returns:
            可交给 ``DocumentChunker`` 的 LangChain 文档。返回对象只存在于内存，
            调用本方法不会更新 ``processing_status`` 或写入任何数据库表。

        Raises:
            ValueError: 正文为空，``source`` 关系尚未加载，或关系值为空时抛出。
        """

        # 1. 正文必须非空：没有正文就无法切分/向量化
        content_text = record.content_text.strip()
        if not content_text:
            raise ValueError(
                "content_text 为空，无法构建 LangChain 文档。"
            )

        # 2. 要求调用方已 eager-load source 关系：异步 SQLAlchemy 下，若此时才触发
        #    懒加载，会在同步属性访问里发起隐式数据库 I/O，不安全
        state = inspect(record)
        if "source" in state.unloaded:
            raise ValueError(
                "构建 LangChain 文档前必须先预加载（eager load） "
                "DocumentRecord.source。"
            )
        source = record.source
        if source is None:
            raise ValueError(
                "DocumentRecord.source 在构建 "
                "LangChain 文档前必须包含来源。"
            )

        # 3. 组装 Metadata：关联类字段用于定位 DB/外部原记录；语义类字段用于过滤、
        #    展示和构造提示词。值限定为字符串或字符串列表，方便日后直接序列化成
        #    Qdrant payload，无需自定义编码
        metadata: dict[str, str | list[str]] = {
            # PostgreSQL 及外部系统关联字段。
            "document_id": str(record.id),
            "source_id": str(record.source_id),
            "source_provider": source.provider,
            "source_external_id": source.external_id,
            "document_external_id": record.external_id,
            "content_hash": record.content_hash,
            # 检索过滤与结果展示字段。
            "title": record.title,
            "source_name": source.name,
            "document_type": record.document_type.value,
            "url": record.url,
            "authors": list(record.authors),
            "labels": list(record.labels),
        }
        # 4. 缺失时间时省略 key 而不是写 None：让 payload 类型稳定，向量库做过滤时
        #    也不用同时判断「字段不存在」和「字段为 null」两种情况
        if record.published_at is not None:
            metadata["published_at"] = record.published_at.isoformat()
        if record.source_updated_at is not None:
            metadata["source_updated_at"] = record.source_updated_at.isoformat()

        # 5. 生成 Document：LangChain Embedding 默认只嵌入 page_content，metadata
        #    不会拼进向量输入，所以 UUID/URL 等字段可完整保留用于过滤和回查
        return Document(
            id=str(record.id),
            page_content=content_text,
            metadata=metadata,
        )
