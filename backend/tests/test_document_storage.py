"""对象存储适配器和处理记录模型的边界测试。"""

from hashlib import sha256
import asyncio

import pytest

from agent_lab.config.object_storage import ObjectStorageSettings
from agent_lab.knowledge.storage import ObjectReference, ObjectStorageError, S3ObjectStorage


def test_storage_requires_private_endpoint_when_enabled():
    with pytest.raises(ObjectStorageError, match="object_storage_not_configured"):
        S3ObjectStorage(ObjectStorageSettings())


def test_storage_can_be_constructed_for_minio_path_style():
    store = S3ObjectStorage(ObjectStorageSettings(endpoint="http://minio:9000", access_key="minio"))
    assert store is not None


def test_s3_put_returns_immutable_content_reference(monkeypatch):
    store = S3ObjectStorage(ObjectStorageSettings(endpoint="http://minio:9000", access_key="minio"))
    seen = {}

    class FakeClient:
        def put_object(self, **kwargs):
            seen.update(kwargs)
            return {"VersionId": "version-1"}

    monkeypatch.setattr(store, "_client", lambda: FakeClient())
    data = "正文".encode()
    reference = asyncio.run(store.put("documents/1/source/a", data, content_type="text/markdown"))

    assert reference == ObjectReference("documents/1/source/a", len(data), sha256(data).hexdigest(), "version-1")
    assert seen["Body"] == data
    assert seen["ContentType"] == "text/markdown"
