"""真实 Docling 文本处理行为；tokenizer 预先准备，测试期间不访问网络。"""

from pathlib import Path

import pytest

from agent_lab.knowledge.adapters.docling_chunker import DoclingStructuredChunker
from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
from agent_lab.knowledge.processing.contracts import DocumentPreview, DocumentProcessingError
from agent_lab.knowledge.processing.processor import DocumentProcessor


@pytest.fixture(scope="module")
def chunker():
    path = Path(__file__).parents[1] / ".cache/tokenizers/bge-m3"
    if not (path / "tokenizer.json").is_file():
        pytest.skip("需要先准备锁定的 BGE-M3 tokenizer，本测试不自动下载资源。")
    return DoclingStructuredChunker(tokenizer_path=path, max_tokens=128)


@pytest.fixture
def processor(chunker):
    return DocumentProcessor(parser=DoclingDocumentParser(), chunker=chunker)


def preview(processor, text, mime_type="text/markdown"):
    return processor.preview(text.encode("utf-8"), mime_type=mime_type, title="Document")


def test_heading_identity_and_nested_context_survive_sibling_and_skipped_levels(processor):
    result = preview(processor, "# Root\n\nIntroduction\n\n## Child\n\nFirst child\n\n#### Deep\n\nDeep body\n\n## Child\n\nSecond child\n\n# Next\n\nLast body")

    assert not result.issues
    assert [chunk.headings for chunk in result.chunk_result.chunks] == [
        ("Root",), ("Root", "Child"), ("Root", "Child", "Deep"), ("Root", "Child"), ("Next",),
    ]
    entries = result.document.outline
    assert [entry.level for entry in entries] == [1, 2, 4, 2, 1]
    assert entries[2].parent_id == entries[1].id
    assert entries[3].parent_id == entries[0].id
    assert entries[1].id != entries[3].id
    assert result.chunk_result.chunks[1].heading_ids != result.chunk_result.chunks[3].heading_ids


def test_identical_text_in_separate_same_named_sections_is_not_deduplicated(processor):
    result = preview(processor, "# Root\n\n## Repeated\n\nSame text\n\n## Repeated\n\nSame text")

    assert not result.issues
    chunks = result.chunk_result.chunks
    assert [chunk.text for chunk in chunks] == ["Same text", "Same text"]
    assert chunks[0].heading_ids != chunks[1].heading_ids


def test_txt_preserves_markdown_symbols_bom_and_normalizes_line_endings(processor):
    text = "\ufeff# ordinary text\r\n\r\n```python\r\nprint(1)\r\n```\r- item"
    result = preview(processor, text, "text/plain")

    assert not result.issues
    assert not result.document.outline
    assert result.document.text_format == "plain"
    expected = "# ordinary text\n\n```python\nprint(1)\n```\n- item"
    assert result.document.body == expected
    assert result.chunk_result.chunks[0].text == expected
    assert all(not chunk.headings for chunk in result.chunk_result.chunks)


def test_html_keeps_first_paragraph_inline_text_lists_and_table(processor):
    text = "<p>First &amp; foremost.</p><h1>Root</h1><p>Body <strong>bold</strong> and <em>emphasis</em>.</p><ul><li>Item one</li><li>Item two</li></ul><table><tr><th>Key</th><th>Value</th></tr><tr><td>A</td><td>B</td></tr></table>"
    result = preview(processor, text, "text/html")

    assert not result.issues
    assert result.chunk_result.chunks[0].text == "First & foremost."
    combined = "\n".join(chunk.text for chunk in result.chunk_result.chunks)
    for value in ("Body", "bold", "emphasis", "Item one", "Item two", "Key", "Value", "A", "B"):
        assert value in combined
    table = next(block.table for block in result.document.blocks if block.kind == "table")
    assert table.rows == 2 and table.columns == 2
    assert [cell.text for cell in table.cells] == ["Key", "Value", "A", "B"]


@pytest.mark.parametrize("mime_type,text", [
    ("text/html", "<p>A short bulletin.</p>"),
    ("text/markdown", "#### Skipped levels\n\nBrief body."),
])
def test_short_content_without_root_heading_is_valid(processor, mime_type, text):
    assert not preview(processor, text, mime_type).issues


def test_markdown_lists_code_and_quote_remain_searchable(processor):
    result = preview(processor, "# Guide\n\n- First item\n  - Nested item\n- Second item\n\n> Quoted fact\n\n```python\ndef calculate(value):\n    return value + 1\n```\n")

    assert not result.issues
    text = "\n".join(chunk.text for chunk in result.chunk_result.chunks)
    for value in ("First item", "Nested item", "Second item", "Quoted fact", "def calculate(value):", "return value + 1"):
        assert value in text
    assert all(chunk.headings == ("Guide",) for chunk in result.chunk_result.chunks)


@pytest.mark.parametrize("kind", ["paragraph", "code", "table", "long_table_row"])
def test_long_content_is_preserved_with_real_context_token_budget(processor, chunker, kind):
    values = [f"sample{index:04d}" for index in range(160)]
    if kind == "code":
        content = "```python\n" + "\n".join(f"print('{value}')" for value in values) + "\n```"
    elif kind == "table":
        content = "| Key | Value |\n| --- | --- |\n" + "\n".join(f"| {value} | detail |" for value in values)
    elif kind == "long_table_row":
        content = "| Key | Value |\n| --- | --- |\n| long | " + " ".join(values) + " |"
    else:
        content = "中文正文。" + " ".join(values)
    result = preview(processor, "# Root\n\n## Child\n\n" + content)

    assert not result.issues
    assert len(result.chunk_result.chunks) > 1
    combined = " ".join(chunk.text for chunk in result.chunk_result.chunks)
    for value in values:
        assert value in combined
    for chunk in result.chunk_result.chunks:
        assert chunk.headings == ("Root", "Child")
        assert chunk.token_count == chunker.count_tokens(chunk.embedding_text)
        assert chunk.token_count <= 128
        assert chunk.embedding_text.startswith("Root\nChild\n")


@pytest.mark.parametrize("text", ["", "   \n", "# Only title\n\n## Another title"])
def test_empty_body_is_actionable_even_if_headings_exist(processor, text):
    result = preview(processor, text)
    assert "document_body_empty" in {issue.code for issue in result.issues}
    assert "document_chunks_empty" in {issue.code for issue in result.issues}


def test_heading_exhausting_budget_is_explicit(processor):
    result = preview(processor, "# " + "very long heading " * 100 + "\n\nContent must not silently lose its heading.")
    assert "chunk_heading_budget_exceeded" in {issue.code for issue in result.issues}
    assert not result.chunk_result.chunks


def test_preview_roundtrip_retains_chunks_and_fingerprint(processor, chunker):
    result = preview(processor, "# Root\n\n## Child\n\nBody\n\n| Key | Value |\n| --- | --- |\n| One | Two |")
    restored = DocumentPreview.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.fingerprint == result.fingerprint
    assert chunker.build_chunks(restored.document) == result.chunk_result
    assert preview(processor, result.document.body + "\n\nChanged").fingerprint != result.fingerprint


@pytest.mark.parametrize("mime_type,text", [
    ("text/markdown", "# Root\n\nText\n\n![remote](https://example.invalid/image.png)\n![local](file:///private/secret.png)"),
    ("text/html", '<p>Text</p><img src="https://example.invalid/image.png"><img src="file:///private/secret.png"><script>throw new Error("not content")</script>'),
])
def test_parser_never_fetches_referenced_images(monkeypatch, mime_type, text):
    import requests
    from PIL import Image

    def unexpected_fetch(*args, **kwargs):
        pytest.fail("解析原始正文不应加载远程或本地图片。")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_fetch)
    monkeypatch.setattr(Image, "open", unexpected_fetch)
    result = DoclingDocumentParser().parse(text.encode(), mime_type=mime_type, title="Document")
    assert any(block.text == "Text" for block in result.blocks)
    assert all("not content" not in block.text for block in result.blocks)


@pytest.mark.parametrize("data,code", [(b"\xff", "document_encoding_invalid"), (b"hello\0world", "document_content_invalid")])
def test_invalid_text_returns_stable_processing_reason(data, code):
    with pytest.raises(DocumentProcessingError) as caught:
        DoclingDocumentParser().parse(data, mime_type="text/plain", title="Document")
    assert caught.value.code == code
