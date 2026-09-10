"""文档处理实现的可替换运行配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DocumentProcessingSettings(BaseSettings):
    tokenizer_path: Path = Field(default=Path(".cache/tokenizers/bge-m3"))
    chunk_max_tokens: int = Field(default=512, gt=8, le=8192)
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="DOCUMENT_", extra="ignore")


@lru_cache
def get_document_processing_settings() -> DocumentProcessingSettings:
    return DocumentProcessingSettings()
