"""原文对象存储：确定性键、租户/文档隔离与前缀删除。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from kb.compliance.backup_s3 import S3NotFoundError, S3ObjectSummary
from kb.indexing.raw_store import (
    InMemoryRawObjectStore,
    S3RawObjectStore,
    build_raw_store_from_env,
)


class FakeTransport:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(
        self, bucket: str, key: str, data: bytes, *, content_type: str | None = None
    ) -> None:
        del bucket, content_type
        self.objects[key] = data

    async def get_object(self, bucket: str, key: str) -> bytes:
        del bucket
        if key not in self.objects:
            raise S3NotFoundError(key)
        return self.objects[key]

    async def list_objects(
        self, bucket: str, *, prefix: str, delimiter: str | None = None, continuation_token=None
    ) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
        del bucket, delimiter, continuation_token
        return [S3ObjectSummary(k) for k in self.objects if k.startswith(prefix)], [], None

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> int:
        del bucket
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        del bucket
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)


async def test_s3_raw_store_roundtrip_and_prefix_delete():
    transport = FakeTransport()
    store = S3RawObjectStore(transport, bucket="kb", prefix="raw")

    key = await store.put("t1", "doc_1", "hash1", b"payload", content_type="text/plain")

    assert key == "raw/t1/doc_1/hash1"
    assert await store.get("t1", "doc_1", "hash1") == b"payload"
    assert await store.delete_document("t1", "doc_1") == 1
    assert await store.get("t1", "doc_1", "hash1") is None


async def test_delete_document_does_not_cross_tenant_or_document():
    transport = FakeTransport()
    store = S3RawObjectStore(transport, bucket="kb", prefix="raw")
    await store.put("t1", "doc_1", "h1", b"a")
    await store.put("t1", "doc_2", "h1", b"b")
    await store.put("t2", "doc_1", "h1", b"c")

    assert await store.delete_document("t1", "doc_1") == 1
    assert await store.get("t1", "doc_2", "h1") == b"b"
    assert await store.get("t2", "doc_1", "h1") == b"c"


@pytest.mark.parametrize("tenant,doc", [("", "d"), ("t1", "d/2"), ("t1", "../d")])
async def test_path_traversal_is_rejected(tenant, doc):
    store = S3RawObjectStore(FakeTransport(), bucket="kb", prefix="raw")

    with pytest.raises(ValueError):
        await store.put(tenant, doc, "h", b"x")


async def test_in_memory_store_matches_s3_semantics():
    store = InMemoryRawObjectStore()
    key = await store.put("t1", "doc_1", "h", b"x")

    assert key == "raw/t1/doc_1/h"
    assert await store.get("t1", "doc_1", "h") == b"x"
    assert await store.delete_document("t1", "doc_1") == 1
    assert await store.get("t1", "doc_1", "h") is None


def test_build_raw_store_from_env():
    assert build_raw_store_from_env({}) is None
    assert build_raw_store_from_env({"KB_RAW_OBJECT_STORE": "none"}) is None
    with pytest.raises(ValueError):
        build_raw_store_from_env({"KB_RAW_OBJECT_STORE": "s3"})
    with pytest.raises(ValueError):
        build_raw_store_from_env({"KB_RAW_OBJECT_STORE": "unknown"})

    store = build_raw_store_from_env(
        {
            "KB_RAW_OBJECT_STORE": "s3",
            "KB_RAW_OBJECT_BUCKET": "kb-raw",
            "KB_S3_ENDPOINT": "https://s3.example.com",
            "KB_S3_ACCESS_KEY_ID": "key",
            "KB_S3_SECRET_ACCESS_KEY": "secret",
        }
    )
    assert isinstance(store, S3RawObjectStore)
