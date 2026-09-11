"""文件入口只校验格式、名称和大小，字节解析由统一处理组件执行。"""

from pathlib import PurePosixPath

from agent_lab.knowledge.files import FILE_MIME_TYPES, MAX_FILE_BYTES, FileDocumentError, TextFile


def parse_text_file(filename: str, data: bytes) -> TextFile:
    """上传与替换共用入口校验；原件内容异常由后台保留记录并转人工处理。"""
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise FileDocumentError("file_name_invalid")
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in FILE_MIME_TYPES:
        raise FileDocumentError("file_format_unsupported")
    if len(data) > MAX_FILE_BYTES:
        raise FileDocumentError("file_too_large")
    return TextFile(
        filename=name, title=PurePosixPath(name).stem.strip() or name.strip(),
        mime_type=FILE_MIME_TYPES[suffix], raw_bytes=bytes(data),
    )
