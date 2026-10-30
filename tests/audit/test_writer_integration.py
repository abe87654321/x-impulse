"""审计哈希链：只追加、prev_hash 串接。"""

from __future__ import annotations

import asyncio
import json

import pytest

from kb.audit.writer import AuditEvent, PostgresAuditStore
from kb.database import Database

pytestmark = pytest.mark.integration

TENANT = "audit_it_tenant"


def _cleanup() -> None:
    Database().execute("DELETE FROM audit_log WHERE tenant_id = $1", TENANT)


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
