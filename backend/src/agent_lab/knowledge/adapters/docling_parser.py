"""仅使用 Docling 的 Markdown/HTML 后端，不导入完整 PDF/模型转换流水线。"""

from io import BytesIO

from docling.backend.html_backend import HTMLDocumentBackend
from docling.backend.md_backend import MarkdownDocumentBackend
from docling.datamodel.backend_options import HTMLBackendOptions, MarkdownBackendOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling_core.types.doc import DocItemLabel, DoclingDocument

from agent_lab.knowledge.adapters.docling_structure import export_structure
from agent_lab.knowledge.adapters.docling_html import normalize_html_document
from agent_lab.knowledge.processing.contracts import DocumentProcessingError, ParsedDocument, ProcessingIssue
from agent_lab.knowledge.processing.specification import PARSER_ID


class DoclingDocumentParser:
    parser_id = PARSER_ID

    def parse(self, data: bytes, *, mime_type: str, title: str) -> ParsedDocument:
        if mime_type not in {"text/plain", "text/markdown", "text/html"}:
            raise DocumentProcessingError("document_format_unsupported")
        try:
            content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise DocumentProcessingError("document_encoding_invalid") from exc
        if "\0" in content:
            raise DocumentProcessingError("document_content_invalid")
        try:
            if mime_type == "text/plain":
                native = DoclingDocument(name=title or "document")
                # TXT 不经过 Markdown 解释；段落拆分和 token 预算交给后续切分器。
                native.add_text(label=DocItemLabel.TEXT, text=content, orig=content)
            else:
                native = self._convert(content, mime_type=mime_type)
                if mime_type == "text/html":
                    normalize_html_document(native, title=title)
            blocks, outline, issues = export_structure(native)
            if not title.strip():
                issues += (ProcessingIssue(code="document_title_empty"),)
            has_body = any(block.kind not in {"heading", "group", "image"} and block.text.strip() for block in blocks)
            if not has_body:
                issues += (ProcessingIssue(code="document_body_empty"),)
            body = native.export_to_markdown() if mime_type == "text/html" else content
            return ParsedDocument(
                title=title, body=body, text_format="plain" if mime_type == "text/plain" else "markdown",
                parser=self.parser_id, blocks=blocks, outline=outline, issues=issues,
            )
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError("document_parse_failed") from exc

    @staticmethod
    def _convert(content: str, *, mime_type: str) -> DoclingDocument:
        is_html = mime_type == "text/html"
        options = HTMLBackendOptions(
            infer_furniture=False, add_title=False, render_page=False,
            fetch_images=False, enable_remote_fetch=False, enable_local_fetch=False,
        ) if is_html else MarkdownBackendOptions(fetch_images=False, enable_remote_fetch=False, enable_local_fetch=False)
        source = InputDocument(
            path_or_stream=BytesIO(content.encode("utf-8")),
            format=InputFormat.HTML if is_html else InputFormat.MD,
            backend=HTMLDocumentBackend if is_html else MarkdownDocumentBackend,
            backend_options=options, filename="document.html" if is_html else "document.md",
        )
        # InputDocument 拥有后端生命周期；这个锁定版本的 SDK 访问只留在此处。
        backend = source._backend
        try:
            if not source.valid:
                raise DocumentProcessingError("document_parse_failed")
            return backend.convert()
        finally:
            backend.unload()
