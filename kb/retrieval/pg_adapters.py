"""异步检索核心的 PostgreSQL / 向量库适配器。

组合方式：
    HybridRetrievalService
      └─ Retriever（融合、缓存、二次校验）
           ├─ HybridRecallBackend（BM25 近似 + 向量腿）
           ├─ PgChunkRepository
           └─ AclPermissionChecker（TenantAclRepository）

权限过滤在召回阶段完成：词法腿把 allowed_doc_ids / audience 下推到 SQL，
向量腿把同样的条件下推给向量库（I-3）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime

from kb.database import Database
from kb.identity.permissions import AclRow, DelegationContext, ScopeLevel, build_chain, evaluate
from kb.indexing.embedding import Embedder
from kb.indexing.vector_store import VectorStore
from kb.retrieval.bm25 import rank as bm25_rank
from kb.retrieval.cache import InMemoryQueryCache
from kb.retrieval.models import (
    ChunkRecord,
    HybridRecall,
    MetadataFilter,
    RecallFilter,
    RetrievalConfig,
    RetrievedChunk,
    ScoredChunk,
)
from kb.retrieval.retriever import (
    AclPermissionChecker,
    AclRepository,
    QueryCache,
    RecallBackend,
    Retriever,
)

_BY_IDS_SQL = """
SELECT
    c.chunk_id, c.doc_id, c.tenant_id, c.seq, c.content, c.content_hash, c.version,
    c.char_start, c.char_end, c.page_no, c.section_path, c.clause_no,
    d.title, d.audience, d.status, d.doc_type, d.department,
    d.effective_until, d.source_updated_at, d.version AS doc_version
FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
WHERE c.tenant_id = $1 AND c.chunk_id = ANY($2::text[])
"""

_LEXICAL_SQL = """
SELECT
    c.chunk_id, c.doc_id, c.tenant_id, c.seq, c.content, c.content_hash, c.version,
    c.char_start, c.char_end, c.page_no, c.section_path, c.clause_no,
    d.title, d.audience, d.status, d.doc_type, d.department,
    d.effective_until, d.source_updated_at, d.version AS doc_version
FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
WHERE c.tenant_id = $1
  AND d.tenant_id = $1
  AND d.status = 'indexed'
  AND d.doc_id = ANY($2::text[])
  AND d.audience = ANY($3::text[])
  AND (cardinality($4::text[]) = 0 OR d.department = ANY($4::text[]))
  AND (cardinality($5::text[]) = 0 OR d.doc_type = ANY($5::text[]))
  AND ($6::timestamptz IS NULL OR d.source_updated_at >= $6)
  AND ($7::timestamptz IS NULL OR d.effective_until IS NULL OR d.effective_until > $7)
ORDER BY c.chunk_id
LIMIT $8
"""

# 开发期语料上限：BM25 在应用层对候选集打分，全量扫描不适合百万级。
_BM25_CORPUS_LIMIT = 5000

_AUDIENCE_SQL = """
SELECT dp.audience_filter
FROM tenants t JOIN deployment_profiles dp ON dp.profile_id = t.profile_id
WHERE t.tenant_id = $1
"""

_DOC_IDS_SQL = """
SELECT doc_id FROM documents WHERE tenant_id = $1 AND status = 'indexed'
"""

_DOC_SOURCE_SQL = """
SELECT source_id FROM documents WHERE tenant_id = $1 AND doc_id = $2
"""

_MEMBERSHIPS_SQL = """
SELECT member_id, group_id FROM principal_memberships WHERE tenant_id = $1
"""

_ACL_SQL = """
SELECT object_type, object_id, principal_type, principal_id, effect, permission,
       scope_level, inherited_from
FROM acl_entries
WHERE tenant_id = $1 AND object_id = ANY($2::text[])
"""

_BROKEN_SQL = """
SELECT DISTINCT object_id FROM acl_entries
WHERE tenant_id = $1 AND inheritance_broken AND object_id = ANY($2::text[])
"""


def load_audience_allowlist(db: Database, tenant_id: str) -> frozenset[str]:
    """受众规则来自部署档案（FR-04-16），不得硬编码。"""
    rows = db.fetch(_AUDIENCE_SQL, tenant_id)
    if not rows:
        raise ValueError(f"租户 {tenant_id} 未配置部署档案")
    values = rows[0]["audience_filter"]
    if not isinstance(values, list):
        raise ValueError("audience_filter 不是数组")
    return frozenset(str(value) for value in values)


class PgChunkRepository:
    def __init__(self, db: Database, tenant_id: str) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def get_chunks(self, chunk_ids: Sequence[str]) -> Sequence[ChunkRecord]:
        if not chunk_ids:
            return []
        rows = await asyncio.to_thread(
            self._db.fetch, _BY_IDS_SQL, self._tenant_id, list(chunk_ids)
        )
        return [_to_record(row) for row in rows]


class TenantAclRepository:
    """按租户绑定的 ACL 加载器，供 AclPermissionChecker 使用。"""

    def __init__(self, db: Database, tenant_id: str) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def memberships(self, principal_id: str) -> frozenset[str]:
        from kb.identity.permissions import expand_memberships

        rows = await asyncio.to_thread(self._db.fetch, _MEMBERSHIPS_SQL, self._tenant_id)
        edges: dict[str, set[str]] = {}
        for row in rows:
            edges.setdefault(str(row["member_id"]), set()).add(str(row["group_id"]))
        return expand_memberships(principal_id, edges)

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]:
        rows = await asyncio.to_thread(self._db.fetch, _DOC_IDS_SQL, self._tenant_id)
        allowed: set[str] = set()
        for row in rows:
            doc_id = str(row["doc_id"])
            if await self._doc_allowed(ctx, doc_id, memberships):
                allowed.add(doc_id)
        return frozenset(allowed)

    async def hierarchy(self, doc_id: str) -> list[tuple[str, ScopeLevel, str | None]]:
        rows = await asyncio.to_thread(self._db.fetch, _DOC_SOURCE_SQL, self._tenant_id, doc_id)
        if not rows:
            return []
        source_id = str(rows[0]["source_id"])
        return [(doc_id, "document", source_id), (source_id, "source", None)]

    async def acls(self, object_ids: Sequence[str]) -> Mapping[str, Sequence[AclRow]]:
        rows = await asyncio.to_thread(self._db.fetch, _ACL_SQL, self._tenant_id, list(object_ids))
        grouped: dict[str, list[AclRow]] = {}
        for row in rows:
            object_id = str(row["object_id"])
            grouped.setdefault(object_id, []).append(_to_acl_row(row))
        return grouped

    async def broken_ids(self, object_ids: Sequence[str]) -> frozenset[str]:
        rows = await asyncio.to_thread(
            self._db.fetch, _BROKEN_SQL, self._tenant_id, list(object_ids)
        )
        return frozenset(str(row["object_id"]) for row in rows)

    async def _doc_allowed(
        self, ctx: DelegationContext, doc_id: str, memberships: frozenset[str]
    ) -> bool:
        hierarchy = await self.hierarchy(doc_id)
        object_ids = [object_id for object_id, _, _ in hierarchy]
        broken = await self.broken_ids(object_ids)
        acls = await self.acls(object_ids)
        return evaluate(ctx, build_chain(hierarchy, broken), acls, memberships)


class HybridRecallBackend:
    def __init__(
        self,
        db: Database,
        chunks: PgChunkRepository,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        tenant_id: str,
        collection: str = "kb_chunks",
    ) -> None:
        self._db = db
        self._chunks = chunks
        self._vectors = vectors
        self._embedder = embedder
        self._tenant_id = tenant_id
        self._collection = collection

    async def search(self, query: str, *, limit: int, recall_filter: RecallFilter) -> HybridRecall:
        lexical = await self._lexical(query, limit, recall_filter)
        vector = await self._vector(query, limit, recall_filter)
        return HybridRecall(bm25=tuple(lexical), vector=tuple(vector))

    async def _lexical(
        self, query: str, limit: int, recall_filter: RecallFilter
    ) -> list[ScoredChunk]:
        if not recall_filter.allowed_doc_ids:
            return []
        metadata = recall_filter.metadata
        rows = await asyncio.to_thread(
            self._db.fetch,
            _LEXICAL_SQL,
            self._tenant_id,
            sorted(recall_filter.allowed_doc_ids),
            sorted(recall_filter.audience_allowlist),
            sorted(metadata.departments),
            sorted(metadata.doc_types),
            metadata.updated_after,
            metadata.exclude_expired_at,
            _BM25_CORPUS_LIMIT,
        )
        records = [_to_record(row) for row in rows]
        by_id = {record.chunk_id: record for record in records}
        ranked = bm25_rank(
            query, [(record.chunk_id, record.content) for record in records], limit=limit
        )
        return [ScoredChunk(record=by_id[chunk_id], score=score) for chunk_id, score in ranked]

    async def _vector(
        self, query: str, limit: int, recall_filter: RecallFilter
    ) -> list[ScoredChunk]:
        if not recall_filter.allowed_doc_ids:
            return []
        vectors = await self._embedder.embed([query])
        if not vectors:
            return []
        hits = await self._vectors.search(
            self._collection,
            vectors[0],
            tenant_id=self._tenant_id,
            allowed_doc_ids=recall_filter.allowed_doc_ids,
            audience_allowlist=recall_filter.audience_allowlist,
            limit=limit,
        )
        records = await self._chunks.get_chunks([hit.chunk_id for hit in hits])
        by_id = {record.chunk_id: record for record in records}
        results: list[ScoredChunk] = []
        for hit in hits:
            record = by_id.get(hit.chunk_id)
            if record is None or hit.score <= 0:
                continue
            if not _metadata_allows(recall_filter.metadata, record):
                continue
            results.append(ScoredChunk(record=record, score=hit.score))
        return results


class HybridRetrievalService:
    """生产形态的异步检索入口。"""

    def __init__(
        self,
        db: Database,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        tenant_id: str,
        collection: str = "kb_chunks",
        config: RetrievalConfig | None = None,
        cache: QueryCache | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._config = config or RetrievalConfig(
            audience_allowlist=load_audience_allowlist(db, tenant_id)
        )
        chunks = PgChunkRepository(db, tenant_id)
        acl_repo: AclRepository = TenantAclRepository(db, tenant_id)
        backend: RecallBackend = HybridRecallBackend(
            db, chunks, vectors, embedder, tenant_id=tenant_id, collection=collection
        )
        self._retriever = Retriever(
            backend,
            chunks,
            AclPermissionChecker(acl_repo),
            self._config,
            cache if cache is not None else InMemoryQueryCache(),
        )

    async def retrieve(
        self, ctx: DelegationContext, query: str, *, k: int | None = None
    ) -> list[RetrievedChunk]:
        return await self._retriever.retrieve(ctx, query, tenant_id=self._tenant_id, k=k)


def _metadata_allows(metadata: MetadataFilter, record: ChunkRecord) -> bool:
    return metadata.allows(record)


def _to_record(row: dict[str, object]) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=str(row["chunk_id"]),
        doc_id=str(row["doc_id"]),
        tenant_id=str(row["tenant_id"]),
        content=str(row["content"]),
        chunk_version=int(str(row["version"])),
        chunk_content_hash=str(row["content_hash"]),
        audience=str(row["audience"]),
        department=_opt_str(row["department"]),
        doc_type=_opt_str(row["doc_type"]),
        effective_until=_opt_dt(row["effective_until"]),
        status=str(row["status"]),
        source_updated_at=_opt_dt(row["source_updated_at"]),
        title=str(row["title"]),
        seq=int(str(row["seq"])),
        char_start=int(str(row["char_start"])),
        char_end=int(str(row["char_end"])),
        page_no=_opt_int(row["page_no"]),
        section_path=_opt_str(row["section_path"]),
        clause_no=_opt_str(row["clause_no"]),
        doc_version=str(row["doc_version"]),
    )


def _to_acl_row(row: dict[str, object]) -> AclRow:
    return AclRow(
        object_type=str(row["object_type"]),  # type: ignore[arg-type]
        object_id=str(row["object_id"]),
        principal_type=str(row["principal_type"]),  # type: ignore[arg-type]
        principal_id=str(row["principal_id"]),
        effect=str(row["effect"]),  # type: ignore[arg-type]
        permission=str(row["permission"]),
        scope_level=str(row["scope_level"]),  # type: ignore[arg-type]
        inherited_from=_opt_str(row["inherited_from"]),
    )


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _opt_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


__all__ = [
    "HybridRecallBackend",
    "HybridRetrievalService",
    "PgChunkRepository",
    "TenantAclRepository",
    "load_audience_allowlist",
]
