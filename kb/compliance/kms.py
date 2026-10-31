"""KMS 密钥管理适配层 —— FR-11-04 / 删除设计 §6。

目标：把主密钥（KEK）从进程环境挪进密码模块/KMS，并让密钥销毁状态
**独立于业务数据库的备份**。这样即使数据库被恢复到删除之前的备份，
KMS 侧已销毁的密钥也无法再解开密文。

本模块只定义通用接缝与假实现，不绑定厂商。真实适配器需实现 `KmsClient`
（AWS KMS / Azure Key Vault / GCP KMS / Vault Transit / 国内云 KMS / HSM）。
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kb.compliance.crypto import KeyDestroyedError
from kb.database import dsn_from_env

KMS_KEK_REF = "kms:v1"
_TOMBSTONE = "[destroyed]"


class KmsError(Exception):
    """KMS 调用失败。调用方必须拒绝继续处理，不得回退明文。"""


class KmsKeyDestroyedError(KmsError):
    """KMS 侧密钥已销毁，密文不可解。"""


@dataclass(frozen=True)
class SubjectKeyRow:
    dek_ref: str
    destroyed_at: datetime | None


class SubjectKeyStore(Protocol):
    async def get(self, tenant_id: str, subject_key: str) -> SubjectKeyRow | None: ...

    async def insert(
        self, tenant_id: str, subject_key: str, dek_ref: str, kek_ref: str
    ) -> None: ...

    async def mark_destroyed(
        self, tenant_id: str, subject_key: str, evidence: str, tombstone: str
    ) -> bool: ...


class KmsClient(Protocol):
    """厂商适配器接缝。密钥引用与包裹后的字节都由 KMS 保管/处理。"""

    async def create_key(self, tenant_id: str, subject_key: str) -> str: ...

    async def wrap(self, key_ref: str, plaintext: bytes) -> str: ...

    async def unwrap(self, key_ref: str, wrapped: str) -> bytes: ...

    async def destroy_key(self, key_ref: str) -> str: ...

    async def key_state(self, key_ref: str) -> str: ...


class FakeKmsClient:
    """进程内假 KMS。只用于测试；进程退出后状态消失。

    行为与真实 KMS 的关键一致性：密钥销毁后 `unwrap` 永久失败，
    不因为调用方数据库里仍有一行"未销毁"的记录而复活。
    """

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._wrapped: dict[tuple[str, str], bytes] = {}
        self.calls: list[str] = []

    async def create_key(self, tenant_id: str, subject_key: str) -> str:
        self.calls.append("create_key")
        key_ref = f"fake-kms-{uuid.uuid4().hex}"
        self._states[key_ref] = "ENABLED"
        return key_ref

    async def wrap(self, key_ref: str, plaintext: bytes) -> str:
        self.calls.append("wrap")
        self._require_enabled(key_ref)
        token = uuid.uuid4().hex
        self._wrapped[(key_ref, token)] = plaintext
        return token

    async def unwrap(self, key_ref: str, wrapped: str) -> bytes:
        self.calls.append("unwrap")
        self._require_enabled(key_ref)
        value = self._wrapped.get((key_ref, wrapped))
        if value is None:
            raise KmsError("包裹密文不存在")
        return value

    async def destroy_key(self, key_ref: str) -> str:
        self.calls.append("destroy_key")
        if key_ref not in self._states:
            raise KmsError("密钥不存在")
        self._states[key_ref] = "DESTROYED"
        for key in [item for item in self._wrapped if item[0] == key_ref]:
            del self._wrapped[key]
        return f"fake-kms-evidence:{key_ref}"

    async def key_state(self, key_ref: str) -> str:
        self.calls.append("key_state")
        return self._states.get(key_ref, "MISSING")

    def _require_enabled(self, key_ref: str) -> None:
        state = self._states.get(key_ref)
        if state == "DESTROYED":
            raise KmsKeyDestroyedError(key_ref)
        if state != "ENABLED":
            raise KmsError(f"密钥状态不可用：{state}")


class KmsKeyManager:
    """用 KMS 包裹主体 DEK；数据本身仍由本地 AES-GCM 加密（信封加密）。"""

    def __init__(
        self,
        client: KmsClient,
        store: SubjectKeyStore,
        *,
        kek_ref: str = KMS_KEK_REF,
    ) -> None:
        self._client = client
        self._store = store
        self._kek_ref = kek_ref

    async def register(self, tenant_id: str, subject_key: str) -> None:
        existing = await self._store.get(tenant_id, subject_key)
        if existing is not None and existing.destroyed_at is None:
            return
        key_ref = await self._client.create_key(tenant_id, subject_key)
        dek = AESGCM.generate_key(bit_length=256)
        wrapped = await self._client.wrap(key_ref, dek)
        await self._store.insert(
            tenant_id,
            subject_key,
            _encode_ref(key_ref, wrapped),
            self._kek_ref,
        )

    async def encrypt(self, tenant_id: str, subject_key: str, plaintext: str) -> str:
        dek = await self._load_dek(tenant_id, subject_key)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    async def decrypt(self, tenant_id: str, subject_key: str, ciphertext: str) -> str:
        dek = await self._load_dek(tenant_id, subject_key)
        raw = base64.b64decode(ciphertext)
        return AESGCM(dek).decrypt(raw[:12], raw[12:], None).decode("utf-8")

    async def destroy(self, tenant_id: str, subject_key: str, evidence: str) -> bool:
        row = await self._store.get(tenant_id, subject_key)
        if row is None or row.destroyed_at is not None or row.dek_ref == _TOMBSTONE:
            return False
        key_ref, _wrapped = _decode_ref(row.dek_ref)
        kms_evidence = await self._client.destroy_key(key_ref)
        return await self._store.mark_destroyed(
            tenant_id, subject_key, f"{evidence}; {kms_evidence}", _TOMBSTONE
        )

    async def is_destroyed(self, tenant_id: str, subject_key: str) -> bool:
        row = await self._store.get(tenant_id, subject_key)
        if row is None:
            return False
        if row.destroyed_at is not None or row.dek_ref == _TOMBSTONE:
            return True
        key_ref, _wrapped = _decode_ref(row.dek_ref)
        return await self._client.key_state(key_ref) != "ENABLED"

    async def _load_dek(self, tenant_id: str, subject_key: str) -> bytes:
        row = await self._store.get(tenant_id, subject_key)
        if row is None or row.destroyed_at is not None or row.dek_ref == _TOMBSTONE:
            raise KeyDestroyedError(f"主体密钥不可用：{subject_key}")
        key_ref, wrapped = _decode_ref(row.dek_ref)
        state = await self._client.key_state(key_ref)
        if state != "ENABLED":
            raise KeyDestroyedError(f"KMS 密钥不可用：{state}")
        try:
            return await self._client.unwrap(key_ref, wrapped)
        except KmsKeyDestroyedError as exc:
            raise KeyDestroyedError(f"KMS 密钥已销毁：{key_ref}") from exc


class PostgresSubjectKeyStore:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def get(self, tenant_id: str, subject_key: str) -> SubjectKeyRow | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_SELECT_SQL, tenant_id, subject_key)
        finally:
            await conn.close()
        if row is None:
            return None
        return SubjectKeyRow(
            dek_ref=str(row["dek_ref"]),
            destroyed_at=row["destroyed_at"],
        )

    async def insert(self, tenant_id: str, subject_key: str, dek_ref: str, kek_ref: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(_INSERT_SQL, tenant_id, subject_key, dek_ref, kek_ref)
        finally:
            await conn.close()

    async def mark_destroyed(
        self, tenant_id: str, subject_key: str, evidence: str, tombstone: str
    ) -> bool:
        conn = await asyncpg.connect(self._dsn)
        try:
            status = await conn.execute(_DESTROY_SQL, tenant_id, subject_key, evidence, tombstone)
        finally:
            await conn.close()
        return _rowcount(status) > 0


_SELECT_SQL = """
SELECT dek_ref, destroyed_at FROM subject_keys
WHERE tenant_id = $1 AND subject_key = $2
"""

_INSERT_SQL = """
INSERT INTO subject_keys (tenant_id, subject_key, dek_ref, kek_ref)
VALUES ($1, $2, $3, $4)
ON CONFLICT (tenant_id, subject_key) DO NOTHING
"""

_DESTROY_SQL = """
UPDATE subject_keys
SET destroyed_at = now(), destruction_evidence = $3, dek_ref = $4
WHERE tenant_id = $1 AND subject_key = $2 AND destroyed_at IS NULL
"""


def _encode_ref(key_ref: str, wrapped: str) -> str:
    return json.dumps({"key_ref": key_ref, "wrapped": wrapped}, sort_keys=True)


def _decode_ref(dek_ref: str) -> tuple[str, str]:
    payload = json.loads(dek_ref)
    return str(payload["key_ref"]), str(payload["wrapped"])


def _rowcount(status: str) -> int:
    parts = status.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


__all__ = [
    "FakeKmsClient",
    "KMS_KEK_REF",
    "KmsClient",
    "KmsError",
    "KmsKeyDestroyedError",
    "KmsKeyManager",
    "PostgresSubjectKeyStore",
    "SubjectKeyRow",
    "SubjectKeyStore",
]
