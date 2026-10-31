"""检索编排 —— I-3 / FR-04-07 / FR-06-03 / FR-04-14a。

安全红线：

1. 权限过滤在【召回阶段】通过 RecallFilter 下推给后端，禁止先召回后遮蔽。
2. 缓存只存召回候选（chunk_id），不存已过滤结果；命中后仍做
   `permissions.is_allowed` 实时二次校验（R-1 不可省略）。
3. 二次校验被拒的候选不计入 k，继续看下一个（R-2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from kb.identity.permissions import (
    AclRow,
    DelegationContext,
    ScopeLevel,
    build_chain,
    evaluate,
)
from kb.retrieval.cache import InMemoryQueryCache, QueryCache, make_cache_key
from kb.retrieval.fusion import fuse
from kb.retrieval.models import (
    ChunkRecord,
    HybridRecall,
    MetadataFilter,
    RecallFilter,
    RetrievalConfig,
    RetrievedChunk,
)
from kb.retrieval.rerank import Reranker


class RecallBackend(Protocol):
    """混合召回后端。**必须**在返回前应用 recall_filter（含两路）。"""

    async def search(
        self, query: str, *, limit: int, recall_filter: RecallFilter
    ) -> HybridRecall: ...


class ChunkRepository(Protocol):
    async def get_chunks(self, chunk_ids: Sequence[str]) -> Sequence[ChunkRecord]: ...


class PermissionChecker(Protocol):
    async def memberships(self, ctx: DelegationContext) -> frozenset[str]: ...

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]: ...

    async def is_allowed(
        self, ctx: DelegationContext, doc_id: str, memberships: frozenset[str]
    ) -> bool: ...


class AclRepository(Protocol):
    """权限数据来源。物化可见集 / 继承链 / ACL 行由仓储层提供。"""

    async def memberships(self, principal_id: str) -> frozenset[str]: ...

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]: ...

    async def hierarchy(self, doc_id: str) -> list[tuple[str, ScopeLevel, str | None]]: ...

    async def acls(self, object_ids: Sequence[str]) -> Mapping[str, Sequence[AclRow]]: ...

    async def broken_ids(self, object_ids: Sequence[str]) -> frozenset[str]: ...


class AclPermissionChecker:
    """把检索接缝接到 TD-03 的求值算法上。

    关键点：`broken_ids` 必须对链上【全部】对象查询，再交给 build_chain 翻译；
    只查文档自身会让目录上的断开继承失效（I-2 / TD-03 B-1）。
    """

    def __init__(self, repository: AclRepository) -> None:
        self._repo = repository

    async def memberships(self, ctx: DelegationContext) -> frozenset[str]:
        if ctx.principal is None:
            return frozenset()
        return await self._repo.memberships(ctx.principal.id)

    async def allowed_doc_ids(
        self, ctx: DelegationContext, memberships: frozenset[str]
    ) -> frozenset[str]:
        return await self._repo.allowed_doc_ids(ctx, memberships)

    async def is_allowed(
        self, ctx: DelegationContext, doc_id: str, memberships: frozenset[str]
    ) -> bool:
        hierarchy = await self._repo.hierarchy(doc_id)
        object_ids = [object_id for object_id, _, _ in hierarchy]
        broken_ids = await self._repo.broken_ids(object_ids)
        chain = build_chain(hierarchy, broken_ids)
        acls = await self._repo.acls(object_ids)
        return evaluate(ctx, chain, acls, memberships)


class Retriever:
    def __init__(
        self,
        backend: RecallBackend,
        repository: ChunkRepository,
        permissions: PermissionChecker,
        config: RetrievalConfig,
        cache: QueryCache | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        if config.rerank_enabled and reranker is None:
            raise ValueError("rerank_enabled=True 时必须注入重排器")
        self._backend = backend
        self._repo = repository
        self._permissions = permissions
        self._config = config
        self._cache: QueryCache = cache if cache is not None else InMemoryQueryCache()
        self._reranker = reranker

    @property
    def cache(self) -> QueryCache:
        return self._cache

    async def retrieve(
        self,
        ctx: DelegationContext,
        query: str,
        *,
        tenant_id: str,
        k: int | None = None,
        metadata: MetadataFilter | None = None,
    ) -> list[RetrievedChunk]:
        cfg = self._config
        top_k = cfg.top_k if k is None else k
        if top_k <= 0:
            raise ValueError("k 必须 > 0")
        fetch_k = max(top_k, cfg.rerank_top_k) if cfg.rerank_enabled else top_k

        memberships = await self._permissions.memberships(ctx)
        allowed_docs = await self._permissions.allowed_doc_ids(ctx, memberships)
        if not allowed_docs:
            return []

        flt = RecallFilter(
            tenant_id=tenant_id,
            allowed_doc_ids=allowed_docs,
            audience_allowlist=cfg.audience_allowlist,
            metadata=metadata or MetadataFilter(),
        )

        key = make_cache_key(
            tenant_id=tenant_id,
            query=query,
            metadata_signature=flt.metadata.signature(),
        )
        cached = await self._cache.get(tenant_id, key)
        if cached is not None:
            candidates = await self._load_cached(cached, flt)
        else:
            candidates = await self._recall_and_fuse(query, flt, fetch_k)
            await self._cache.put(tenant_id, key, tuple(c.record.chunk_id for c in candidates))

        approved = await self._revalidate(ctx, candidates, memberships, fetch_k)
        if cfg.rerank_enabled and self._reranker is not None and approved:
            return await self._reranker.rerank(query, approved, limit=top_k)
        return approved[:top_k]

    async def _recall_and_fuse(
        self, query: str, flt: RecallFilter, fetch_k: int
    ) -> list[RetrievedChunk]:
        limit = max(fetch_k * self._config.overfetch, fetch_k)
        recall = await self._backend.search(query, limit=limit, recall_filter=flt)
        fused = fuse(recall, self._config)
        return [
            RetrievedChunk(record=scored.record, score=scored.score)
            for scored in fused
            if scored.score >= self._config.min_score
        ]

    async def _load_cached(
        self, chunk_ids: Sequence[str], flt: RecallFilter
    ) -> list[RetrievedChunk]:
        records = await self._repo.get_chunks(list(chunk_ids))
        by_id = {record.chunk_id: record for record in records}
        candidates: list[RetrievedChunk] = []
        for chunk_id in chunk_ids:
            record = by_id.get(chunk_id)
            if record is None:
                continue
            # 缓存不按权限分区：必须先按当前请求的召回过滤条件收敛，
            # 再交给下面的实时二次校验。
            # 取舍：若缓存由另一个权限更宽的用户填充，当前用户可能少拿到
            # 本该可见的候选（不会越权，只会少召回）。安全优先。
            if not flt.allows(record):
                continue
            candidates.append(RetrievedChunk(record=record, score=0.0))
        return candidates

    async def _revalidate(
        self,
        ctx: DelegationContext,
        candidates: Sequence[RetrievedChunk],
        memberships: frozenset[str],
        limit: int,
    ) -> list[RetrievedChunk]:
        results: list[RetrievedChunk] = []
        for candidate in candidates:
            if not await self._permissions.is_allowed(ctx, candidate.record.doc_id, memberships):
                continue
            results.append(candidate)
            if len(results) >= limit:
                break
        return results
