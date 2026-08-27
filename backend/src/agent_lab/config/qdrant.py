"""定义 Qdrant 向量存储的连接和 Collection 命名配置。

本模块只把环境变量解析为可校验的设置，不负责创建 Collection、切换 Alias、写入
Point 或执行检索。Qdrant 的 Collection（向量集合）保存 Vector 和 Payload；应用
业务读写只能使用 ``current`` Alias（别名指针），物理 Collection 名称只由生命周期
管理代码使用。
"""

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class QdrantSettings(BaseSettings):
    """访问 Qdrant 服务和定位当前索引代次所需的进程级配置。

    一个实例通常在进程内缓存并复用；它不持有 Qdrant 网络连接。API Key 使用
    ``SecretStr``，因此配置对象的 ``repr`` 不显示明文。``collection_name`` 是实际
    保存 Point 的物理 Collection，``collection_alias`` 是应用运行时唯一访问入口。
    """

    base_url: AnyHttpUrl = Field(
        default="http://localhost:6333",
        description=(
            "Qdrant HTTP API 根地址，来源于 QDRANT_BASE_URL；必须是合法 HTTP(S) URL，"
            "不包含 Collection 名称；可带显式端口，省略时按 URL scheme 使用 80/443。"
        ),
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description=(
            "Qdrant 可选 API Key，来源于 QDRANT_API_KEY；允许为空，明文不得出现在日志、"
            "异常或 repr 中。"
        ),
    )
    request_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description=(
            "单次 Qdrant HTTP 请求的超时秒数，来源于 QDRANT_REQUEST_TIMEOUT_SECONDS；"
            "必须是大于等于 1 的整数。"
        ),
    )
    environment: str = Field(
        default="dev",
        min_length=1,
        description=(
            "运行环境短名称，来源于 QDRANT_ENVIRONMENT；会进入 Collection 和 Alias 名称，"
            "用于避免开发、测试、生产数据互相混用。"
        ),
    )
    collection_schema_version: str = Field(
        default="v1",
        min_length=2,
        description=(
            "向量索引契约版本，来源于 QDRANT_COLLECTION_SCHEMA_VERSION；模型、维度、"
            "Distance、Chunk 规则或 Payload 不兼容变化时必须递增。"
        ),
    )
    collection_generation: int = Field(
        default=1,
        ge=1,
        description=(
            "同一 Schema 版本的物理 Collection 代次，来源于 QDRANT_COLLECTION_GENERATION；"
            "全量重建时递增，不改变应用使用的 current Alias。"
        ),
    )
    write_batch_size: int = Field(
        default=64,
        gt=0,
        description=(
            "一次 Qdrant upsert 最多发送的 Point 数，来源于 QDRANT_WRITE_BATCH_SIZE；"
            "用于控制请求体大小、延迟和服务端内存。"
        ),
    )
    vector_dimension: int = Field(
        default=1024,
        gt=0,
        description=(
            "当前 Qdrant dense Vector 的维度，来源于 QDRANT_VECTOR_DIMENSION；本项目阶段 2 "
            "已由真实 Ollama 探测确认是 1024，写入前仍会与真实 Embedding 长度核对。"
        ),
    )
    distance: str = Field(
        default="Cosine",
        description=(
            "Qdrant Distance metric（距离度量），来源于 QDRANT_DISTANCE；阶段 2 固定使用 "
            "Cosine，其他算法需要新 Schema 版本和重新评测。"
        ),
    )

    @field_validator("environment")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        """规范化环境名，并拒绝会破坏命名边界的字符。

        Args:
            value: 环境变量中的原始环境短名称。

        Returns:
            小写、去除首尾空白的环境名。

        Raises:
            ValueError: 名称不是字母、数字、下划线或连字符组成，或规范化后为空。
        """

        normalized = value.strip().lower()
        if not normalized or not all(
            character.isalnum() or character in {"_", "-"}
            for character in normalized
        ):
            raise ValueError(
                "Qdrant 环境名只能包含字母、数字、'_' 或 '-'。"
            )
        return normalized

    @field_validator("collection_schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        """校验索引 Schema 版本使用简单的 ``v数字`` 格式。"""

        normalized = value.strip().lower()
        if len(normalized) < 2 or normalized[0] != "v" or not normalized[1:].isdigit():
            raise ValueError("Qdrant 集合 schema 版本必须形如 v1 或 v2。")
        return normalized

    @field_validator("distance")
    @classmethod
    def validate_distance(cls, value: str) -> str:
        """只接受当前阶段明确支持的 Cosine 距离度量。"""

        normalized = value.strip().lower()
        if normalized != "cosine":
            raise ValueError(
                "schema v1 中 Qdrant 距离度量固定为 Cosine；"
                "要修改它需要新的索引 schema 版本。"
            )
        return "Cosine"

    @property
    def collection_name(self) -> str:
        """返回真正保存 Vector 和 Payload 的物理 Collection 名称。

        名字由 environment + schema_version + generation 拼成（如
        news_chunks_langchain_v1_001），只在生命周期组件里使用；业务读写不碰它。
        """

        return (
            f"news_chunks_{self.environment}_{self.collection_schema_version}_"
            f"{self.collection_generation:03d}"
        )

    @property
    def collection_alias(self) -> str:
        """返回所有应用数据读写使用的稳定 ``current`` Alias 名称。

        Alias 只是一个「指针」，不存数据。应用写 Point、搜索都只通过这个 Alias，
        部署时把 Alias 原子切换到新的物理 Collection，就能零停机换索引——应用代码
        完全不用改。
        """

        return f"news_chunks_{self.environment}_current"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="QDRANT_",
        extra="ignore",
    )


@lru_cache
def get_qdrant_settings() -> QdrantSettings:
    """读取并缓存 Qdrant 配置，不进行网络或数据库 I/O。

    Returns:
        已完成 URL、密钥类型、命名参数、超时和批量大小校验的进程级配置。

    Raises:
        pydantic.ValidationError: 环境变量不满足字段约束时抛出。
    """

    return QdrantSettings()
