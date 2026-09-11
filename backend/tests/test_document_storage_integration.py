"""显式启用的 S3 原件验收，只创建和删除随机测试前缀中的一个对象。"""

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from agent_lab.config.object_storage import ObjectStorageSettings
from agent_lab.knowledge.storage import ObjectStorageError, S3ObjectStorage


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_S3_INTEGRATION_TEST") != "1",
    reason="需要显式授权已有私有桶中的随机原件读写和删除。",
)


def test_real_s3_original_roundtrip_and_conditional_write() -> None:
    """覆盖字节一致、幂等写入、冲突不覆盖和按实际对象版本删除；不改变桶配置。"""
    settings = ObjectStorageSettings()
    if not settings.endpoint:
        pytest.fail("请先配置测试可用的 S3_ENDPOINT 与私有桶凭据。", pytrace=False)
    storage = S3ObjectStorage(settings)
    key = f"acceptance/docling/{uuid4()}/original.md"
    data = b"\xef\xbb\xbf" + "# 原件验收\r\n\r\n保留 BOM、中文和原始换行。\r\n".encode("utf-8")
    report = {"key": key, "stage": "created", "cleaned": False}

    async def verify() -> None:
        reference = None
        try:
            report["stage"] = "write"
            reference = await storage.put(key, data, content_type="text/markdown")
            assert reference.size == len(data) and reference.sha256 == sha256(data).hexdigest()
            report["versioned"] = reference.version_id is not None
            report["stage"] = "read_and_repeat"
            assert await storage.get(key, version_id=reference.version_id) == data
            assert await storage.inspect(key, version_id=reference.version_id) == reference
            assert await storage.put(key, data, content_type="text/markdown") == reference
            report["stage"] = "conflict"
            with pytest.raises(ObjectStorageError) as error:
                await storage.put(key, b"different bytes", content_type="text/markdown")
            assert error.value.code == "object_storage_content_mismatch"
            assert await storage.get(key, version_id=reference.version_id) == data
            report["stage"] = "delete"
            await storage.delete(key, version_id=reference.version_id)
            assert await storage.inspect(key, version_id=reference.version_id) is None
            assert await storage.inspect(key) is None
            report.update(stage="completed", cleaned=True)
        finally:
            # 回执可能丢失，仍按本次唯一键核对清理；不列桶、不碰任何其他对象。
            if not report["cleaned"]:
                await storage.delete(key, version_id=reference.version_id if reference else None)
                report["cleaned"] = await storage.inspect(key) is None

    try:
        asyncio.run(verify())
    finally:
        # 中断后保留精确测试键，便于核实清理结果；不记录 endpoint、凭据或响应正文。
        output = Path(".pytest_cache/docling-s3-report.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
