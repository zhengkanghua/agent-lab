"""只读取已提交的文本字节；保留 Markdown 原文，以成熟解析器派生索引文本。"""

from hashlib import sha256
from pathlib import PurePosixPath

from markdown_it import MarkdownIt

from agent_lab.knowledge.files import FILE_MIME_TYPES, MAX_FILE_BYTES, FileDocumentError, TextFile


def parse_text_file(filename: str, data: bytes) -> TextFile:
    """上传与替换共用校验；无效文件不会进入业务事务。"""
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise FileDocumentError("file_name_invalid")
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in FILE_MIME_TYPES:
        raise FileDocumentError("file_format_unsupported")
    if len(data) > MAX_FILE_BYTES:
        raise FileDocumentError("file_too_large")
    try:
        content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise FileDocumentError("file_encoding_invalid") from None
    if "\0" in content:
        raise FileDocumentError("file_content_invalid")
    if not content.strip():
        raise FileDocumentError("file_empty")
    return TextFile(
        filename=name, title=PurePosixPath(name).stem.strip() or name.strip(),
        mime_type=FILE_MIME_TYPES[suffix], content_text=content,
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
    )


def markdown_index_text(content: str) -> str:
    """从 Markdown AST 取文本与代码，不下载链接或图片，也不执行 HTML。

    数据库只保存原文；这份派生文本只存在于索引流水线。代码缩进和重复段落保留，
    不使用 FreshRSS 的正文去重和清洗规则。位置无法与原文对应时，阅读器不猜测高亮。
    """
    blocks: list[str] = []
    for token in MarkdownIt("commonmark").enable("table").parse(content):
        if token.type in {"fence", "code_block", "html_block"}:
            blocks.append(token.content.rstrip("\n"))
        elif token.type == "inline":
            parts: list[str] = []
            for child in token.children or []:
                if child.type in {"text", "code_inline"}:
                    parts.append(child.content)
                elif child.type in {"softbreak", "hardbreak"}:
                    parts.append("\n")
                elif child.type == "image":
                    parts.append(f"[图片说明：{child.content}；图片内容未读取]")
            if parts:
                blocks.append("".join(parts))
    # 仅有分隔符等合法 Markdown 仍保留为文本，不制造空索引或额外拒绝规则。
    return "\n\n".join(blocks) or content
