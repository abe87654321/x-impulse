"""KMS 适配层 —— 信封加密与“备份回滚不能复活已销毁密钥”。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kb.compliance.crypto import KeyDestroyedError
from kb.compliance.kms import (
    FakeKmsClient,
    KmsKeyManager,
    SubjectKeyRow,
)


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


async def _manager() -> tuple[KmsKeyManager, FakeKmsClient, _MemorySubjectKeyStore]:
    client = FakeKmsClient()
    store = _MemorySubjectKeyStore()
    return KmsKeyManager(client, store), client, store


async def test_register_encrypt_decrypt_roundtrip():
    manager, client, _ = await _manager()
    await manager.register("t1", "subject_a")

    ciphertext = await manager.encrypt("t1", "subject_a", "13800138000")

    assert ciphertext != "13800138000"
    assert await manager.decrypt("t1", "subject_a", ciphertext) == "13800138000"
    assert client.calls.count("create_key") == 1


async def test_register_is_idempotent():
    manager, client, _ = await _manager()

    await manager.register("t1", "subject_a")
    first = await manager.encrypt("t1", "subject_a", "x")
    await manager.register("t1", "subject_a")

    assert await manager.decrypt("t1", "subject_a", first) == "x"
    assert client.calls.count("create_key") == 1


async def test_destroyed_key_cannot_be_used():
    manager, _, _ = await _manager()
    await manager.register("t1", "subject_a")
    ciphertext = await manager.encrypt("t1", "subject_a", "secret")

    assert await manager.destroy("t1", "subject_a", "PIPL-47") is True
    assert await manager.is_destroyed("t1", "subject_a") is True

    with pytest.raises(KeyDestroyedError):
        await manager.decrypt("t1", "subject_a", ciphertext)


async def test_restoring_database_rows_cannot_resurrect_destroyed_key():
    manager, _, store = await _manager()
    await manager.register("t1", "subject_a")
    original = store.rows[("t1", "subject_a")]
    ciphertext = await manager.encrypt("t1", "subject_a", "secret")
    await manager.destroy("t1", "subject_a", "PIPL-47")

    # 模拟从删除前的数据库备份恢复：行看起来仍然有效，但 KMS 侧已销毁。
    store.rows[("t1", "subject_a")] = original

    assert await manager.is_destroyed("t1", "subject_a") is True
    with pytest.raises(KeyDestroyedError):
        await manager.decrypt("t1", "subject_a", ciphertext)


async def test_unknown_subject_is_rejected_not_plaintext():
    manager, _, _ = await _manager()

    with pytest.raises(KeyDestroyedError):
        await manager.encrypt("t1", "subject_missing", "x")


async def test_disabled_kms_key_fails_closed():
    manager, client, _ = await _manager()
    await manager.register("t1", "subject_a")
    key_ref = next(iter(client._states))
    ciphertext = await manager.encrypt("t1", "subject_a", "x")
    client._states[key_ref] = "DISABLED"

    with pytest.raises(KeyDestroyedError):
        await manager.decrypt("t1", "subject_a", ciphertext)
