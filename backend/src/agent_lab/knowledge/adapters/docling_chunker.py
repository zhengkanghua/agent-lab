"""Docling HybridChunker 适配器；只输出项目的预览清单，不进行向量化。"""

from pathlib import Path
import warnings

from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

from agent_lab.knowledge.adapters.docling_serialization import ContextBudgetHybridChunker, DocumentSerializerProvider
from agent_lab.knowledge.adapters.docling_structure import import_structure
from agent_lab.knowledge.adapters.tokenizer_resources import (
    BGE_M3_TOKENIZER, BGE_M3_TOKENIZER_REVISION, verify_tokenizer_resources,
)
from agent_lab.knowledge.processing.contracts import (
    ChunkResult, ChunkSpecification, DocumentProcessingError, ParsedDocument,
    PreviewChunk, ProcessingIssue,
)
from agent_lab.knowledge.processing.specification import chunk_specification


class DoclingStructuredChunker:
    def __init__(self, *, tokenizer_path: str | Path, max_tokens: int = 512):
        path = verify_tokenizer_resources(tokenizer_path)
        try:
            tokenizer = HuggingFaceTokenizer.from_pretrained(
                model_name=path, max_tokens=max_tokens, local_files_only=True,
            )
        except Exception as exc:
            raise DocumentProcessingError("document_tokenizer_unavailable") from exc
        self._tokenizer = tokenizer.tokenizer
        special_tokens = self._tokenizer.num_special_tokens_to_add(pair=False)
        if max_tokens <= special_tokens:
            raise ValueError("Chunk token 预算必须大于模型特殊 token 数。")
        # HybridChunker 只数正文 token，预留实际 Embedding 会添加的边界 token。
        tokenizer.max_tokens = max_tokens - special_tokens
        self._chunker = ContextBudgetHybridChunker(
            tokenizer=tokenizer, merge_peers=False, repeat_table_header=True,
            serializer_provider=DocumentSerializerProvider(),
        )
        self.specification = chunk_specification(max_tokens)

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=True))

    def build_chunks(self, document: ParsedDocument) -> ChunkResult:
        outline = {entry.id: entry for entry in document.outline}
        blocks = {block.id: block for block in document.blocks}
        for block in document.blocks:
            heading_text = "\n".join(outline[item].title for item in block.heading_ids)
            if heading_text and self.count_tokens(heading_text) >= self.specification.max_tokens:
                return ChunkResult(specification=self.specification, chunks=(), issues=(ProcessingIssue(code="chunk_heading_budget_exceeded", block_ids=block.heading_ids),))
        try:
            native, source_ids = import_structure(document)
            chunks: list[PreviewChunk] = []
            issues: list[ProcessingIssue] = []
            # 上游超限 warning 含正文；转换成稳定质量原因，不让原文进入日志。
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", UserWarning)
                native_chunks = list(self._chunker.chunk(native))
            if any(issubclass(warning.category, UserWarning) for warning in captured):
                issues.append(ProcessingIssue(code="chunk_content_warning"))
            for chunk in native_chunks:
                block_ids = tuple(dict.fromkeys(source_ids[item.self_ref] for item in chunk.meta.doc_items))
                heading_ids = blocks[block_ids[0]].heading_ids if block_ids else ()
                expected_headings = tuple(outline[item].title for item in heading_ids)
                if tuple(chunk.meta.headings or ()) != expected_headings:
                    issues.append(ProcessingIssue(code="chunk_heading_context_lost", block_ids=block_ids))
                embedding_text = self._chunker.contextualize(chunk)
                token_count = self.count_tokens(embedding_text)
                if token_count > self.specification.max_tokens:
                    issues.append(ProcessingIssue(code="chunk_token_budget_exceeded", block_ids=block_ids))
                if chunk.text.strip():
                    chunks.append(PreviewChunk(
                        sequence=len(chunks), text=chunk.text, embedding_text=embedding_text,
                        token_count=token_count, block_ids=block_ids,
                        heading_ids=heading_ids, headings=expected_headings,
                    ))
            if not chunks:
                issues.append(ProcessingIssue(code="document_chunks_empty"))
            return ChunkResult(specification=self.specification, chunks=tuple(chunks), issues=tuple(issues))
        except DocumentProcessingError as exc:
            return ChunkResult(specification=self.specification, chunks=(), issues=(ProcessingIssue(code=exc.code),))
        except Exception as exc:
            raise DocumentProcessingError("document_chunking_failed") from exc
