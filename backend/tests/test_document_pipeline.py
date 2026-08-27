"""LangChain 文档构建与切分流程的行为测试。"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import tiktoken
from langchain_core.documents import Document

from news_vector_service.domain.enums import DocumentType
from news_vector_service.models.document import DocumentRecord
from news_vector_service.models.source import SourceRecord
from news_vector_service.pipeline.document_builder import DocumentBuilder
from news_vector_service.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from news_vector_service.pipeline.document_chunker import DocumentChunker
from news_vector_service.runtime import selector_loop_factory


def build_record(*, content_text: str = "正文内容") -> DocumentRecord:
    """构造无需数据库连接的已加载 ORM 文档。"""

    source_id = uuid4()
    source = SourceRecord(
        id=source_id,
        provider="freshrss_main",
        external_id="feed/2",
        name="示例来源",
    )
    return DocumentRecord(
        id=uuid4(),
        source_id=source_id,
        source=source,
        external_id="article/42",
        document_type=DocumentType.ARTICLE,
        title="示例标题",
        url="https://example.com/article/42",
        published_at=datetime(2026, 8, 13, 1, 2, 3, tzinfo=UTC),
        authors=["作者甲"],
        labels=["宏观"],
        image_urls=[],
        content_text=content_text,
        content_hash="0" * 64,
    )


def test_builder_maps_record_to_langchain_document() -> None:
    record = build_record(content_text="  第一段。\n\n第二段。  ")

    document = DocumentBuilder().build(record)

    assert isinstance(document, Document)
    assert document.id == str(record.id)
    assert document.page_content == "第一段。\n\n第二段。"
    assert document.metadata == {
        "document_id": str(record.id),
        "source_id": str(record.source_id),
        "source_provider": "freshrss_main",
        "source_external_id": "feed/2",
        "document_external_id": "article/42",
        "content_hash": "0" * 64,
        "title": "示例标题",
        "source_name": "示例来源",
        "document_type": "article",
        "url": "https://example.com/article/42",
        "authors": ["作者甲"],
        "labels": ["宏观"],
        "published_at": "2026-08-13T01:02:03+00:00",
    }


def test_builder_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="content_text 为空"):
        DocumentBuilder().build(build_record(content_text=" \n "))


def test_builder_rejects_missing_source() -> None:
    record = build_record()
    record.source = None  # type: ignore[assignment]

    with pytest.raises(ValueError, match="必须包含来源"):
        DocumentBuilder().build(record)


def test_chunker_generates_stable_ids_and_relationship_metadata() -> None:
    parent_id = uuid4()
    document = Document(
        id=str(parent_id),
        page_content=" ".join(f"token{index}" for index in range(80)),
        metadata={"title": "示例标题", "document_id": str(parent_id)},
    )
    chunker = DocumentChunker(chunk_size=24, chunk_overlap=4)

    first_run = chunker.chunk(document)
    second_run = chunker.chunk(document)

    assert len(first_run) > 1
    assert [chunk.id for chunk in first_run] == [chunk.id for chunk in second_run]
    assert all(chunk.id is not None for chunk in first_run)
    assert all(UUID(chunk.id) for chunk in first_run if chunk.id is not None)

    encoding = tiktoken.get_encoding(chunker.encoding_name)
    assert all(len(encoding.encode(chunk.page_content)) <= 24 for chunk in first_run)

    for index, chunk in enumerate(first_run):
        assert chunk.metadata["title"] == "示例标题"
        assert chunk.metadata["parent_document_id"] == str(parent_id)
        assert chunk.metadata["chunk_index"] == index
        assert chunk.metadata["chunk_count"] == len(first_run)
        if index == 0:
            assert "previous_chunk_id" not in chunk.metadata
        else:
            assert chunk.metadata["previous_chunk_id"] == first_run[index - 1].id
        if index == len(first_run) - 1:
            assert "next_chunk_id" not in chunk.metadata
        else:
            assert chunk.metadata["next_chunk_id"] == first_run[index + 1].id


def test_chunker_drops_blank_and_duplicate_chunks_then_rebuilds_relationships() -> None:
    parent_id = uuid4()
    document = Document(
        id=str(parent_id),
        page_content="有效正文",
        metadata={
            "document_id": str(parent_id),
            "previous_chunk_id": "stale",
            "next_chunk_id": "stale",
        },
    )
    chunker = DocumentChunker(chunk_size=32, chunk_overlap=4)

    class FakeSplitter:
        """返回包含空白和完全重复正文的可控切分结果。"""

        def split_documents(self, _documents: list[Document]) -> list[Document]:
            return [
                Document(page_content=" 第一块 ", metadata=dict(document.metadata)),
                Document(page_content=" \n ", metadata=dict(document.metadata)),
                Document(page_content="第一块", metadata=dict(document.metadata)),
                Document(page_content="第二块", metadata=dict(document.metadata)),
            ]

    chunker._splitter = FakeSplitter()  # type: ignore[assignment]  # noqa: SLF001
    chunks = chunker.chunk(document)

    assert [chunk.page_content for chunk in chunks] == ["第一块", "第二块"]
    assert len({chunk.page_content for chunk in chunks}) == len(chunks)
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == [0, 1]
    assert all(chunk.metadata["chunk_count"] == 2 for chunk in chunks)
    assert "previous_chunk_id" not in chunks[0].metadata
    assert chunks[0].metadata["next_chunk_id"] == chunks[1].id
    assert chunks[1].metadata["previous_chunk_id"] == chunks[0].id
    assert "next_chunk_id" not in chunks[1].metadata


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"chunk_size": 0}, "大于零"),
        ({"chunk_overlap": -1}, "不能为负数"),
        ({"chunk_size": 10, "chunk_overlap": 10}, "必须小于 chunk_size"),
    ],
)
def test_chunker_rejects_invalid_configuration(
    kwargs: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DocumentChunker(**kwargs)


@pytest.mark.parametrize("document_id", [None, "not-a-uuid"])
def test_chunker_requires_postgresql_uuid(document_id: str | None) -> None:
    document = Document(id=document_id, page_content="正文")

    with pytest.raises(ValueError, match="Document.id"):
        DocumentChunker().chunk(document)


def test_pipeline_builds_langchain_chunks_from_record() -> None:
    record = build_record(content_text="段落内容。" * 300)
    pipeline = DocumentChunkPipeline(
        document_chunker=DocumentChunker(chunk_size=64, chunk_overlap=8)
    )

    chunks = pipeline.build_chunks(record)

    assert len(chunks) > 1
    assert all(isinstance(chunk, Document) for chunk in chunks)
    assert all(chunk.metadata["document_id"] == str(record.id) for chunk in chunks)


def test_server_loop_factory_is_psycopg_compatible() -> None:
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
