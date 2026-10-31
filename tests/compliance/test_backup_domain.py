"""备份目录领域层与适配器：租户过滤、轮转、manifest/HTTP 扩展点。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kb.compliance.backup_adapters import (
    BackupCatalogRegistry,
    CatalogDependencies,
    HttpBackupCatalog,
    InMemoryBackupCatalog,
    InMemoryManifestSource,
    ManifestBackupCatalog,
    default_backup_registry,
)
from kb.compliance.backup_domain import (
    BackupCatalogError,
    BackupGeneration,
    CatalogBackupRotation,
)
from kb.compliance.backup_policy import (
    build_rotation_from_policy,
    resolve_backup_policy,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _generation(
    backup_id: str,
    *,
    tenant_id: str | None = "t1",
    scope: str = "tenant",
    expires_in: int = 30,
) -> BackupGeneration:
    return BackupGeneration(
        backup_id=backup_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=expires_in),
        scope=scope,  # type: ignore[arg-type]
        tenant_id=tenant_id,
    )


async def test_catalog_filters_by_tenant_and_includes_shared():
    catalog = InMemoryBackupCatalog(
        generations=[
            _generation("b_own"),
            _generation("b_other", tenant_id="t2"),
            _generation("b_shared", tenant_id=None, scope="shared"),
        ]
    )

    result = await catalog.list_generations("t1", now=NOW)

    assert [generation.backup_id for generation in result] == ["b_own", "b_shared"]


async def test_rotation_reports_expiry_status_and_expires_due():
    catalog = InMemoryBackupCatalog(
        generations=[
            _generation("b_due", expires_in=5),
            _generation("b_later", expires_in=60),
        ]
    )
    rotation = CatalogBackupRotation(catalog, rotation_days=90)

    outcome = await rotation.notify_erasure("t1", scope_type="document", scope_key="k", now=NOW)
    status = await rotation.rotation_status("t1", now=NOW)
    expired = await rotation.rotate("t1", now=NOW + timedelta(days=10))

    assert outcome.status == "LOGICAL_DONE"
    assert outcome.expires_at == NOW + timedelta(days=60)
    assert status.pending == 2
    assert expired == 1
    assert catalog.expired == {"b_due"}


async def test_rotation_without_matching_generations_still_reports():
    rotation = CatalogBackupRotation(InMemoryBackupCatalog(), rotation_days=90)

    outcome = await rotation.notify_erasure("t1", scope_type="document", scope_key="k", now=NOW)

    assert outcome.status == "LOGICAL_DONE"
    assert "无匹配" in outcome.detail


async def test_manifest_catalog_parses_rows_and_filters_tenant():
    source = InMemoryManifestSource(
        rows=(
            {
                "backup_id": "b1",
                "tenant_id": "t1",
                "created_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(days=30)).isoformat(),
                "scope": "tenant",
            },
            {
                "backup_id": "b2",
                "tenant_id": "t2",
                "created_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(days=30)).isoformat(),
            },
        )
    )
    catalog = ManifestBackupCatalog(source)

    result = await catalog.list_generations("t1", now=NOW)

    assert [generation.backup_id for generation in result] == ["b1"]


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, object]] = []

    async def request(self, method, path, *, tenant_id, body):
        self.calls.append((method, path, tenant_id, body))
        if method == "GET":
            return {
                "items": [
                    {
                        "backup_id": "api_b1",
                        "tenant_id": "t1",
                        "created_at": NOW.isoformat(),
                        "expires_at": (NOW + timedelta(days=10)).isoformat(),
                    }
                ]
            }
        return {"expired": len(body["backup_ids"])}  # type: ignore[index]


async def test_http_catalog_uses_transport_and_expire_endpoint():
    transport = _FakeTransport()
    catalog = HttpBackupCatalog(
        transport, endpoint="https://backup.local/api", credential_ref="kms://ref"
    )

    generations = await catalog.list_generations("t1", now=NOW)
    expired = await catalog.expire(["api_b1"], tenant_id="t1", evidence="rotation")

    assert [generation.backup_id for generation in generations] == ["api_b1"]
    assert expired == 1
    assert transport.calls[0][:3] == (
        "GET",
        "https://backup.local/api/generations",
        "t1",
    )
    assert transport.calls[1][1] == "https://backup.local/api/expire"


async def test_registry_supports_custom_adapter_extension():
    registry = BackupCatalogRegistry()
    custom = InMemoryBackupCatalog()
    registry.register("vendor_x", "catalog", lambda settings, deps: custom)

    created = registry.create("vendor_x", "catalog", {}, CatalogDependencies())

    assert created is custom


async def test_default_registry_rejects_unregistered_pair():
    with pytest.raises(BackupCatalogError):
        default_backup_registry().create("vendor_x", "catalog", {}, CatalogDependencies())


async def test_build_rotation_from_policy_uses_injected_manifest():
    policy = resolve_backup_policy({})
    source = InMemoryManifestSource(
        rows=(
            {
                "backup_id": "b1",
                "tenant_id": "t1",
                "created_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(days=30)).isoformat(),
            },
        )
    )

    rotation = build_rotation_from_policy(
        policy, dependencies=CatalogDependencies(manifest_source=source)
    )
    status = await rotation.rotation_status("t1", now=NOW)

    assert status.pending == 1
