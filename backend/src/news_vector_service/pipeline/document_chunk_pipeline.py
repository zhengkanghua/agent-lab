"""串接「持久化文档 → LangChain Chunk」的内存处理流程。

本模块是供 Service 或任务执行器调用的稳定门面（facade）：它把两个环节——
DocumentBuilder（ORM→Document）和 DocumentChunker（Document→Chunk）——按顺序
串好。调用方只需依赖这个门面，不用关心 LangChain 切分器怎么构造。

关键约束：它只做内存转换，数据库查询、事务、Embedding、Qdrant 写入都由上层做，
避免一个同步方法暗中产生 I/O。


DocumentRecord (SQLAlchemy/数据库)
   ↓ DocumentBuilder
LangChain Document (完整)
   ↓ DocumentChunker
LangChain Document Chunk (多个，可检索)
   ↓ (再交给 embedding_provider)
"""

from langchain_core.documents import Document

from news_vector_service.models.document import DocumentRecord
from news_vector_service.pipeline.document_builder import DocumentBuilder
from news_vector_service.pipeline.document_chunker import DocumentChunker


class DocumentChunkPipeline:
    """按 ``DocumentRecord → Document → Chunk[]`` 顺序处理文档。

    调用方只依赖该门面即可完成「ORM 文档 → Chunk 列表」的转换，既不用了解
    LangChain 切分器的构造细节，也不必分别调两个组件。构建和切分仍由两个职责单一
    的组件完成，便于分别测试 Metadata 映射规则和 Chunk 策略。
    """

    def __init__(
        self,
        *,
        document_builder: DocumentBuilder | None = None,
        document_chunker: DocumentChunker | None = None,
    ) -> None:
        """创建文档 Chunk 流水线。

        Args:
            document_builder: ORM 到 LangChain Document 的转换器。未传入时使用
                项目标准 ``DocumentBuilder``。
            document_chunker: 完整文档切分器。未传入时使用默认 512/96 token
                配置。允许注入主要用于测试和确有需要的自定义切分参数。

        Notes:
            使用 ``is None`` 的语义比依赖对象真假值更明确；当前组件没有自定义
            布尔行为，因此 ``or`` 可用，但显式判断更利于以后替换实现。
        """

        self._document_builder = (
            document_builder if document_builder is not None else DocumentBuilder()
        )
        self._document_chunker = (
            document_chunker if document_chunker is not None else DocumentChunker()
        )

    @property
    def chunk_size(self) -> int:
        """返回当前 Pipeline 实际使用的 Chunk token 上限。"""

        return self._document_chunker.chunk_size

    @property
    def chunk_overlap(self) -> int:
        """返回当前 Pipeline 实际使用的相邻 Chunk token 重叠上限。"""

        return self._document_chunker.chunk_overlap

    @property
    def encoding_name(self) -> str:
        """返回当前 Pipeline 实际使用的 tokenizer 编码名称。"""

        return self._document_chunker.encoding_name

    def build_chunks(self, record: DocumentRecord) -> list[Document]:
        """把一条已加载来源关系的 ORM 文档转换为 Chunk 列表。

        Args:
            record: ``documents`` 表实体。调用方查询时必须提前加载 ``source``
                relationship；本同步方法不会补发数据库查询。

        Returns:
            按原文顺序排列的 LangChain Chunk Document 列表。ID、父子关系和
            Metadata 契约由 ``DocumentBuilder`` 与 ``DocumentChunker`` 保证。

        Raises:
            ValueError: 正文、来源关系、文档 UUID 或切分参数不满足下游要求时
                原样向调用方传播。
        """

        # 1. ORM 记录 → LangChain Document（组装正文稳定 ID 和 Metadata）
        document = self._document_builder.build(record)
        # 2. Document → Chunk[]（按 token 预算切分 + 建立稳定关系 ID）
        #    两步保持显式，调试时能清楚区分「ORM 映射失败」还是「文本切分失败」
        return self._document_chunker.chunk(document)
