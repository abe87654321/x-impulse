"""主体级密钥与加密擦除 —— 删除设计 §6。

软件实现（AES-256-GCM + 环境注入的 KEK），用于本地开发与测试。
**生产必须替换为取得商用密码产品认证的密码模块 / KMS**（FR-11-04）；
软件 KEK 不满足 §6.2 的「密钥销毁可验证、不可恢复」前提。

密钥销毁后 `dek_ref` 被清空，密文无法再解密 —— 这就是加密擦除。
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Protocol

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kb.database import dsn_from_env

_KEK_REF = "software:kek-v1"
_WRAPPED_CLEARED = "[destroyed]"


class KeyDestroyedError(Exception):
    """密钥已销毁，密文不可解。"""


class KeyManager(Protocol):
    async def register(self, tenant_id: str, subject_key: str) -> None: ...

    async def encrypt(self, tenant_id: str, subject_key: str, plaintext: str) -> str: ...

    async def decrypt(self, tenant_id: str, subject_key: str, ciphertext: str) -> str: ...

    async def destroy(self, tenant_id: str, subject_key: str, evidence: str) -> bool: ...


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


class SoftwareKeyManager:
    def __init__(
        self,
        kek: bytes | None = None,
        *,
        dsn: str | None = None,
    ) -> None:
        raw = kek or _kek_from_env()
        if len(raw) != 32:
            raise ValueError("KEK 必须是 32 字节")
        self._aes = AESGCM(raw)
        self._dsn = dsn or dsn_from_env()

    async def register(self, tenant_id: str, subject_key: str) -> None:
        dek = AESGCM.generate_key(bit_length=256)
        wrapped = self._wrap(dek)
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(_INSERT_SQL, tenant_id, subject_key, wrapped, _KEK_REF)
        finally:
            await conn.close()

    async def encrypt(self, tenant_id: str, subject_key: str, plaintext: str) -> str:
        dek = await self._load_dek(tenant_id, subject_key)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    async def decrypt(self, tenant_id: str, subject_key: str, ciphertext: str) -> str:
        dek = await self._load_dek(tenant_id, subject_key)
        raw = base64.b64decode(ciphertext)
        nonce, payload = raw[:12], raw[12:]
        return AESGCM(dek).decrypt(nonce, payload, None).decode("utf-8")

    async def destroy(self, tenant_id: str, subject_key: str, evidence: str) -> bool:
        conn = await asyncpg.connect(self._dsn)
        try:
            status = await conn.execute(
                _DESTROY_SQL, tenant_id, subject_key, evidence, _WRAPPED_CLEARED
            )
        finally:
            await conn.close()
        parts = status.split()
        return bool(parts and parts[-1].isdigit() and int(parts[-1]) > 0)

    async def _load_dek(self, tenant_id: str, subject_key: str) -> bytes:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_SELECT_SQL, tenant_id, subject_key)
        finally:
            await conn.close()
        if row is None or row["destroyed_at"] is not None or row["dek_ref"] == _WRAPPED_CLEARED:
            raise KeyDestroyedError(f"主体密钥不可用：{subject_key}")
        return self._unwrap(str(row["dek_ref"]))

    def _wrap(self, dek: bytes) -> str:
        nonce = os.urandom(12)
        wrapped = self._aes.encrypt(nonce, dek, None)
        return base64.b64encode(nonce + wrapped).decode("ascii")

    def _unwrap(self, wrapped: str) -> bytes:
        raw = base64.b64decode(wrapped)
        return self._aes.decrypt(raw[:12], raw[12:], None)


def _kek_from_env() -> bytes:
    value = os.environ.get("KB_KEK_BASE64")
    if not value:
        raise ValueError("未配置 KB_KEK_BASE64（生产应由密码模块提供 KEK）")
    return base64.b64decode(value)


def kek_from_passphrase(passphrase: str) -> bytes:
    """测试/开发便捷入口；生产不要用口令派生 KEK。"""
    return hashlib.sha256(passphrase.encode("utf-8")).digest()


__all__ = [
    "KeyDestroyedError",
    "KeyManager",
    "SoftwareKeyManager",
    "kek_from_passphrase",
]
