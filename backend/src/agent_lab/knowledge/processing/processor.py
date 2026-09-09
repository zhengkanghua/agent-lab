"""自动接入和人工预览共用的纯处理门面；持久化、调度及向量化在外层。"""

from agent_lab.knowledge.processing.contracts import DocumentPreview
from agent_lab.knowledge.processing.ports import DocumentParser, StructuredChunker


class DocumentProcessor:
    def __init__(self, *, parser: DocumentParser, chunker: StructuredChunker):
        self._parser = parser
        self._chunker = chunker

    def preview(self, data: bytes, *, mime_type: str, title: str) -> DocumentPreview:
        document = self._parser.parse(data, mime_type=mime_type, title=title)
        return DocumentPreview(document=document, chunk_result=self._chunker.build_chunks(document))
