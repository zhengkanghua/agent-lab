"""把 LangChain ``Document`` 切分成可独立检索的 Chunk Document。

为什么要切分：一篇新闻可能很长，超过了模型/检索适合处理的长度。所以把完整正文
切成多个小块（Chunk），每一块都是能独立检索的 Document。

本模块只处理内存对象——不调用 Embedding、不连向量库、不写 PostgreSQL。切分结果
仍是 LangChain ``Document``：``page_content`` 保存片段正文，``metadata`` 保存父子
关系和过滤字段，``id`` 保存稳定 Chunk UUID。
"""

from uuid import UUID, uuid5

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


class DocumentChunker:
    """按 token 预算切分文档，并为每个 Chunk 建立稳定关系 Metadata。

    用 token 而不是字符数计量，是因为 Embedding 和 LLM 的输入限制都以 token 为准。
    ``RecursiveCharacterTextSplitter`` 会依次尝试更细的边界，只有上一级边界仍无法
    满足预算时才继续拆——所以通常能优先保留段落和完整句子（语义更完整）。

    父子/相邻 Chunk 关系存为普通 Metadata，而不是某个框架专有的节点对象。这样
    这些字段可以直接进 Qdrant payload，不依赖特定向量库适配器。
    """

    # 默认值是正文 token 预算，不包含 Metadata。LangChain 常规向量化只处理
    # page_content，因此 Metadata 不会占用这个预算，也不会混入 Embedding 文本。
    DEFAULT_CHUNK_SIZE = 512
    DEFAULT_CHUNK_OVERLAP = 96
    DEFAULT_ENCODING_NAME = "cl100k_base"

    # 分隔符按“语义边界从强到弱”排列。空字符串是最终兜底：极长且没有任何
    # 标点或空格的文本仍会被强制切到 token 上限以内，避免超出模型输入限制。
    SEPARATORS = (
        "\n\n",
        "\n",
        "。",
        "！",
        "？",
        "；",
        ". ",
        "! ",
        "? ",
        "; ",
        "，",
        ", ",
        " ",
        "",
    )

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        encoding_name: str = DEFAULT_ENCODING_NAME,
    ) -> None:
        """创建 token-aware 文档切分器。

        Args:
            chunk_size: 单个 Chunk ``page_content`` 的最大 token 数，必须大于零。
            chunk_overlap: 相邻 Chunk 最多复用的 token 数，不能为负数，并且必须
                小于 ``chunk_size``。
            encoding_name: tiktoken 编码名称。编码决定“一个 token”的实际计算
                方式；修改编码会改变切分边界，也会改变稳定 Chunk ID。

        Raises:
            ValueError: Chunk 大小或重叠参数不满足上述约束时抛出。编码名称是否
                有效由 tiktoken 在构造切分器时校验。
        """

        if chunk_size < 1:
            raise ValueError("chunk_size 必须大于零")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能为负数")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._encoding_name = encoding_name
        # from_tiktoken_encoder 让 length_function 使用真实 tokenizer 计数；
        # keep_separator=True 会把句末标点留在相邻正文中，避免片段丢失语气边界。
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=encoding_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=list(self.SEPARATORS),
            keep_separator=True,
        )

    @property
    def chunk_size(self) -> int:
        """返回单个 Chunk 的最大 token 预算。"""

        return self._chunk_size

    @property
    def chunk_overlap(self) -> int:
        """返回相邻 Chunk 最多复用的 token 数。"""

        return self._chunk_overlap

    @property
    def encoding_name(self) -> str:
        """返回切分时使用的 tiktoken 编码名称。"""

        return self._encoding_name

    def chunk(self, document: Document) -> list[Document]:
        """把完整 Document 切分成按原文顺序排列的 Chunk Document。

        每个 Chunk 继承父文档 Metadata，并通过普通 Metadata 保存父 ID、顺序及
        相邻 Chunk ID。这些字段可直接持久化为 Qdrant payload。

        Args:
            document: ``DocumentBuilder`` 生成的完整文档。``id`` 必须是 PostgreSQL
                ``documents.id`` 的 UUID 字符串，``page_content`` 必须非空。

        Returns:
            按原文顺序排列的 LangChain ``Document`` 列表。每项的 ``id`` 是稳定
            Chunk UUID，``metadata`` 是父文档 Metadata 的副本并附加以下字段：
            ``parent_document_id``、``chunk_index``、``chunk_count``，以及存在相邻
            Chunk 时的 ``previous_chunk_id``、``next_chunk_id``。

        Raises:
            ValueError: 文档 ID 缺失、不是 UUID，或正文为空时抛出。
        """

        # 1. 校验：父文档要有合法 UUID、正文得非空
        document_uuid = self._document_uuid(document)
        if not document.page_content.strip():
            raise ValueError(
                "page_content 为空，无法对 LangChain 文档进行分块。"
            )

        # 2. 用 LangChain 切分器切出原始片段（它会复制父 Metadata，避免多个 Chunk
        #    共享同一个可变 dict）。先生成所有 ID，后续才能一次遍历写入准确前后关系。
        raw_chunks = self._splitter.split_documents([document])
        # 3. 过滤空白 Chunk 和同文档内完全重复的正文（排序用最终列表重建）
        chunks: list[Document] = []
        seen_page_content: set[str] = set()
        for chunk in raw_chunks:
            page_content = chunk.page_content.strip()
            if not page_content or page_content in seen_page_content:
                continue
            seen_page_content.add(page_content)
            chunk.page_content = page_content
            chunks.append(chunk)
        if not chunks:
            raise ValueError("文档切分没有产生任何非空的唯一分块。")

        # 4. 为每个 Chunk 生成稳定 ID（uuid5，重跑不变）
        chunk_ids = [
            self._build_chunk_id(document_uuid, chunk_index)
            for chunk_index in range(len(chunks))
        ]

        # 5. 关系字段（index/count/prev/next）按去重后的最终列表计算，不用切分器
        #    返回的原始顺序，否则被丢弃的空白/重复片段会在链上留下空洞
        for chunk_index, chunk in enumerate(chunks):
            chunk.id = chunk_ids[chunk_index]
            chunk.metadata.update(
                {
                    "parent_document_id": str(document_uuid),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                }
            )
            if chunk_index > 0:
                chunk.metadata["previous_chunk_id"] = chunk_ids[chunk_index - 1]
            if chunk_index + 1 < len(chunks):
                chunk.metadata["next_chunk_id"] = chunk_ids[chunk_index + 1]

        return chunks

    def _build_chunk_id(self, document_uuid: UUID, chunk_index: int) -> str:
        """根据父文档、切分配置和顺序生成稳定 UUIDv5。

        Args:
            document_uuid: PostgreSQL 文档 UUID，同时作为 UUIDv5 namespace。
            chunk_index: Chunk 在当前切分结果中从零开始的顺序。

        Returns:
            UUID 字符串。相同父文档使用相同编码和切分参数重复处理时结果一致；
            修改编码、大小、重叠或顺序后会生成另一组 ID，避免覆盖旧配置的向量。
        """

        # 把所有会改变切分结果的配置放入 name。uuid5 是确定性哈希，不会像 uuid4
        # 那样在重跑任务时产生新 ID，因此向量库可以按 ID 幂等 upsert。
        name = (
            f"recursive:{self._encoding_name}:{self._chunk_size}:"
            f"{self._chunk_overlap}:{chunk_index}"
        )
        return str(uuid5(document_uuid, name))

    @staticmethod
    def _document_uuid(document: Document) -> UUID:
        """校验父 Document ID，并转换为 UUID。

        Args:
            document: 待切分的 LangChain 文档。

        Returns:
            可用作 UUIDv5 namespace 的 PostgreSQL 文档 UUID。

        Raises:
            ValueError: ``Document.id`` 缺失或不是合法 UUID 字符串时抛出。
        """

        if document.id is None:
            raise ValueError("分块前必须设置 Document.id。")
        try:
            return UUID(document.id)
        except ValueError as exc:
            raise ValueError(
                "分块前 Document.id 必须是 PostgreSQL 文档 UUID。"
            ) from exc
