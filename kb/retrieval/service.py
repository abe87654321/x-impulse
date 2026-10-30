"""检索服务 —— 预过滤 + 实时二次校验的持久层接缝。

测试用的同步接口；生产应改为异步并复用连接池。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kb.database import Database
from kb.identity.permissions import (
    DelegationContext,
    ScopeLevel,
    build_chain,
    evaluate,
)
from kb.identity.repository import DEFAULT_TENANT, AclRepository
from kb.retrieval.models import ChunkRecord

_CANDIDATE_SQL = """
SELECT c.chunk_id, c.doc_id, c.tenant_id, c.seq, c.content, c.content_hash, c.version,
       c.char_start, c.char_end, c.page_no, c.section_path, c.clause_no,
       d.source_id, d.title, d.audience, d.status, d.doc_type, d.department,
       d.effective_until, d.source_updated_at, d.version AS doc_version
FROM chunks c
JOIN documents d ON d.doc_id = c.doc_id
WHERE c.tenant_id = $1 AND d.tenant_id = $1 AND d.status = 'indexed'
  AND c.content ILIKE '%' || $2 || '%'
ORDER BY c.chunk_id
LIMIT $3
"""


@dataclass(frozen=True)
class SearchHit:
    tenant_id: str
    chunk_id: str
    doc_id: str
    seq: int
    source_id: str
    content: str
    content_hash: str
    chunk_version: int
    doc_version: str
    title: str
    audience: str
    status: str
    doc_type: str | None
    department: str | None
    effective_until: datetime | None
    source_updated_at: datetime | None
    page_no: int | None
    section_path: str | None
    clause_no: str | None
    char_start: int
    char_end: int

    def to_chunk_record(self) -> ChunkRecord:
        return ChunkRecord(
            chunk_id=self.chunk_id,
            doc_id=self.doc_id,
            tenant_id=self.tenant_id,
            content=self.content,
            chunk_version=self.chunk_version,
            chunk_content_hash=self.content_hash,
            audience=self.audience,
            department=self.department,
            doc_type=self.doc_type,
            effective_until=self.effective_until,
            status=self.status,
            source_updated_at=self.source_updated_at,
            title=self.title,
            seq=self.seq,
            char_start=self.char_start,
            char_end=self.char_end,
            page_no=self.page_no,
            section_path=self.section_path,
            clause_no=self.clause_no,
            doc_version=self.doc_version,
        )


class RetrievalService:
    def __init__(
        self,
        db: Database | None = None,
        acl_repo: AclRepository | None = None,
        tenant_id: str = DEFAULT_TENANT,
    ) -> None:
        self._db = db or Database()
        self._acl = acl_repo or AclRepository(self._db)
        self._tenant_id = tenant_id

    def prefilter(self, ctx: DelegationContext, query: str, *, limit: int = 50) -> list[SearchHit]:
        memberships = self._memberships(ctx)
        hits = self._candidates(query, limit)
        return [hit for hit in hits if self._allowed(ctx, hit, memberships)]

    def recheck(self, ctx: DelegationContext, hits: list[SearchHit]) -> list[SearchHit]:
        memberships = self._memberships(ctx)
        return [hit for hit in hits if self._allowed(ctx, hit, memberships)]

    def search(self, ctx: DelegationContext, query: str, *, limit: int = 50) -> list[SearchHit]:
        return self.recheck(ctx, self.prefilter(ctx, query, limit=limit))

    def search_chunks(
        self, ctx: DelegationContext, query: str, *, limit: int = 50
    ) -> list[ChunkRecord]:
        return [hit.to_chunk_record() for hit in self.search(ctx, query, limit=limit)]

    # ── 内部 ────────────────────────────────────────────

    def _candidates(self, query: str, limit: int) -> list[SearchHit]:
        rows = self._db.fetch(_CANDIDATE_SQL, self._tenant_id, query, limit)
        return [_to_hit(row) for row in rows]

    def _memberships(self, ctx: DelegationContext) -> frozenset[str]:
        if ctx.principal is None:
            return frozenset()
        return self._acl.load_memberships(self._tenant_id, ctx.principal.id)

    def _allowed(
        self,
        ctx: DelegationContext,
        hit: SearchHit,
        memberships: frozenset[str],
    ) -> bool:
        object_ids = [hit.doc_id, hit.source_id]
        hierarchy: list[tuple[str, ScopeLevel, str | None]] = [
            (hit.doc_id, "document", hit.source_id),
            (hit.source_id, "source", None),
        ]
        broken = self._acl.load_broken_ids(self._tenant_id, object_ids)
        acls = self._acl.load_acl_rows(self._tenant_id, object_ids)
        return evaluate(ctx, build_chain(hierarchy, broken), acls, memberships)


def _to_hit(row: dict[str, object]) -> SearchHit:
    return SearchHit(
        tenant_id=str(row["tenant_id"]),
        chunk_id=str(row["chunk_id"]),
        doc_id=str(row["doc_id"]),
        seq=int(str(row["seq"])),
        source_id=str(row["source_id"]),
        content=str(row["content"]),
        content_hash=str(row["content_hash"]),
        chunk_version=int(str(row["version"])),
        doc_version=str(row["doc_version"]),
        title=str(row["title"]),
        audience=str(row["audience"]),
        status=str(row["status"]),
        doc_type=_optional_str(row["doc_type"]),
        department=_optional_str(row["department"]),
        effective_until=_optional_dt(row["effective_until"]),
        source_updated_at=_optional_dt(row["source_updated_at"]),
        page_no=_optional_int(row["page_no"]),
        section_path=_optional_str(row["section_path"]),
        clause_no=_optional_str(row["clause_no"]),
        char_start=int(str(row["char_start"])),
        char_end=int(str(row["char_end"])),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _optional_dt(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


__all__ = ["RetrievalService", "SearchHit"]
