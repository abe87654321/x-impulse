"""审计哈希链：只追加、prev_hash 串接。"""

from __future__ import annotations

import asyncio
import json

import pytest

from kb.audit.protection import open_payload
from kb.audit.writer import AuditEvent, PostgresAuditStore
from kb.compliance.crypto import SoftwareKeyManager, kek_from_passphrase
from kb.compliance.pii import subject_key
from kb.database import Database

pytestmark = pytest.mark.integration

TENANT = "audit_it_tenant"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM audit_log WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM subject_keys WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_audit_chain_links_rows():
    _cleanup()
    try:
        store = PostgresAuditStore()
        first = asyncio.run(
            store.append(
                AuditEvent(
                    category="query",
                    action="ask",
                    tenant_id=TENANT,
                    principal_id="alice",
                    payload={"query_id": "q1"},
                )
            )
        )
        second = asyncio.run(
            store.append(
                AuditEvent(
                    category="answer",
                    action="answered",
                    tenant_id=TENANT,
                    principal_id="alice",
                    payload={"query_id": "q1"},
                )
            )
        )

        rows = Database().fetch(
            "SELECT log_id, prev_hash, row_hash, payload FROM audit_log "
            "WHERE tenant_id = $1 ORDER BY log_id",
            TENANT,
        )
        assert [str(row["log_id"]) for row in rows] == [first, second]
        assert rows[0]["prev_hash"] is None
        assert rows[1]["prev_hash"] == rows[0]["row_hash"]
        assert rows[0]["row_hash"] != rows[1]["row_hash"]
        assert json.loads(str(rows[0]["payload"])) == {"query_id": "q1"}
    finally:
        _cleanup()


def test_audit_encrypts_pii_and_supports_crypto_shredding():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'audit')", TENANT)
    try:
        pepper = "audit-pepper"
        manager = SoftwareKeyManager(kek=kek_from_passphrase("audit-kek"))
        store = PostgresAuditStore(
            key_manager=manager,
            subject_hasher=lambda value: subject_key(value, pepper),
        )

        asyncio.run(
            store.append(
                AuditEvent(
                    category="query",
                    action="ask",
                    tenant_id=TENANT,
                    payload={"query_id": "q1", "note": "联系 alice@example.com"},
                )
            )
        )

        row = db.fetch("SELECT payload FROM audit_log WHERE tenant_id = $1", TENANT)[0]
        payload = json.loads(str(row["payload"]))
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "alice@example.com" not in serialized
        assert "[enc:v1:" in serialized
        assert payload["query_id"] == "q1"

        key = subject_key("alice@example.com", pepper)
        keys = db.fetch(
            "SELECT dek_ref, destroyed_at FROM subject_keys "
            "WHERE tenant_id = $1 AND subject_key = $2",
            TENANT,
            key,
        )
        assert len(keys) == 1
        assert keys[0]["destroyed_at"] is None

        assert asyncio.run(manager.destroy(TENANT, key, "PIPL-47-test"))

        opened = asyncio.run(open_payload(payload, tenant_id=TENANT, key_manager=manager))
        assert opened["note"] == "联系 [已加密擦除]"
        assert opened["query_id"] == "q1"
    finally:
        _cleanup()
