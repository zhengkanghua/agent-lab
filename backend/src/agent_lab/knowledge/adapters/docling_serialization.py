"""集中适配锁定的 Docling 序列化与表格预算行为。"""

from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.line_chunker import LineBasedTokenChunker
from docling_core.transforms.serializer.base import BaseSerializerProvider
from docling_core.transforms.serializer.markdown import MarkdownParams, MarkdownTableSerializer, MarkdownTextSerializer
from docling_core.types.doc import TableItem

from agent_lab.knowledge.processing.contracts import DocumentProcessingError


class LiteralLineTextSerializer(MarkdownTextSerializer):
    def _md_line_breaks(self, text: str) -> str:
        # 预览不是重新排版 Markdown；TXT 的换行也不能被插入 Markdown 硬换行空格。
        return text


class DocumentSerializerProvider(BaseSerializerProvider):
    def get_serializer(self, doc):
        return ChunkingDocSerializer(
            doc=doc, text_serializer=LiteralLineTextSerializer(),
            table_serializer=MarkdownTableSerializer(),
            params=MarkdownParams(
                image_placeholder="", escape_underscores=False, escape_html=False,
                compact_tables=True,
            ),
        )


class ContextBudgetHybridChunker(HybridChunker):
    """2.95.0 的表格分支使用总预算；在公开 segment 扩展点补扣章节上下文。"""

    def segment(self, doc_chunk, available_length, doc_serializer):
        if len(doc_chunk.meta.doc_items) != 1 or not isinstance(doc_chunk.meta.doc_items[0], TableItem):
            return super().segment(doc_chunk, available_length, doc_serializer)
        headers, rows = doc_serializer.table_serializer.get_header_and_body_lines(table_text=doc_chunk.text)
        prefix = "".join(headers)
        if prefix and self.tokenizer.count_tokens(prefix) >= available_length:
            raise DocumentProcessingError("chunk_table_header_budget_exceeded")
        line_chunker = LineBasedTokenChunker(
            tokenizer=self.tokenizer.model_copy(update={"max_tokens": available_length}),
            prefix=prefix, omit_prefix_on_overflow=False,
            serializer_provider=self.serializer_provider,
        )
        return line_chunker.chunk_text(rows)
