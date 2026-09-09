"""解析器与切分器的替换入口，业务调用方只使用项目数据契约。"""

from typing import Protocol

from agent_lab.knowledge.processing.contracts import ChunkResult, ParsedDocument


class DocumentParser(Protocol):
    def parse(self, data: bytes, *, mime_type: str, title: str) -> ParsedDocument: ...


class StructuredChunker(Protocol):
    def build_chunks(self, document: ParsedDocument) -> ChunkResult: ...
