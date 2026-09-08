"""把纯文档快照转换成 LangChain Document，不读取数据库或修改业务状态。"""

from langchain_core.documents import Document

from agent_lab.knowledge.document_contracts import DocumentSnapshot
from agent_lab.knowledge.adapters.text_files import markdown_index_text


class DocumentBuilder:
    """集中定义正文与检索元数据的映射；只有正文进入 Embedding。"""

    def build(self, record: DocumentSnapshot) -> Document:
        """构造带稳定身份的内存文档，元数据保留可空来源和原文地址。"""
        content_text = record.content_text.strip()
        if not content_text:
            raise ValueError("content_text 为空，无法构建 LangChain 文档。")
        if record.mime_type == "text/markdown":
            content_text = markdown_index_text(record.content_text)
        source = record.source
        metadata = {
            "document_id": str(record.id),
            "knowledge_base_id": str(record.knowledge_base_id) if record.knowledge_base_id else None,
            "source_id": str(record.source_id) if record.source_id else None,
            "source_provider": source.provider if source else None,
            "source_external_id": source.external_id if source else None,
            "document_external_id": record.external_id,
            "content_hash": record.content_hash,
            "title": record.title,
            "source_name": source.name if source else None,
            "document_type": record.document_type.value,
            "mime_type": record.mime_type,
            "upload_filename": record.upload_filename,
            "url": record.url,
            "authors": list(record.authors),
            "labels": list(record.labels),
        }
        for field in ("published_at", "source_updated_at"):
            value = getattr(record, field)
            if value is not None:
                metadata[field] = value.isoformat()
        return Document(id=str(record.id), page_content=content_text, metadata=metadata)
