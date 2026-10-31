"""备份目录适配器 —— 领域端口的具体实现与注册表。

新增备份厂商/目录形态时，只需实现 `BackupCatalog` 并注册工厂；
领域层与轮转语义不变。
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from kb.compliance.backup_domain import (
    BackupCatalog,
    BackupCatalogError,
    BackupGeneration,
)
from kb.compliance.backup_s3 import S3BackupCatalog, S3Transport


class ManifestSource(Protocol):
    async def read(self, tenant_id: str) -> Sequence[Mapping[str, object]]: ...


class HttpTransport(Protocol):
    async def request(
        self, method: str, path: str, *, tenant_id: str, body: Mapping[str, object] | None
    ) -> object: ...


@dataclass
class InMemoryBackupCatalog:
    generations: list[BackupGeneration] = field(default_factory=list)
    expired: set[str] = field(default_factory=set)
    expire_calls: int = 0

    async def list_generations(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> Sequence[BackupGeneration]:
        del now
        return [
            generation
            for generation in self.generations
            if generation.backup_id not in self.expired
            and (generation.scope != "tenant" or generation.tenant_id == tenant_id)
        ]

    async def expire(self, backup_ids: Sequence[str], *, tenant_id: str, evidence: str) -> int:
        del tenant_id, evidence
        self.expire_calls += 1
        self.expired.update(backup_ids)
        return len(backup_ids)


@dataclass(frozen=True)
class InMemoryManifestSource:
    rows: tuple[Mapping[str, object], ...]

    async def read(self, tenant_id: str) -> Sequence[Mapping[str, object]]:
        del tenant_id
        return self.rows


class FileManifestSource:
    """读取本地 JSON 数组或 JSONL manifest；文件不存在视为空目录。"""

    def __init__(self, path: str) -> None:
        self._path = path

    async def read(self, tenant_id: str) -> Sequence[Mapping[str, object]]:
        del tenant_id
        return await asyncio.to_thread(_read_manifest, self._path)


class ManifestBackupCatalog:
    """基于 manifest 的目录。到期标记只在进程内生效，真实删除依赖存储生命周期。"""

    def __init__(self, source: ManifestSource) -> None:
        self._source = source
        self._expired: set[str] = set()

    async def list_generations(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> Sequence[BackupGeneration]:
        del now
        rows = await self._source.read(tenant_id)
        result: list[BackupGeneration] = []
        for row in rows:
            generation = _parse_generation(row)
            if generation.backup_id in self._expired:
                continue
            if generation.scope != "tenant" or generation.tenant_id == tenant_id:
                result.append(generation)
        return result

    async def expire(self, backup_ids: Sequence[str], *, tenant_id: str, evidence: str) -> int:
        del tenant_id, evidence
        self._expired.update(backup_ids)
        return len(backup_ids)


class HttpBackupCatalog:
    """通用目录 API 适配器；请求路径可由厂商适配器覆盖。"""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        endpoint: str,
        credential_ref: str | None = None,
    ) -> None:
        self._transport = transport
        self._endpoint = endpoint.rstrip("/")
        self._credential_ref = credential_ref

    async def list_generations(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> Sequence[BackupGeneration]:
        del now
        payload = await self._transport.request(
            "GET",
            f"{self._endpoint}/generations",
            tenant_id=tenant_id,
            body=None,
        )
        items = _items(payload)
        return [_parse_generation(item) for item in items]

    async def expire(self, backup_ids: Sequence[str], *, tenant_id: str, evidence: str) -> int:
        payload = await self._transport.request(
            "POST",
            f"{self._endpoint}/expire",
            tenant_id=tenant_id,
            body={
                "tenant_id": tenant_id,
                "backup_ids": list(backup_ids),
                "evidence": evidence,
            },
        )
        if not isinstance(payload, Mapping):
            raise BackupCatalogError("目录 API expire 返回不是对象")
        count = payload.get("expired")
        return int(count) if isinstance(count, int) else len(backup_ids)


class UrllibHttpTransport:
    """最小 HTTP 传输；认证头由部署层构造，不从设置项读取密钥。"""

    def __init__(self, base_url: str, headers: Mapping[str, str] | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = dict(headers or {})

    async def request(
        self, method: str, path: str, *, tenant_id: str, body: Mapping[str, object] | None
    ) -> object:
        return await asyncio.to_thread(self._request, method, path, tenant_id, body)

    def _request(
        self,
        method: str,
        path: str,
        tenant_id: str,
        body: Mapping[str, object] | None,
    ) -> object:
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json", **self._headers}
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise BackupCatalogError(f"目录 API 调用失败：{exc}") from exc


@dataclass(frozen=True)
class CatalogDependencies:
    manifest_source: ManifestSource | None = None
    http_transport: HttpTransport | None = None
    s3_transport: S3Transport | None = None


CatalogFactory = Callable[[Mapping[str, object], CatalogDependencies], BackupCatalog]


class BackupCatalogRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], CatalogFactory] = {}

    def register(self, provider: str, mode: str, factory: CatalogFactory) -> None:
        self._factories[(provider, mode)] = factory

    def create(
        self,
        provider: str,
        mode: str,
        settings: Mapping[str, object],
        dependencies: CatalogDependencies,
    ) -> BackupCatalog:
        factory = self._factories.get((provider, mode))
        if factory is None:
            raise BackupCatalogError(f"未注册的备份目录适配器：{provider}/{mode}")
        return factory(settings, dependencies)


def default_backup_registry() -> BackupCatalogRegistry:
    registry = BackupCatalogRegistry()
    providers = ("database_pitr", "object_storage", "vm_snapshot", "external_vendor")
    for provider in providers:
        registry.register(provider, "manifest", _manifest_factory)
        registry.register(provider, "http_api", _http_factory)
        registry.register(provider, "s3_api", _s3_factory)
    return registry


def _manifest_factory(
    settings: Mapping[str, object], dependencies: CatalogDependencies
) -> BackupCatalog:
    source = dependencies.manifest_source
    if source is None:
        uri = settings.get("manifest_uri")
        if not isinstance(uri, str) or not uri.strip():
            raise BackupCatalogError("manifest_uri 未配置")
        source = FileManifestSource(_path_from_uri(uri))
    return ManifestBackupCatalog(source)


def _http_factory(
    settings: Mapping[str, object], dependencies: CatalogDependencies
) -> BackupCatalog:
    transport = dependencies.http_transport
    if transport is None:
        raise BackupCatalogError("http_api 模式必须注入 HttpTransport（凭据由部署层提供）")
    endpoint = settings.get("catalog_endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BackupCatalogError("catalog_endpoint 未配置")
    credential_ref = settings.get("catalog_credential_ref")
    return HttpBackupCatalog(
        transport,
        endpoint=endpoint,
        credential_ref=credential_ref if isinstance(credential_ref, str) else None,
    )


def _s3_factory(settings: Mapping[str, object], dependencies: CatalogDependencies) -> BackupCatalog:
    transport = dependencies.s3_transport
    if transport is None:
        raise BackupCatalogError("s3_api 模式必须注入 S3Transport（凭据由部署层提供）")
    bucket = settings.get("catalog_bucket")
    if not isinstance(bucket, str) or not bucket.strip():
        raise BackupCatalogError("catalog_bucket 未配置")
    prefix = settings.get("catalog_prefix")
    return S3BackupCatalog(
        transport,
        bucket=bucket,
        prefix=prefix if isinstance(prefix, str) else "",
    )


def _read_manifest(path: str) -> tuple[Mapping[str, object], ...]:
    if not os.path.exists(path):
        return ()
    with open(path, encoding="utf-8") as handle:
        content = handle.read().strip()
    if not content:
        return ()
    if content.startswith("["):
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            raise BackupCatalogError("manifest 顶层不是数组")
        return tuple(item for item in parsed if isinstance(item, dict))
    rows: list[Mapping[str, object]] = []
    for line in content.splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return tuple(rows)


def _path_from_uri(uri: str) -> str:
    return uri[len("file://") :] if uri.startswith("file://") else uri


def _items(payload: object) -> Sequence[Mapping[str, object]]:
    if isinstance(payload, Mapping):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    raise BackupCatalogError("目录 API 返回缺少 items")


def _parse_generation(row: Mapping[str, object]) -> BackupGeneration:
    try:
        return BackupGeneration(
            backup_id=str(row["backup_id"]),
            created_at=_datetime(row["created_at"]),
            expires_at=_datetime(row["expires_at"]),
            scope=str(row.get("scope", "tenant")),  # type: ignore[arg-type]
            tenant_id=_optional_str(row.get("tenant_id")),
            encryption_key_ref=_optional_str(row.get("encryption_key_ref")),
            status=str(row.get("status", "available")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupCatalogError(f"备份代次字段不合法：{exc}") from exc


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("时间字段不是 ISO 字符串")


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "BackupCatalogRegistry",
    "CatalogDependencies",
    "CatalogFactory",
    "FileManifestSource",
    "HttpBackupCatalog",
    "HttpTransport",
    "InMemoryBackupCatalog",
    "InMemoryManifestSource",
    "ManifestBackupCatalog",
    "ManifestSource",
    "UrllibHttpTransport",
    "default_backup_registry",
]
