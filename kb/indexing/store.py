"""文档/切分/ACL 的落库入口。

`apply` 在【同一个事务】里写文档、chunks 与 ACL —— O-3 要求禁止
"先入内容后补权限"的时间窗。

chunk 版本规则：同一 chunk_id 内容未变则 version 不变；内容变了则 +1。
这样 citation 的 chunk_version 快照仍然对得上。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env
from kb.indexing.ids import acl_id
from kb.indexing.models import (
    ChunkVectorEntry,
    PreparedChunk,
    PreparedDocument,
    StoredDocument,
)

_GET_DOC_SQL = """
SELECT doc_id, content_hash, version
FROM documents
WHERE tenant_id = $1 AND source_id = $2 AND external_id = $3
"""

_UPSERT_DOC_SQL = """
INSERT INTO documents
    (doc_id, tenant_id, source_id, external_id, title, url, author, doc_type,
     department, version, content_hash, parser_version, normalization_version,
     audience, source_updated_at, status)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'indexed')
ON CONFLICT (doc_id) DO UPDATE SET
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    author = EXCLUDED.author,
    doc_type = EXCLUDED.doc_type,
    department = EXCLUDED.department,
    version = EXCLUDED.version,
    content_hash = EXCLUDED.content_hash,
    parser_version = EXCLUDED.parser_version,
    normalization_version = EXCLUDED.normalization_version,
    audience = EXCLUDED.audience,
    source_updated_at = EXCLUDED.source_updated_at,
    status = 'indexed',
    ingested_at = now()
"""

_EXISTING_CHUNKS_SQL = """
SELECT chunk_id, content_hash, version FROM chunks WHERE doc_id = $1
"""

_DELETE_MISSING_VECTORS_SQL = """
DELETE FROM chunk_vectors cv
USING chunks c
WHERE cv.chunk_id = c.chunk_id
  AND c.doc_id = $1
  AND NOT (c.chunk_id = ANY($2::text[]))
"""

_DELETE_MISSING_CHUNKS_SQL = """
DELETE FROM chunks WHERE doc_id = $1 AND NOT (chunk_id = ANY($2::text[]))
"""

_UPSERT_CHUNK_VECTOR_SQL = """
INSERT INTO chunk_vectors
    (chunk_id, tenant_id, model_name, model_version, vector_store, collection, indexed_at)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (chunk_id) DO UPDATE SET
    model_name = EXCLUDED.model_name,
    model_version = EXCLUDED.model_version,
    vector_store = EXCLUDED.vector_store,
    collection = EXCLUDED.collection,
    indexed_at = now()
"""

_UPSERT_CHUNK_SQL = """
INSERT INTO chunks
    (chunk_id, tenant_id, doc_id, seq, content, content_hash, token_count,
     page_no, section_path, clause_no, char_start, char_end, chunker_version, version)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
ON CONFLICT (chunk_id) DO UPDATE SET
    seq = EXCLUDED.seq,
    content = EXCLUDED.content,
    content_hash = EXCLUDED.content_hash,
    token_count = EXCLUDED.token_count,
    page_no = EXCLUDED.page_no,
    section_path = EXCLUDED.section_path,
    clause_no = EXCLUDED.clause_no,
    char_start = EXCLUDED.char_start,
    char_end = EXCLUDED.char_end,
    chunker_version = EXCLUDED.chunker_version,
    version = EXCLUDED.version
"""

_DELETE_ACL_SQL = """
DELETE FROM acl_entries
WHERE tenant_id = $1 AND object_type = 'document' AND object_id = $2
"""

_INSERT_ACL_SQL = """
INSERT INTO acl_entries
    (acl_id, tenant_id, object_type, object_id, principal_type, principal_id,
     effect, permission, scope_level, inherited_from, inheritance_broken, source_id)
VALUES ($1, $2, 'document', $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""


class DocumentStore(Protocol):
    async def get_document(
        self, tenant_id: str, source_id: str, external_id: str
    ) -> StoredDocument | None: ...

    async def apply(self, prepared: PreparedDocument) -> None: ...

    async def record_chunk_vectors(
        self, tenant_id: str, entries: Sequence[ChunkVectorEntry]
    ) -> None: ...


class PostgresDocumentStore:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def get_document(
        self, tenant_id: str, source_id: str, external_id: str
    ) -> StoredDocument | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_GET_DOC_SQL, tenant_id, source_id, external_id)
        finally:
            await conn.close()
        if row is None:
            return None
        return StoredDocument(
            doc_id=str(row["doc_id"]),
            content_hash=str(row["content_hash"]),
            version=str(row["version"]),
        )

    async def apply(self, prepared: PreparedDocument) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await self._write_document(conn, prepared)
                await self._write_chunks(conn, prepared)
                await self._write_acl(conn, prepared)
        finally:
            await conn.close()

    async def _write_document(self, conn: asyncpg.Connection, prepared: PreparedDocument) -> None:
        await conn.execute(
            _UPSERT_DOC_SQL,
            prepared.doc_id,
            prepared.tenant_id,
            prepared.source_id,
            prepared.external_id,
            prepared.title,
            prepared.url,
            prepared.author,
            prepared.doc_type,
            prepared.department,
            prepared.doc_version,
            prepared.content_hash,
            prepared.parser_version,
            prepared.normalization_version,
            prepared.audience,
            prepared.source_updated_at,
        )

    async def _write_chunks(self, conn: asyncpg.Connection, prepared: PreparedDocument) -> None:
        rows = await conn.fetch(_EXISTING_CHUNKS_SQL, prepared.doc_id)
        existing = {
            str(row["chunk_id"]): (str(row["content_hash"]), int(row["version"])) for row in rows
        }
        new_ids = [chunk.chunk_id for chunk in prepared.chunks]
        await conn.execute(_DELETE_MISSING_VECTORS_SQL, prepared.doc_id, new_ids)
        await conn.execute(_DELETE_MISSING_CHUNKS_SQL, prepared.doc_id, new_ids)

        for chunk in prepared.chunks:
            await conn.execute(
                _UPSERT_CHUNK_SQL,
                chunk.chunk_id,
                prepared.tenant_id,
                prepared.doc_id,
                chunk.seq,
                chunk.content,
                chunk.content_hash,
                chunk.token_count,
                chunk.page_no,
                chunk.section_path,
                chunk.clause_no,
                chunk.char_start,
                chunk.char_end,
                chunk.chunker_version,
                _chunk_version(chunk, existing),
            )

    async def _write_acl(self, conn: asyncpg.Connection, prepared: PreparedDocument) -> None:
        await conn.execute(_DELETE_ACL_SQL, prepared.tenant_id, prepared.doc_id)
        for entry in prepared.acl_entries:
            await conn.execute(
                _INSERT_ACL_SQL,
                acl_id(prepared.tenant_id, prepared.doc_id, entry),
                prepared.tenant_id,
                prepared.doc_id,
                entry.principal_type,
                entry.principal_id,
                entry.effect,
                entry.permission,
                entry.scope_level,
                None,
                prepared.inheritance_broken,
                prepared.source_id,
            )

    async def record_chunk_vectors(
        self, tenant_id: str, entries: Sequence[ChunkVectorEntry]
    ) -> None:
        if not entries:
            return
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                for entry in entries:
                    await conn.execute(
                        _UPSERT_CHUNK_VECTOR_SQL,
                        entry.chunk_id,
                        tenant_id,
                        entry.model_name,
                        entry.model_version,
                        entry.vector_store,
                        entry.collection,
                    )
        finally:
            await conn.close()


def _chunk_version(chunk: PreparedChunk, existing: dict[str, tuple[str, int]]) -> int:
    previous = existing.get(chunk.chunk_id)
    if previous is None:
        return 1
    previous_hash, previous_version = previous
    if previous_hash == chunk.content_hash:
        return previous_version
    return previous_version + 1


__all__ = ["DocumentStore", "PostgresDocumentStore"]
