"""Docling 与项目结构的双向映射，SDK 类型只在适配器之间使用。"""

from docling_core.types.doc import (
    CodeItem, CodeLanguageLabel, DocItemLabel, DoclingDocument, GroupItem,
    GroupLabel, ListItem, NodeItem, PictureItem, SectionHeaderItem,
    TableCell as DoclingCell, TableData, TableItem, TextItem, TitleItem,
)

from agent_lab.knowledge.processing.contracts import (
    ContentBlock, OutlineEntry, ParsedDocument, ProcessingIssue, TableCell, TableContent,
)


def export_structure(document: DoclingDocument) -> tuple[tuple[ContentBlock, ...], tuple[OutlineEntry, ...], tuple[ProcessingIssue, ...]]:
    """保留阅读顺序和组合节点；用标题栈补出 Markdown 的章节归属。"""
    items = [item for item, _ in document.iterate_items(root=document.body, with_groups=True)]
    items = [item for item in items if item.self_ref != document.body.self_ref]
    ids = {item.self_ref: f"block-{index}" for index, item in enumerate(items)}
    blocks: list[ContentBlock] = []
    outline: list[OutlineEntry] = []
    headings: list[OutlineEntry] = []
    issues: list[ProcessingIssue] = []
    for item in items:
        block_id = ids[item.self_ref]
        parent_id = ids.get(item.parent.cref) if item.parent else None
        values = dict(id=block_id, parent_id=parent_id, text=getattr(item, "text", ""))
        if isinstance(item, TitleItem | SectionHeaderItem):
            level = item.level + 1 if isinstance(item, SectionHeaderItem) else 1
            while headings and headings[-1].level >= level:
                headings.pop()
            entry = OutlineEntry(id=block_id, title=item.text, level=level, parent_id=headings[-1].id if headings else None)
            outline.append(entry)
            headings.append(entry)
            values.update(kind="heading", level=level)
        elif isinstance(item, GroupItem):
            group_kind = item.label.value if item.label.value in {"inline", "list", "ordered_list", "section"} else "group"
            values.update(kind="group", group_kind=group_kind)
        elif isinstance(item, TableItem):
            cells = tuple(TableCell(
                text=cell.text, row_start=cell.start_row_offset_idx, row_end=cell.end_row_offset_idx,
                column_start=cell.start_col_offset_idx, column_end=cell.end_col_offset_idx,
                column_header=cell.column_header, row_header=cell.row_header,
            ) for cell in item.data.table_cells)
            values.update(kind="table", text=item.export_to_markdown(document), table=TableContent(rows=item.data.num_rows, columns=item.data.num_cols, cells=cells))
        elif isinstance(item, CodeItem):
            values.update(kind="code", language=item.code_language.value)
        elif isinstance(item, ListItem):
            values.update(kind="list_item", enumerated=item.enumerated, marker=item.marker)
        elif isinstance(item, PictureItem):
            values.update(kind="image", text=item.caption_text(document))
        elif isinstance(item, TextItem):
            values.update(kind="formula" if item.label == DocItemLabel.FORMULA else "paragraph")
        else:
            issues.append(ProcessingIssue(code="unsupported_content_block", block_ids=(block_id,)))
            values.update(kind="paragraph")
        values["heading_ids"] = tuple(heading.id for heading in headings)
        blocks.append(ContentBlock(**values))
    return tuple(blocks), tuple(outline), tuple(issues)


def import_structure(parsed: ParsedDocument) -> tuple[DoclingDocument, dict[str, str]]:
    """从可持久化的项目结构重建切分输入，返回 SDK 引用到版本内块身份的映射。"""
    document = DoclingDocument(name=parsed.title or "document")
    nodes: dict[str, NodeItem] = {}
    source_ids: dict[str, str] = {}
    for block in parsed.blocks:
        parent = nodes.get(block.parent_id) if block.parent_id else None
        if block.kind == "heading":
            if block.level == 1:
                node = document.add_text(label=DocItemLabel.TITLE, text=block.text, parent=parent)
            else:
                node = document.add_heading(text=block.text, level=block.level - 1, parent=parent)
        elif block.kind == "group":
            label = GroupLabel.UNSPECIFIED if block.group_kind == "group" else GroupLabel(block.group_kind)
            node = document.add_group(label=label, parent=parent)
        elif block.kind == "table":
            table = block.table
            cells = [DoclingCell(
                text=cell.text, start_row_offset_idx=cell.row_start, end_row_offset_idx=cell.row_end,
                start_col_offset_idx=cell.column_start, end_col_offset_idx=cell.column_end,
                row_span=cell.row_end - cell.row_start, col_span=cell.column_end - cell.column_start,
                column_header=cell.column_header, row_header=cell.row_header,
            ) for cell in table.cells]
            node = document.add_table(data=TableData(num_rows=table.rows, num_cols=table.columns, table_cells=cells), parent=parent)
        elif block.kind == "code":
            node = document.add_code(text=block.text, code_language=CodeLanguageLabel(block.language) if block.language else None, parent=parent)
        elif block.kind == "list_item":
            node = document.add_list_item(text=block.text, enumerated=block.enumerated, marker=block.marker, parent=parent)
        else:
            label = DocItemLabel.FORMULA if block.kind == "formula" else DocItemLabel.TEXT
            node = document.add_text(label=label, text=block.text, parent=parent)
        nodes[block.id] = node
        source_ids[node.self_ref] = block.id
    return document, source_ids
