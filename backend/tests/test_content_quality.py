"""FreshRSS 保留原件，真实 Docling 在保存后的阶段规范化正文并保留标题层级。"""

from hashlib import sha256

import pytest

from agent_lab.ingestion.content_quality import ContentQualityNormalizer
from agent_lab.ingestion.freshrss_mapper import FreshRSSItemMapper
from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
from agent_lab.schemas.freshrss import FreshRSSItem, FreshRSSSubscription


def subscription() -> FreshRSSSubscription:
    """构造固定 FreshRSS 订阅协议对象。"""

    return FreshRSSSubscription.model_validate(
        {
            "id": "feed/1",
            "title": "测试来源",
            "url": "https://example.com/feed.xml",
            "htmlUrl": "https://example.com/",
            "categories": [{"id": "label/news", "label": "新闻"}],
        }
    )


def item(
    *,
    title: str = "利率更新！",
    content: str | None = "<p>正文内容。</p>",
    summary: str | None = None,
) -> FreshRSSItem:
    """按测试场景构造最小 FreshRSS 文章。"""

    payload: dict[str, object] = {
        "id": "item/1",
        "title": title,
        "alternate": [{"href": "https://example.com/articles/1"}],
        "origin": {
            "streamId": "feed/1",
            "title": "测试来源",
            "htmlUrl": "https://example.com/",
        },
    }
    if content is not None:
        payload["content"] = {"content": content}
    if summary is not None:
        payload["summary"] = {"content": summary}
    return FreshRSSItem.model_validate(payload)


def map_item(article: FreshRSSItem):
    """映射只负责接收，解析单独使用锁定的真实 Docling。"""

    return FreshRSSItemMapper().map(
        article,
        subscription(),
        provider="freshrss_test",
    )


def parse_item(article):
    incoming = map_item(article)
    return DoclingDocumentParser().parse(incoming.raw_bytes, mime_type=incoming.mime_type, title=incoming.title)


def test_structural_title_at_body_start_is_preserved_as_chunk_context() -> None:
    article = item(
        title="<strong>利率&nbsp;更新！</strong>",
        content="<h1>利率 更新。</h1><p>第一段正文。</p>",
    )

    parsed = parse_item(article)
    assert [entry.title for entry in parsed.outline] == ["利率 更新。"]
    paragraph = next(block for block in parsed.blocks if block.kind == "paragraph")
    assert paragraph.text == "第一段正文。"
    assert paragraph.heading_ids == (parsed.outline[0].id,)


def test_title_at_body_end_is_removed_only_as_complete_boundary_block() -> None:
    article = item(
        title="利率更新！",
        content="<p>第一段正文。</p><p>利率 更新。</p>",
    )

    assert parse_item(article).body == "第一段正文。"


def test_plain_title_boundary_does_not_become_a_body_only_document():
    parsed = parse_item(item(title="唯一标题", content="<p>唯一 标题。</p>"))
    assert "document_body_empty" in [issue.code for issue in parsed.issues]


def test_content_and_summary_are_never_concatenated() -> None:
    repeated_html = "<p>相同摘要正文。</p>"
    article = item(content=repeated_html, summary=repeated_html)

    assert map_item(article).raw_bytes == repeated_html.encode()
    assert parse_item(article).body == "相同摘要正文。"


def test_adjacent_duplicate_paragraphs_collapse_after_entity_and_unicode_space() -> None:
    article = item(
        content=(
            "<p>A&nbsp;&amp;&nbsp;B</p>"
            "<p>A&#160;&amp;&#160;B</p>"
            "<p>Cafe\u0301</p>"
        )
    )

    assert [block.text for block in parse_item(article).blocks if block.kind == "paragraph"] == ["A & B", "Café"]


def test_same_paragraph_in_distinct_sections_and_inline_code_are_preserved():
    parsed = parse_item(item(content="<h1>同名</h1><p>重复正文</p><h1>同名</h1><p>重复正文</p><pre><code>x  = 2</code></pre>"))
    paragraphs = [block for block in parsed.blocks if block.kind == "paragraph"]
    assert [block.text for block in paragraphs] == ["重复正文", "重复正文"]
    assert paragraphs[0].heading_ids != paragraphs[1].heading_ids
    assert any(block.kind == "code" and "x  = 2" in block.text for block in parsed.blocks)


def test_legal_repeated_sentence_and_non_adjacent_paragraph_are_preserved() -> None:
    normalizer = ContentQualityNormalizer(min_content_chars=1)
    result = normalizer.inspect(
        title="标题",
        content_text="重要。重要。\n引用段\n重要。重要。",
    )

    assert result.normalized_text == "重要。重要。\n引用段\n重要。重要。"
    assert result.removed_duplicate_lines == 0


@pytest.mark.parametrize("title,html,reason", [
    ("  ", "<p>正文</p>", "document_title_empty"),
    ("标题", "<div>&nbsp;</div>", "document_body_empty"),
    ("唯一标题", "<h1>唯一 标题。</h1>", "document_body_empty"),
])
def test_bad_content_is_received_and_has_stable_processing_issue(title, html, reason):
    article = item(title=title, content=html)
    assert map_item(article).raw_bytes == html.encode()
    assert reason in [issue.code for issue in parse_item(article).issues]


def test_body_that_is_entirely_adjacent_duplicates_keeps_one_copy() -> None:
    result = ContentQualityNormalizer(min_content_chars=1).inspect(
        title="标题",
        content_text="合法正文段\n合法正文段\n合法正文段",
    )

    assert result.normalized_text == "合法正文段"
    assert result.removed_duplicate_lines == 2
    assert result.is_usable is True


def test_normalization_and_content_hash_are_idempotent() -> None:
    normalizer = ContentQualityNormalizer(min_content_chars=1)
    first = normalizer.inspect(
        title="标题！",
        content_text="标题。\nA&nbsp; B\nA  B",
    ).normalized_text
    second = normalizer.inspect(title="标题！", content_text=first).normalized_text

    assert first == "A B"
    assert second == first
    assert sha256(first.encode("utf-8")).hexdigest() == sha256(
        second.encode("utf-8")
    ).hexdigest()
