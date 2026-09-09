"""解析、审核预览与采用共享的不可变数据，不携带第三方文档对象。"""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessingValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProcessingIssue(ProcessingValue):
    """可持久化的质量原因；不保存解析器异常文本。"""

    code: str
    block_ids: tuple[str, ...] = ()


class OutlineEntry(ProcessingValue):
    id: str
    title: str
    level: int
    parent_id: str | None = None


class TableCell(ProcessingValue):
    """表格格子使用半开行列区间，合并单元格无需压成字符串。"""

    text: str
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    column_header: bool = False
    row_header: bool = False


class TableContent(ProcessingValue):
    rows: int
    columns: int
    cells: tuple[TableCell, ...]


class ContentBlock(ProcessingValue):
    """按阅读顺序保存内容；父节点表达列表/行内组合，章节路径另行表达。"""

    id: str
    kind: Literal["heading", "paragraph", "list_item", "code", "table", "image", "group", "formula"]
    text: str = ""
    parent_id: str | None = None
    heading_ids: tuple[str, ...] = ()
    level: int | None = None
    group_kind: Literal["inline", "list", "ordered_list", "section", "group"] = "group"
    enumerated: bool = False
    marker: str | None = None
    language: str | None = None
    table: TableContent | None = None


class ParsedDocument(ProcessingValue):
    """审核正文与结构属于同一份解析结果；块标识仅在该版本内有效。"""

    title: str
    body: str
    text_format: Literal["markdown", "plain"]
    parser: str
    blocks: tuple[ContentBlock, ...]
    outline: tuple[OutlineEntry, ...] = ()
    issues: tuple[ProcessingIssue, ...] = ()


class ChunkSpecification(ProcessingValue):
    algorithm: str
    tokenizer: str
    tokenizer_revision: str
    max_tokens: int = Field(gt=0)


class PreviewChunk(ProcessingValue):
    """展示与向量化文本显式分开；采用只能消费这份已冻结的清单。"""

    sequence: int
    text: str
    embedding_text: str
    token_count: int
    block_ids: tuple[str, ...]
    heading_ids: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()


class ChunkResult(ProcessingValue):
    specification: ChunkSpecification
    chunks: tuple[PreviewChunk, ...]
    issues: tuple[ProcessingIssue, ...] = ()


class DocumentPreview(ProcessingValue):
    document: ParsedDocument
    chunk_result: ChunkResult

    @property
    def issues(self) -> tuple[ProcessingIssue, ...]:
        return self.document.issues + self.chunk_result.issues

    @property
    def fingerprint(self) -> str:
        """正文、结构、规格或 Chunk 任一改变都会使旧预览失效。"""
        return sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class DocumentProcessingError(Exception):
    """处理失败只向业务层提供稳定原因，外部异常通过 cause 留在边界。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
