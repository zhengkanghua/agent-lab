"""原始资料对象存储端口及 MinIO/S3 实现。"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from agent_lab.config.object_storage import ObjectStorageSettings


@dataclass(frozen=True, slots=True)
class ObjectReference:
    key: str
    size: int
    sha256: str
    version_id: str | None = None


class ObjectStorageError(Exception):
    """对象写入、读取或删除失败。"""


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectReference: ...
    async def get(self, key: str, *, version_id: str | None = None) -> bytes: ...
    async def delete(self, key: str, *, version_id: str | None = None) -> None: ...


class S3ObjectStorage:
    """使用 boto3 的同步 SDK，并将阻塞 I/O 移出 asyncio 事件循环。"""

    def __init__(self, settings: ObjectStorageSettings):
        if not settings.endpoint:
            if settings.required:
                raise ObjectStorageError("object_storage_not_configured")
            raise ObjectStorageError("object_storage_disabled")
        self._settings = settings

    def _client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - 安装依赖时不会走这里
            raise ObjectStorageError("object_storage_dependency_missing") from exc
        return boto3.client(
            "s3", endpoint_url=self._settings.endpoint, region_name=self._settings.region,
            aws_access_key_id=self._settings.access_key,
            aws_secret_access_key=self._settings.secret_key.get_secret_value(),
            config=Config(s3={"addressing_style": self._settings.addressing_style}),
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectReference:
        from hashlib import sha256
        digest = sha256(data).hexdigest()

        def write():
            try:
                response = self._client().put_object(Bucket=self._settings.bucket, Key=key, Body=data, ContentType=content_type)
                return ObjectReference(key=key, size=len(data), sha256=digest, version_id=response.get("VersionId"))
            except Exception as exc:
                raise ObjectStorageError("object_storage_put_failed") from exc
        return await asyncio.to_thread(write)

    async def get(self, key: str, *, version_id: str | None = None) -> bytes:
        def read():
            try:
                kwargs = {"Bucket": self._settings.bucket, "Key": key}
                if version_id:
                    kwargs["VersionId"] = version_id
                return self._client().get_object(**kwargs)["Body"].read()
            except Exception as exc:
                raise ObjectStorageError("object_storage_get_failed") from exc
        return await asyncio.to_thread(read)

    async def delete(self, key: str, *, version_id: str | None = None) -> None:
        def remove():
            try:
                kwargs = {"Bucket": self._settings.bucket, "Key": key}
                if version_id:
                    kwargs["VersionId"] = version_id
                self._client().delete_object(**kwargs)
            except Exception as exc:
                raise ObjectStorageError("object_storage_delete_failed") from exc
        await asyncio.to_thread(remove)
