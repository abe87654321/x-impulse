"""备份链路端到端验证：目录轮转 + KMS 加密 + 密钥销毁不可复活。

使用假 S3 / 假 Vault 传输；真实环境仍需客户的备份任务与凭据（A-19）。
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kb.compliance.backup_domain import CatalogBackupRotation
from kb.compliance.backup_s3 import S3BackupCatalog, S3NotFoundError, S3ObjectSummary
from kb.compliance.crypto import KeyDestroyedError
from kb.compliance.kms import KmsKeyDestroyedError, KmsKeyManager, SubjectKeyRow
from kb.compliance.kms_vault import VaultTransitKmsClient, VaultTransportError

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

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


class FakeVault:
    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}

    async def request(
        self, method: str, path: str, *, body: Mapping[str, object] | None = None
    ) -> Mapping[str, object] | None:
        parts = path.strip("/").split("/")
        operation, name = parts[2], parts[3]
        if operation == "keys":
            if method == "POST":
                self.keys.setdefault(name, {"wrapped": {}})
                return None
            if name not in self.keys:
                raise VaultTransportError("key not found", status=404)
            if method == "DELETE":
                del self.keys[name]
                return None
            return {"data": {"name": name}}
        record = self.keys.get(name)
        if record is None:
            raise VaultTransportError("encryption key not found", status=400)
        wrapped: dict[str, bytes] = record["wrapped"]
        if body is None:
            raise VaultTransportError("missing body", status=400)
        if operation == "encrypt":
            token = uuid.uuid4().hex
            wrapped[token] = base64.b64decode(str(body["plaintext"]))
            return {"data": {"ciphertext": f"vault:v1:{token}"}}
        token = str(body["ciphertext"]).rsplit(":", 1)[-1]
        value = wrapped.get(token)
        if value is None:
            raise VaultTransportError("ciphertext not found", status=400)
        return {"data": {"plaintext": base64.b64encode(value).decode("ascii")}}


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], SubjectKeyRow] = {}

    async def get(self, tenant_id: str, subject_key: str) -> SubjectKeyRow | None:
        return self.rows.get((tenant_id, subject_key))

    async def insert(self, tenant_id: str, subject_key: str, dek_ref: str, kek_ref: str) -> None:
        self.rows.setdefault((tenant_id, subject_key), SubjectKeyRow(dek_ref, None))

    async def mark_destroyed(
        self, tenant_id: str, subject_key: str, evidence: str, tombstone: str
    ) -> bool:
        row = self.rows.get((tenant_id, subject_key))
        if row is None or row.destroyed_at is not None:
            return False
        self.rows[(tenant_id, subject_key)] = SubjectKeyRow(tombstone, NOW)
        return True


def _manifest(backup_id: str, tenant: str, days: int) -> bytes:
    return json.dumps(
        {
            "backup_id": backup_id,
            "tenant_id": tenant,
            "created_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(days=days)).isoformat(),
            "scope": "tenant",
            "encryption_key_ref": "vault-transit://backup",
        }
    ).encode()


async def test_backup_rotation_deletes_due_generations_only():
    s3 = FakeS3(
        {
            "kb/t1/due/kb-manifest.json": _manifest("due", "t1", -1),
            "kb/t1/due/payload.bin": b"old",
            "kb/t1/later/kb-manifest.json": _manifest("later", "t1", 30),
            "kb/t1/later/payload.bin": b"new",
            "kb/t2/other/kb-manifest.json": _manifest("other", "t2", -1),
        }
    )
    rotation = CatalogBackupRotation(
        S3BackupCatalog(s3, bucket="bucket", prefix="kb"), rotation_days=90
    )

    status = await rotation.rotation_status("t1", now=NOW)
    expired = await rotation.rotate("t1", now=NOW)

    assert status.pending == 1
    assert expired == 2
    assert "kb/t1/later/kb-manifest.json" in s3.objects
    assert "kb/t2/other/kb-manifest.json" in s3.objects
    assert "kb/t1/due/kb-manifest.json" not in s3.objects


async def test_backup_payload_encryption_key_cannot_be_resurrected():
    store = MemoryStore()
    client = VaultTransitKmsClient(FakeVault())
    manager = KmsKeyManager(client, store)
    await manager.register("t1", "backup-subject")
    ciphertext = await manager.encrypt("t1", "backup-subject", "backup payload")
    original_row = store.rows[("t1", "backup-subject")]

    assert await manager.decrypt("t1", "backup-subject", ciphertext) == "backup payload"
    assert await manager.destroy("t1", "backup-subject", "retention-expired") is True

    # 模拟从删除前的数据库备份恢复 subject_keys 行；KMS 侧密钥已销毁。
    store.rows[("t1", "backup-subject")] = original_row
    assert await manager.is_destroyed("t1", "backup-subject") is True
    with pytest.raises(KeyDestroyedError):
        await manager.decrypt("t1", "backup-subject", ciphertext)
    with pytest.raises(KmsKeyDestroyedError):
        await client.unwrap("missing", "vault:v1:x")
