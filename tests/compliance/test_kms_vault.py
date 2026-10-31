"""Vault Transit KMS 适配器 —— 假传输测试（不连真实 Vault）。"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from kb.compliance.crypto import KeyDestroyedError
from kb.compliance.kms import KmsError, KmsKeyDestroyedError, KmsKeyManager, SubjectKeyRow
from kb.compliance.kms_vault import (
    KmsConfigError,
    VaultTransitKmsClient,
    VaultTransportError,
    build_kms_from_env,
)


class FakeVaultTransport:
    """按 Vault Transit 语义模拟传输层；密钥删除后旧密文不可解。"""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []
        self.unavailable = False

    async def request(
        self, method: str, path: str, *, body: Mapping[str, object] | None = None
    ) -> Mapping[str, object] | None:
        self.calls.append((method, path, body))
        if self.unavailable:
            raise VaultTransportError("Vault 不可用：connection refused")
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "v1":
            raise VaultTransportError(f"unexpected path: {path}", status=500)
        operation, name = parts[2], parts[3]
        if operation == "keys":
            return self._keys(method, name)
        if operation in {"encrypt", "decrypt"}:
            return self._crypto(operation, name, body)
        raise VaultTransportError(f"unexpected path: {path}", status=500)

    def _keys(self, method: str, name: str) -> Mapping[str, object] | None:
        if method == "POST":
            self.keys.setdefault(name, {"wrapped": {}})
            return None
        if name not in self.keys:
            raise VaultTransportError("key not found", status=404)
        if method == "GET":
            return {"data": {"name": name, "type": "aes256-gcm96", "deletion_allowed": True}}
        if method == "DELETE":
            del self.keys[name]
            return None
        raise VaultTransportError(f"unexpected method: {method}", status=500)

    def _crypto(
        self, operation: str, name: str, body: Mapping[str, object] | None
    ) -> Mapping[str, object]:
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
        plaintext = wrapped.get(token)
        if plaintext is None:
            raise VaultTransportError("ciphertext not found", status=400)
        return {"data": {"plaintext": base64.b64encode(plaintext).decode("ascii")}}


class _MemorySubjectKeyStore:
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
        self.rows[(tenant_id, subject_key)] = SubjectKeyRow(tombstone, datetime.now(UTC))
        return True


def _manager() -> tuple[KmsKeyManager, VaultTransitKmsClient, FakeVaultTransport]:
    transport = FakeVaultTransport()
    client = VaultTransitKmsClient(transport)
    return KmsKeyManager(client, _MemorySubjectKeyStore()), client, transport


async def test_roundtrip_uses_vault_transit_paths():
    manager, _, transport = _manager()

    await manager.register("t1", "subject_a")
    ciphertext = await manager.encrypt("t1", "subject_a", "13800138000")

    assert await manager.decrypt("t1", "subject_a", ciphertext) == "13800138000"
    assert any(
        method == "POST" and path.startswith("/v1/transit/keys/")
        for method, path, _ in transport.calls
    )
    assert any(
        method == "POST" and path.startswith("/v1/transit/encrypt/")
        for method, path, _ in transport.calls
    )
    decrypt_bodies = [
        body
        for method, path, body in transport.calls
        if method == "POST" and path.startswith("/v1/transit/decrypt/")
    ]
    assert decrypt_bodies
    assert str(decrypt_bodies[0]["ciphertext"]).startswith("vault:v1:")


async def test_create_key_is_deterministic_and_reuses_name():
    _, client, transport = _manager()

    first = await client.create_key("t1", "subject_a")
    second = await client.create_key("t1", "subject_a")

    assert first == second
    assert first.startswith("kb-subject-")
    assert len(transport.keys) == 1


async def test_destroy_permanently_breaks_unwrap():
    manager, client, transport = _manager()
    await manager.register("t1", "subject_a")
    ciphertext = await manager.encrypt("t1", "subject_a", "secret")
    key_ref = next(iter(transport.keys))

    assert await manager.destroy("t1", "subject_a", "PIPL-47") is True
    assert await manager.is_destroyed("t1", "subject_a") is True
    assert await client.key_state(key_ref) == "MISSING"
    with pytest.raises(KmsKeyDestroyedError):
        await client.unwrap(key_ref, ciphertext)
    with pytest.raises(KeyDestroyedError):
        await manager.decrypt("t1", "subject_a", ciphertext)


async def test_key_state_reports_missing_for_unknown_key():
    _, client, _ = _manager()

    assert await client.key_state("kb-subject-does-not-exist") == "MISSING"


async def test_unavailable_vault_fails_closed_without_plaintext_fallback():
    manager, _, transport = _manager()
    await manager.register("t1", "subject_a")
    ciphertext = await manager.encrypt("t1", "subject_a", "secret")

    transport.unavailable = True

    with pytest.raises(KmsError):
        await manager.decrypt("t1", "subject_a", ciphertext)
    with pytest.raises(KmsError):
        await manager.register("t1", "subject_b")


def test_build_kms_from_env_requires_provider():
    with pytest.raises(KmsConfigError):
        build_kms_from_env({})
    with pytest.raises(KmsConfigError):
        build_kms_from_env({"KB_KMS_PROVIDER": "software"})


def test_build_kms_from_env_requires_vault_credentials():
    with pytest.raises(KmsConfigError):
        build_kms_from_env({"KB_KMS_PROVIDER": "vault", "KB_VAULT_ADDR": "http://vault:8200"})
    with pytest.raises(KmsConfigError):
        build_kms_from_env({"KB_KMS_PROVIDER": "vault", "KB_VAULT_TOKEN": "token"})


def test_build_kms_from_env_returns_vault_client():
    client = build_kms_from_env(
        {
            "KB_KMS_PROVIDER": "vault",
            "KB_VAULT_ADDR": "http://vault:8200",
            "KB_VAULT_TOKEN": "s.test",
            "KB_VAULT_TRANSIT_MOUNT": "kb-transit",
        }
    )

    assert isinstance(client, VaultTransitKmsClient)
