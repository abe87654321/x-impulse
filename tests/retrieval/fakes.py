"""检索测试替身 —— 普通模块，测试文件显式 import。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from kb.identity.permissions import (
    FULL_SCOPE,
    AclRow,
    DelegationContext,
    Principal,
    ScopeLevel,
)
from kb.retrieval.models import (
    ChunkRecord,
    HybridRecall,
    RecallFilter,
    ScoredChunk,
)
from kb.retrieval.retriever import AclRepository

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def make_ctx(user: str = "alice") -> DelegationContext:
    return DelegationContext(
        principal=Principal(type="user", id=user),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=NOW + timedelta(hours=1),
        evidence={},
    )


def make_record(
    chunk_id: str,
    *,
    doc_id: str | None = None,
    tenant_id: str = "t1",
    audience: str = "internal_only",
    department: str | None = "eng",
    doc_type: str | None = "policy",
    effective_until: datetime | None = None,
    status: str = "indexed",
    source_updated_at: datetime | None = NOW,
) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        doc_id=doc_id or f"doc_{chunk_id}",
        tenant_id=tenant_id,
        content=f"content of {chunk_id}",
        chunk_version=1,
        chunk_content_hash=f"hash_{chunk_id}",
        audience=audience,
        department=department,
        doc_type=doc_type,
        effective_until=effective_until,
        status=status,
        source_updated_at=source_updated_at,
        title=f"title {chunk_id}",
        seq=0,
        char_start=0,
        char_end=10,
    )


class FakeBackend:
    def __init__(self, records: Sequence[ChunkRecord]) -> None:
        self.records = list(records)
        self.last_filter: RecallFilter | None = None
        self.calls = 0

    async def search(self, query: str, *, limit: int, recall_filter: RecallFilter) -> HybridRecall:
        self.calls += 1
        self.last_filter = recall_filter
        allowed = [r for r in self.records if recall_filter.allows(r)][:limit]
        leg = tuple(ScoredChunk(record=r, score=1.0) for r in allowed)
        return HybridRecall(bm25=leg, vector=leg)


class FakeRepository:
    def __init__(self, records: Sequence[ChunkRecord]) -> None:
        self.records = {r.chunk_id: r for r in records}

    async def get_chunks(self, chunk_ids: Sequence[str]) -> Sequence[ChunkRecord]:
        return [self.records[cid] for cid in chunk_ids if cid in self.records]


class MapPermissions:
    """按 principal 提供预过滤集；二次校验默认全放行（便于单独测试各道闸）。"""

    def __init__(
        self,
        allowed_by_user: Mapping[str, frozenset[str]],
        *,
        denied_docs: frozenset[str] = frozenset(),
        memberships: frozenset[str] = frozenset(),
    ) -> None:
        self._allowed = allowed_by_user
        self._denied = denied_docs
        self._memberships = memberships

    async def memberships(self, ctx: DelegationContext) -> frozenset[str]:
        return self._memberships

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]:
        user = ctx.principal.id if ctx.principal else ""
        return self._allowed.get(user, frozenset())

    async def is_allowed(
        self, ctx: DelegationContext, doc_id: str, memberships: frozenset[str]
    ) -> bool:
        return doc_id not in self._denied


class FakeAclRepository(AclRepository):
    def __init__(
        self,
        hierarchy: Mapping[str, list[tuple[str, ScopeLevel, str | None]]],
        acls: Mapping[str, Sequence[AclRow]],
        broken: frozenset[str] = frozenset(),
        allowed_docs: frozenset[str] = frozenset(),
    ) -> None:
        self._hierarchy = hierarchy
        self._acls = acls
        self._broken = broken
        self._allowed = allowed_docs

    async def memberships(self, principal_id: str) -> frozenset[str]:
        return frozenset({"grp_eng"}) if principal_id == "alice" else frozenset()

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]:
        return self._allowed

    async def hierarchy(self, doc_id: str) -> list[tuple[str, ScopeLevel, str | None]]:
        return self._hierarchy[doc_id]

    async def acls(self, object_ids: Sequence[str]) -> Mapping[str, Sequence[AclRow]]:
        return {oid: self._acls[oid] for oid in object_ids if oid in self._acls}

    async def broken_ids(self, object_ids: Sequence[str]) -> frozenset[str]:
        return frozenset(oid for oid in object_ids if oid in self._broken)


def row(
    object_id: str,
    *,
    object_type: ScopeLevel,
    principal_id: str,
    principal_type: str = "group",
    effect: str = "allow",
    scope_level: ScopeLevel | None = None,
) -> AclRow:
    return AclRow(
        object_type=object_type,
        object_id=object_id,
        principal_type=principal_type,  # type: ignore[arg-type]
        principal_id=principal_id,
        effect=effect,  # type: ignore[arg-type]
        permission="read",
        scope_level=scope_level or object_type,
        inherited_from=None,
    )
