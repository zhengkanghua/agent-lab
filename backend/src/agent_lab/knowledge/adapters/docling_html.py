"""保存原件后的 HTML 规范化；只处理完整文本块，不压平章节或代码。"""

from docling_core.types.doc import DocItemLabel, DoclingDocument, GroupLabel, TextItem

from agent_lab.ingestion.content_quality import normalize_inline_text, title_comparison_key


def normalize_html_document(document: DoclingDocument, *, title: str) -> None:
    """保留全部结构标题；仅删除首尾重复标题段及同一章节的相邻重复段。"""
    items = [item for item, _ in document.iterate_items(root=document.body, with_groups=True)
             if item.self_ref != document.body.self_ref]
    paragraphs = []
    for item in items:
        if type(item) is not TextItem or item.label not in {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH}:
            continue
        parent = item.parent.resolve(document) if item.parent else None
        # inline 的 TextItem 只是句内片段，不能按完整段落去重或删除。
        if parent is not None and parent.label == GroupLabel.INLINE:
            continue
        item.text = normalize_inline_text(item.text)
        paragraphs.append(item)

    paragraph_ids = {item.self_ref for item in paragraphs}
    title_key = title_comparison_key(title)
    meaningful = [item for item in items if isinstance(item, TextItem) or item.label == DocItemLabel.TABLE]
    removed = []
    if title_key and meaningful:
        for item in (meaningful[0], meaningful[-1]):
            if item.self_ref in paragraph_ids and not item.children and item not in removed:
                if title_comparison_key(item.text) == title_key:
                    removed.append(item)

    previous = None
    for item in items:
        if item in removed:
            continue
        if item.self_ref in paragraph_ids and not item.children:
            if previous is not None and item.parent == previous.parent and item.text == previous.text:
                removed.append(item)
                continue
            previous = item
        else:
            previous = None
    if removed:
        document.delete_items(node_items=removed)
