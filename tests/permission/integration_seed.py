"""集成测试种子数据 —— 幂等。

只在 `-m integration` 时由 conftest 调用。固定 ID，前后删除再插入，
因此 revoke / offboard 造成的删除不会影响下一条用例。
"""

from __future__ import annotations

from kb.database import Database
from kb.parsing.normalize import hash_text

TENANT = "default"
SOURCE = "src_seed"
DOC = "doc-1"
CHUNK = "chk_seed_1"
ACL = "acl_seed_allow_alice"
CONTENT = "报销标准：员工报销需在三十日内提交，并附发票原件。"


def seed() -> None:
    db = Database()
    _upsert_source(db)
    _upsert_document(db)
    _replace_chunk(db)
    _replace_acl(db)
    db.execute(
        "DELETE FROM principal_memberships WHERE tenant_id = $1 AND member_id = $2",
        TENANT,
        "alice",
    )


def _upsert_source(db: Database) -> None:
    db.execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '集成测试源')
        ON CONFLICT (source_id) DO UPDATE SET display_name = EXCLUDED.display_name
        """,
        SOURCE,
        TENANT,
    )


def _upsert_document(db: Database) -> None:
    db.execute(
        """
        INSERT INTO documents
            (doc_id, tenant_id, source_id, external_id, title,
             content_hash, parser_version, status, source_updated_at)
        VALUES ($1, $2, $3, $1, '报销制度', $4, 'seed-v1', 'indexed', now())
        ON CONFLICT (doc_id) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            title = EXCLUDED.title,
            status = 'indexed',
            parser_version = EXCLUDED.parser_version,
            source_updated_at = EXCLUDED.source_updated_at
        """,
        DOC,
        TENANT,
        SOURCE,
        hash_text(CONTENT),
    )


def _replace_chunk(db: Database) -> None:
    db.execute("DELETE FROM chunks WHERE chunk_id = $1", CHUNK)
    db.execute(
        """
        INSERT INTO chunks
            (chunk_id, tenant_id, doc_id, seq, content, content_hash, token_count,
             page_no, section_path, clause_no, char_start, char_end,
             chunker_version, version)
        VALUES ($1, $2, $3, 0, $4, $5, 20, 1, '报销制度', '第二条', 0, $6, 'chunk-v1', 1)
        """,
        CHUNK,
        TENANT,
        DOC,
        CONTENT,
        hash_text(CONTENT),
        len(CONTENT),
    )


def _replace_acl(db: Database) -> None:
    db.execute("DELETE FROM acl_entries WHERE acl_id = $1", ACL)
    db.execute(
        """
        INSERT INTO acl_entries
            (acl_id, tenant_id, object_type, object_id, principal_type, principal_id,
             effect, permission, scope_level, inheritance_broken, source_id)
        VALUES ($1, $2, 'document', $3, 'user', 'alice',
                'allow', 'read', 'document', false, $4)
        """,
        ACL,
        TENANT,
        DOC,
        SOURCE,
    )
