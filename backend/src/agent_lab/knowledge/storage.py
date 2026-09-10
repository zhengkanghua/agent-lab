"""原始资料对象存储端口及 MinIO/S3 实现。"""

import asyncio
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
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

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def verify_object_bytes(reference: ObjectReference, data: bytes) -> None:
    """摘要独立于 S3 ETag；读取原件与写入回执均须符合接收意图。"""
    if len(data) != reference.size or sha256(data).hexdigest() != reference.sha256:
        raise ObjectStorageError("object_storage_content_mismatch")


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectReference: ...
    async def get(self, key: str, *, version_id: str | None = None) -> bytes: ...
    async def delete(self, key: str, *, version_id: str | None = None) -> None: ...
    async def inspect(self, key: str, *, version_id: str | None = None) -> ObjectReference | None: ...


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
            config=Config(s3={"addressing_style": self._settings.addressing_style}, retries={"total_max_attempts": 1},
                          connect_timeout=10, read_timeout=60),
        )

    def _read(self, client, key, version_id=None):
        """按明确版本读取并关闭响应体；不存在与网络异常分别表达。"""
        from botocore.exceptions import ClientError
        arguments = {"Bucket": self._settings.bucket, "Key": key}
        if version_id is not None:
            arguments["VersionId"] = version_id
        try:
            response = client.get_object(**arguments)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion", "NotFound", "404"}:
                return None
            raise
        with closing(response["Body"]) as body:
            data = body.read()
        return data, ObjectReference(key, len(data), sha256(data).hexdigest(), response.get("VersionId"))

    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectReference:
        from botocore.exceptions import ClientError
        digest = sha256(data).hexdigest()

        def write():
            try:
                with closing(self._client()) as client:
                    version_id = None
                    try:
                        response = client.put_object(
                            Bucket=self._settings.bucket, Key=key, Body=data, ContentType=content_type,
                            IfNoneMatch="*", Metadata={"sha256": digest},
                        )
                        version_id = response.get("VersionId")
                    except ClientError as exc:
                        if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
                            raise
                        # 上次可能已写成功但没收到回执；核对原对象，不能覆盖或另造版本。
                    observed = self._read(client, key, version_id)
                    if observed is None:
                        raise ObjectStorageError("object_storage_content_mismatch")
                    reference = observed[1]
                    if reference.size != len(data) or reference.sha256 != digest:
                        raise ObjectStorageError("object_storage_content_mismatch")
                    return reference
            except ObjectStorageError:
                raise
            except Exception:
                raise ObjectStorageError("object_storage_put_failed") from None
        return await asyncio.to_thread(write)

    async def get(self, key: str, *, version_id: str | None = None) -> bytes:
        def read():
            try:
                with closing(self._client()) as client:
                    observed = self._read(client, key, version_id)
                    if observed is None:
                        raise ObjectStorageError("object_storage_get_failed")
                    return observed[0]
            except Exception:
                raise ObjectStorageError("object_storage_get_failed") from None
        return await asyncio.to_thread(read)

    async def inspect(self, key: str, *, version_id: str | None = None) -> ObjectReference | None:
        """恢复未决接收时核对字节摘要；S3 ETag 或用户元数据不能代替回读核验。"""
        def inspect_object():
            try:
                with closing(self._client()) as client:
                    observed = self._read(client, key, version_id)
                    return observed[1] if observed is not None else None
            except Exception:
                raise ObjectStorageError("object_storage_inspect_failed") from None
        return await asyncio.to_thread(inspect_object)

    async def delete(self, key: str, *, version_id: str | None = None) -> None:
        def remove():
            try:
                with closing(self._client()) as client:
                    observed = self._read(client, key, version_id)
                    if observed is None:
                        return
                    actual_version = observed[1].version_id
                    kwargs = {"Bucket": self._settings.bucket, "Key": key}
                    if actual_version is not None:
                        kwargs["VersionId"] = actual_version
                    client.delete_object(**kwargs)
                    if self._read(client, key, actual_version) is not None:
                        raise ObjectStorageError("object_storage_delete_failed")
            except Exception:
                raise ObjectStorageError("object_storage_delete_failed") from None
        await asyncio.to_thread(remove)
