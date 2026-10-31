"""审计 PII 字段加密：只加密识别到的片段，密钥销毁后不可读。"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from kb.audit.protection import (
    AuditPayloadError,
    contains_pii,
    open_payload,
    protect_payload,
)
from kb.audit.writer import PostgresAuditStore, build_audit_store_from_env
from kb.compliance.crypto import KeyDestroyedError


class _FakeKeyManager:
    def __init__(self) -> None:
        self.registered: set[str] = set()
        self.destroyed: set[str] = set()

    async def register(self, tenant_id: str, subject_key: str) -> None:
        self.registered.add(subject_key)

    async def encrypt(self, tenant_id: str, subject_key: str, plaintext: str) -> str:
        if subject_key in self.destroyed:
            raise KeyDestroyedError(subject_key)
        return base64.b64encode(f"{subject_key}|{plaintext}".encode()).decode()

    async def decrypt(self, tenant_id: str, subject_key: str, ciphertext: str) -> str:
        if subject_key in self.destroyed:
            raise KeyDestroyedError(subject_key)
        key, plaintext = base64.b64decode(ciphertext).decode().split("|", 1)
        assert key == subject_key
        return plaintext


def _hasher(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def test_encrypts_only_pii_fragments():
    manager = _FakeKeyManager()
    payload = {
        "query_id": "q1",
        "note": "联系 alice@example.com 或 13800138000",
        "count": 2,
    }

    protected = await protect_payload(
        payload, tenant_id="t1", key_manager=manager, subject_hasher=_hasher
    )
    serialized = json.dumps(protected, ensure_ascii=False)

    assert "alice@example.com" not in serialized
    assert "13800138000" not in serialized
    assert "q1" in serialized
    assert serialized.count("[enc:v1:") == 2
    assert len(manager.registered) == 2


async def test_open_restores_plaintext_and_redacts_destroyed_keys():
    manager = _FakeKeyManager()
    payload = {"note": "alice@example.com / 13800138000"}

    protected = await protect_payload(
        payload, tenant_id="t1", key_manager=manager, subject_hasher=_hasher
    )
    opened = await open_payload(protected, tenant_id="t1", key_manager=manager)
    assert "alice@example.com" in opened["note"]
    assert "13800138000" in opened["note"]

    email_key = _hasher("alice@example.com")
    manager.destroyed.add(email_key)
    partially_opened = await open_payload(protected, tenant_id="t1", key_manager=manager)

    assert "[已加密擦除]" in partially_opened["note"]
    assert "13800138000" in partially_opened["note"]


async def test_nested_values_are_protected():
    manager = _FakeKeyManager()
    payload = {"rows": [{"email": "bob@example.com"}], "meta": {"phone": "13900139000"}}

    protected = await protect_payload(
        payload, tenant_id="t1", key_manager=manager, subject_hasher=_hasher
    )

    assert "bob@example.com" not in json.dumps(protected)
    assert "13900139000" not in json.dumps(protected)


async def test_pii_without_encryption_is_rejected():
    with pytest.raises(AuditPayloadError):
        await protect_payload(
            {"note": "alice@example.com"},
            tenant_id="t1",
            key_manager=None,
            subject_hasher=None,
        )


async def test_pii_free_payload_passes_without_encryption():
    payload = {"query_id": "q1", "count": 2}

    assert (
        await protect_payload(payload, tenant_id="t1", key_manager=None, subject_hasher=None)
        == payload
    )
    assert contains_pii(payload) is False
    assert contains_pii({"note": "alice@example.com"}) is True


def test_factory_requires_complete_encryption_config():
    with pytest.raises(AuditPayloadError):
        build_audit_store_from_env(None, environ={"KB_KEK_BASE64": "AAAA"})
    with pytest.raises(AuditPayloadError):
        build_audit_store_from_env(None, environ={"KB_AUDIT_PII_PEPPER": "pepper"})


def test_factory_without_config_returns_plain_store():
    store = build_audit_store_from_env(None, environ={})

    assert isinstance(store, PostgresAuditStore)
