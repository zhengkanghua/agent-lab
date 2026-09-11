"""对象存储适配器和处理记录模型的边界测试。"""

from hashlib import sha256
import asyncio
from io import BytesIO
from botocore.exceptions import ClientError

import pytest

from agent_lab.config.object_storage import ObjectStorageSettings
from agent_lab.knowledge.storage import ObjectReference, ObjectStorageError, S3ObjectStorage


def test_storage_requires_private_endpoint_when_enabled():
    with pytest.raises(ObjectStorageError, match="object_storage_not_configured"):
        S3ObjectStorage(ObjectStorageSettings(_env_file=None, endpoint=None, required=True))


def test_storage_can_be_constructed_for_minio_path_style():
    store = S3ObjectStorage(ObjectStorageSettings(endpoint="http://minio:9000", access_key="minio"))
    assert store is not None


class VersionedS3:
    """协议替身保存真实字节与版本，能模拟写成功但回执丢失。"""
    def __init__(self):
        self.objects, self.writes, self.deletes, self.bodies = {}, [], [], []
        self.close_count = 0
        self.lose_receipt = False

    def put_object(self, **kwargs):
        if kwargs["Key"] in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        version = f"version-{len(self.writes) + 1}"
        self.writes.append(kwargs)
        self.objects[kwargs["Key"]] = (kwargs["Body"], version)
        if self.lose_receipt:
            self.lose_receipt = False
            raise TimeoutError()
        return {"VersionId": version}

    def get_object(self, **kwargs):
        value = self.objects.get(kwargs["Key"])
        if value is None or kwargs.get("VersionId", value[1]) != value[1]:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = BytesIO(value[0])
        self.bodies.append(body)
        return {"Body": body, "VersionId": value[1]}

    def delete_object(self, **kwargs):
        self.deletes.append(kwargs)
        if kwargs.get("VersionId") == self.objects[kwargs["Key"]][1]:
            del self.objects[kwargs["Key"]]

    def close(self):
        self.close_count += 1


@pytest.fixture
def s3(monkeypatch):
    store = S3ObjectStorage(ObjectStorageSettings(endpoint="http://minio:9000", access_key="minio"))
    client = VersionedS3()
    monkeypatch.setattr(store, "_client", lambda: client)
    return store, client


def test_s3_put_verifies_bytes_and_never_overwrites_original(s3):
    store, client = s3
    data = "正文".encode()
    reference = asyncio.run(store.put("documents/1/source/a", data, content_type="text/markdown"))
    assert reference == ObjectReference("documents/1/source/a", len(data), sha256(data).hexdigest(), "version-1")
    assert asyncio.run(store.put(reference.key, data, content_type="text/markdown")) == reference
    with pytest.raises(ObjectStorageError, match="object_storage_content_mismatch"):
        asyncio.run(store.put(reference.key, b"other bytes", content_type="text/plain"))
    assert len(client.writes) == 1 and client.writes[0]["ContentType"] == "text/markdown"
    assert client.close_count == 3 and all(body.closed for body in client.bodies)


def test_lost_s3_receipt_is_recovered_from_same_object_and_version(s3):
    store, client = s3
    client.lose_receipt = True
    with pytest.raises(ObjectStorageError, match="object_storage_put_failed"):
        asyncio.run(store.put("original", b"body", content_type="text/plain"))
    reference = asyncio.run(store.inspect("original"))
    assert reference == ObjectReference("original", 4, sha256(b"body").hexdigest(), "version-1")
    assert asyncio.run(store.put("original", b"body", content_type="text/plain")) == reference
    assert len(client.writes) == 1


def test_delete_without_saved_version_resolves_and_deletes_actual_version(s3):
    store, client = s3
    asyncio.run(store.put("original", b"body", content_type="text/plain"))
    asyncio.run(store.delete("original"))
    asyncio.run(store.delete("original"))
    assert [item["VersionId"] for item in client.deletes] == ["version-1"]
    assert asyncio.run(store.inspect("original")) is None
    assert all(body.closed for body in client.bodies)
