"""原文对象存储 —— FR-12-04 §5.1 / A-15 的 raw_object 位置。

对象键是确定性的：`<prefix>/<tenant_id>/<doc_id>/<content_hash>`。
删除按文档前缀进行，并包含 S3 版本历史；不依赖数据库映射表。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol

from kb.compliance.backup_s3 import (
    S3NotFoundError,
    S3Transport,
    build_s3_transport_from_env,
)


class RawObjectStore(Protocol):
    async def put(
        self,
        tenant_id: str,
        doc_id: str,
        content_hash: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> str: ...

    async def get(self, tenant_id: str, doc_id: str, content_hash: str) -> bytes | None: ...

    async def delete_document(self, tenant_id: str, doc_id: str) -> int: ...


class InMemoryRawObjectStore:
    """测试用；进程重启即丢失。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], tuple[bytes, str | None]] = {}

    async def put(
        self,
        tenant_id: str,
        doc_id: str,
        content_hash: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> str:
        key = _key(tenant_id, doc_id, content_hash)
        self.objects[(tenant_id, doc_id, content_hash)] = (data, content_type)
        return key

    async def get(self, tenant_id: str, doc_id: str, content_hash: str) -> bytes | None:
        item = self.objects.get((tenant_id, doc_id, content_hash))
        return None if item is None else item[0]

    async def delete_document(self, tenant_id: str, doc_id: str) -> int:
        keys = [key for key in self.objects if key[0] == tenant_id and key[1] == doc_id]
        for key in keys:
            del self.objects[key]
        return len(keys)


class S3RawObjectStore:
    def __init__(self, transport: S3Transport, *, bucket: str, prefix: str = "raw") -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket 不能为空")
        self._transport = transport
        self._bucket = bucket
        self._prefix = _normalise_prefix(prefix)

    async def put(
        self,
        tenant_id: str,
        doc_id: str,
        content_hash: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> str:
        key = f"{self._doc_prefix(tenant_id, doc_id)}{_validate_part(content_hash, 'content_hash')}"
        await self._transport.put_object(self._bucket, key, data, content_type=content_type)
        return key

    async def get(self, tenant_id: str, doc_id: str, content_hash: str) -> bytes | None:
        key = f"{self._doc_prefix(tenant_id, doc_id)}{_validate_part(content_hash, 'content_hash')}"
        try:
            return await self._transport.get_object(self._bucket, key)
        except S3NotFoundError:
            return None

    async def delete_document(self, tenant_id: str, doc_id: str) -> int:
        prefix = self._doc_prefix(tenant_id, doc_id)
        return await self._transport.delete_prefix(self._bucket, prefix)

    def _doc_prefix(self, tenant_id: str, doc_id: str) -> str:
        return (
            f"{self._prefix}{_validate_part(tenant_id, 'tenant_id')}/"
            f"{_validate_part(doc_id, 'doc_id')}/"
        )


def build_raw_store_from_env(
    environ: Mapping[str, str] | None = None,
) -> RawObjectStore | None:
    """按部署环境装配原文存储；显式 `none` 才表示不落原文。"""
    env = os.environ if environ is None else environ
    provider = env.get("KB_RAW_OBJECT_STORE", "").strip().lower()
    if provider in {"", "none"}:
        return None
    if provider == "s3":
        bucket = env.get("KB_RAW_OBJECT_BUCKET", "").strip()
        if not bucket:
            raise ValueError("KB_RAW_OBJECT_STORE=s3 时必须配置 KB_RAW_OBJECT_BUCKET")
        return S3RawObjectStore(
            build_s3_transport_from_env(env),
            bucket=bucket,
            prefix=env.get("KB_RAW_OBJECT_PREFIX") or "raw",
        )
    raise ValueError(f"未知 KB_RAW_OBJECT_STORE：{provider}")


def _key(tenant_id: str, doc_id: str, content_hash: str) -> str:
    return f"raw/{tenant_id}/{doc_id}/{content_hash}"


def _normalise_prefix(prefix: str) -> str:
    cleaned = prefix.strip().strip("/")
    if not cleaned:
        return ""
    parts = cleaned.split("/")
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise ValueError(f"prefix 含非法路径段：{prefix!r}")
    return f"{cleaned}/"


def _validate_part(value: str, name: str) -> str:
    if not value or value in {".", ".."}:
        raise ValueError(f"{name} 不能为空或相对路径")
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"{name} 含非法字符：{value!r}")
    return value


__all__ = [
    "InMemoryRawObjectStore",
    "RawObjectStore",
    "S3RawObjectStore",
    "build_raw_store_from_env",
]
