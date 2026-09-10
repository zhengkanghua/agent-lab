"""锁定 BGE-M3 tokenizer 的来源与文件摘要，不包含模型权重。"""

from hashlib import file_digest
from pathlib import Path

from agent_lab.knowledge.processing.contracts import DocumentProcessingError
from agent_lab.knowledge.processing.specification import TOKENIZER_ID, TOKENIZER_REVISION

BGE_M3_TOKENIZER = TOKENIZER_ID
BGE_M3_TOKENIZER_REVISION = TOKENIZER_REVISION
TOKENIZER_FILES = {
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",
    "tokenizer_config.json": "a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
    "config.json": "26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa",
}


def verify_tokenizer_resources(directory: str | Path) -> Path:
    path = Path(directory)
    for filename, expected in TOKENIZER_FILES.items():
        try:
            with (path / filename).open("rb") as stream:
                actual = file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise DocumentProcessingError("document_tokenizer_unavailable") from exc
        if actual != expected:
            raise DocumentProcessingError("document_tokenizer_mismatch")
    return path


def prepare_tokenizer_resources(directory: str | Path) -> Path:
    """部署准备时显式下载四份锁定资源；运行时只调用本地核验。"""
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=BGE_M3_TOKENIZER, revision=BGE_M3_TOKENIZER_REVISION,
        allow_patterns=list(TOKENIZER_FILES), local_dir=directory,
    )
    return verify_tokenizer_resources(directory)
