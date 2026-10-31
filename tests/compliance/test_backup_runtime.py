"""按租户设置选择备份轮转；未显式配置不得假装已接入。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from kb.compliance.backup_adapters import CatalogDependencies
from kb.compliance.backup_runtime import PolicyBackupRotation
from kb.compliance.backup_s3 import S3NotFoundError, S3ObjectSummary
from kb.governance.settings_store import InMemorySettingsStore

NOW = datetime(2026, 6, 1, tzinfo=UTC)
TENANT = "t1"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        delimiter: str | None = None,
        continuation_token: str | None = None,
    ) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
        del bucket, continuation_token
        objects: list[S3ObjectSummary] = []
        common: set[str] = set()
        for key in self.objects:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if delimiter and delimiter in rest:
                common.add(f"{prefix}{rest.split(delimiter, 1)[0]}{delimiter}")
            else:
                objects.append(S3ObjectSummary(key))
        return objects, sorted(common), None

    async def get_object(self, bucket: str, key: str) -> bytes:
        del bucket
        if key not in self.objects:
            raise S3NotFoundError(key)
        return self.objects[key]

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> int:
        del bucket
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


def _manifest(backup_id: str, days: int) -> bytes:
    return json.dumps(
        {
            "backup_id": backup_id,
            "tenant_id": TENANT,
            "created_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(days=days)).isoformat(),
            "scope": "tenant",
        }
    ).encode()


def _s3_settings() -> dict[str, object]:
    return {
        "backup_system": "object_storage",
        "rotation_days": 90,
        "persistence_location": "external_catalog",
        "scheduler": "k8s_cronjob",
        "tenant_isolation": "per_tenant",
        "catalog_mode": "s3_api",
        "catalog_endpoint": "https://s3.example.com",
        "catalog_credential_ref": "kms://s3",
        "catalog_bucket": "bucket",
        "catalog_prefix": "kb",
        "backup_encryption": "subject_dek",
        "authority_store": "kms_and_external_db",
    }


async def test_unconfigured_tenant_stays_unconfigured():
    rotation = PolicyBackupRotation(InMemorySettingsStore())

    outcome = await rotation.notify_erasure(TENANT, scope_type="document", scope_key="k", now=NOW)

    assert outcome.status == "SKIPPED"
    assert (await rotation.rotation_status(TENANT, now=NOW)).configured is False


async def test_tenant_settings_drive_s3_catalog_rotation():
    store = InMemorySettingsStore()
    await store.put(TENANT, "backup_system", "object_storage", "admin")
    for key, value in _s3_settings().items():
        await store.put(TENANT, key, value, "admin")
    s3 = FakeS3(
        {
            "kb/t1/due/kb-manifest.json": _manifest("due", -1),
            "kb/t1/due/data.bin": b"old",
            "kb/t1/later/kb-manifest.json": _manifest("later", 30),
        }
    )
    rotation = PolicyBackupRotation(store, CatalogDependencies(s3_transport=s3))

    outcome = await rotation.notify_erasure(TENANT, scope_type="document", scope_key="k", now=NOW)
    status = await rotation.rotation_status(TENANT, now=NOW)
    expired = await rotation.rotate(TENANT, now=NOW)

    assert outcome.status == "LOGICAL_DONE"
    assert status.pending == 1
    assert expired == 2
    assert "kb/t1/later/kb-manifest.json" in s3.objects
