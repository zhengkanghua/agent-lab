"""阶段 6 新闻正文规范化、质量拒绝与稳定哈希输入的完全离线测试。

测试直接使用纯文本 Normalizer 和 FreshRSS Mapper，不访问数据库或网络。规则只允许
删除边界标题块和相邻完整重复段落，明确验证正文内部合法重复句子不会被误删。
"""

from hashlib import sha256

import pytest

from news_vector_service.ingestion.content_quality import ContentQualityNormalizer
from news_vector_service.ingestion.freshrss_mapper import (
    FreshRSSContentQualityError,
    FreshRSSItemMapper,
)
from news_vector_service.schemas.freshrss import FreshRSSItem, FreshRSSSubscription


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


def map_item(article: FreshRSSItem) -> str:
    """返回 Mapper 生成的稳定 ``content_text``。"""

    return FreshRSSItemMapper().map(
        article,
        subscription(),
        provider="freshrss_test",
    ).content_text


def test_title_at_body_start_matches_html_entity_whitespace_and_punctuation() -> None:
    article = item(
        title="<strong>利率&nbsp;更新！</strong>",
        content="<h1>利率 更新。</h1><p>第一段正文。</p>",
    )

    assert map_item(article) == "第一段正文。"


def test_title_at_body_end_is_removed_only_as_complete_boundary_block() -> None:
    article = item(
        title="利率更新！",
        content="<p>第一段正文。</p><h2>利率 更新。</h2>",
    )

    assert map_item(article) == "第一段正文。"


def test_content_and_summary_are_never_concatenated() -> None:
    repeated_html = "<p>相同摘要正文。</p>"
    article = item(content=repeated_html, summary=repeated_html)

    assert map_item(article) == "相同摘要正文。"


def test_adjacent_duplicate_paragraphs_collapse_after_entity_and_unicode_space() -> None:
    article = item(
        content=(
            "<p>A&nbsp;&amp;&nbsp;B</p>"
            "<p>A&#160;&amp;&#160;B</p>"
            "<p>Cafe\u0301</p>"
        )
    )

    assert map_item(article) == "A & B\nCafé"


def test_legal_repeated_sentence_and_non_adjacent_paragraph_are_preserved() -> None:
    normalizer = ContentQualityNormalizer(min_content_chars=1)
    result = normalizer.inspect(
        title="标题",
        content_text="重要。重要。\n引用段\n重要。重要。",
    )

    assert result.normalized_text == "重要。重要。\n引用段\n重要。重要。"
    assert result.removed_duplicate_lines == 0


def test_empty_title_empty_body_and_title_only_have_stable_reasons() -> None:
    mapper = FreshRSSItemMapper()
    with pytest.raises(FreshRSSContentQualityError) as empty_title:
        mapper.map(
            item(title="  ", content="<p>正文</p>"),
            subscription(),
            provider="freshrss_test",
        )
    assert empty_title.value.reason == "empty_title"

    with pytest.raises(FreshRSSContentQualityError) as empty_body:
        mapper.map(
            item(content="<div>&nbsp;</div>"),
            subscription(),
            provider="freshrss_test",
        )
    assert empty_body.value.reason == "empty_content"

    with pytest.raises(FreshRSSContentQualityError) as title_only:
        mapper.map(
            item(title="唯一标题", content="<h1>唯一 标题。</h1>"),
            subscription(),
            provider="freshrss_test",
        )
    assert title_only.value.reason == "title_only"


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
