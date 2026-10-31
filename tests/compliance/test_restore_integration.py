"""恢复演练闸门与 PostgreSQL 恢复目录的集成验证。"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from kb.compliance.restore import PostgresRestoreCatalog, RestoreGuard
from kb.database import Database
from kb.indexing.suppression import PostgresSuppressionChecker, document_scope_key

pytestmark = pytest.mark.integration

TENANT = "t_restore_it"
SOURCE = "src_restore"
DOC = "doc_restore"
CHUNK = "chk_restore"
PHONE = "13800138000"


def _hasher(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM suppression_list WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM chunks WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM documents WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM sources WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_restore_guard_detects_suppressed_document_and_subject():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'restore')", TENANT)
    db.execute(
        "INSERT INTO sources (source_id, tenant_id, source_type, display_name) "
        "VALUES ($1,$2,'upload','restore')",
        SOURCE,
        TENANT,
    )
    db.execute(
        "INSERT INTO documents "
        "(doc_id, tenant_id, source_id, external_id, title, content_hash, parser_version, status) "
        "VALUES ($1,$2,$3,'ext_restore','恢复文档','h','text-v1','indexed')",
        DOC,
        TENANT,
        SOURCE,
    )
    db.execute(
        "INSERT INTO chunks "
        "(chunk_id, tenant_id, doc_id, seq, content, content_hash, token_count, "
        " char_start, char_end, chunker_version) "
        "VALUES ($1,$2,$3,0,$4,'h',1,0,11,'chunk-v1')",
        CHUNK,
        TENANT,
        DOC,
        f"联系电话{PHONE}",
    )
    db.execute(
        "INSERT INTO suppression_list "
        "(suppression_id, tenant_id, scope_type, scope_key, reason) "
        "VALUES ('sup_doc',$1,'document',$2,'erasure_request'),"
        "       ('sup_subj',$1,'subject',$3,'erasure_request')",
        TENANT,
        _hasher(document_scope_key(SOURCE, "ext_restore")),
        _hasher(PHONE),
    )
    try:
        guard = RestoreGuard(
            PostgresRestoreCatalog(db),
            PostgresSuppressionChecker(_hasher, dsn=db.dsn),
            tenant_id=TENANT,
        )

        report = asyncio.run(guard.scan())

        assert report.passed is False
        assert any(finding.kind == "document" for finding in report.findings)
        assert any(finding.kind == "subject" for finding in report.findings)
    finally:
        _cleanup()
