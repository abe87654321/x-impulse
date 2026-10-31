"""检索编排的安全性质 —— I-3 / FR-04-07 / FR-06-03 / FR-04-14a。

这些测试守的是"任何配置下无权内容都不出现在召回结果中"。
"""

from __future__ import annotations

from datetime import UTC, datetime

from kb.retrieval.cache import InMemoryQueryCache
from kb.retrieval.models import MetadataFilter, RetrievalConfig
from kb.retrieval.retriever import Retriever
from tests.retrieval.fakes import (
    FakeBackend,
    FakeRepository,
    MapPermissions,
    make_ctx,
    make_record,
)


def _config(**kwargs) -> RetrievalConfig:
    return RetrievalConfig(audience_allowlist=frozenset({"internal_only"}), **kwargs)


def _retriever(records, permissions, *, config=None, cache=None) -> Retriever:
    return Retriever(
        backend=FakeBackend(records),
        repository=FakeRepository(records),
        permissions=permissions,
        config=config or _config(),
        cache=cache,
    )


async def test_permission_filter_is_pushed_to_recall_stage():
    records = [make_record("c_a", doc_id="doc_a"), make_record("c_b", doc_id="doc_b")]
    perms = MapPermissions({"alice": frozenset({"doc_a"})})
    r = _retriever(records, perms)

    result = await r.retrieve(make_ctx(), "q", tenant_id="t1")

    assert [x.record.chunk_id for x in result] == ["c_a"]
    backend = r._backend  # type: ignore[attr-defined]
    assert backend.last_filter is not None
    assert backend.last_filter.allowed_doc_ids == frozenset({"doc_a"})


async def test_secondary_revalidation_drops_revoked_and_keeps_k():
    records = [
        make_record("c_a", doc_id="doc_a"),
        make_record("c_b", doc_id="doc_b"),
        make_record("c_c", doc_id="doc_c"),
    ]
    perms = MapPermissions(
        {"alice": frozenset({"doc_a", "doc_b", "doc_c"})}, denied_docs=frozenset({"doc_b"})
    )
    r = _retriever(records, perms)

    result = await r.retrieve(make_ctx(), "q", tenant_id="t1", k=2)

    # R-2：被二次校验拒绝的候选不计入 k，继续取下一个。
    assert [x.record.chunk_id for x in result] == ["c_a", "c_c"]


async def test_no_allowed_docs_returns_empty_without_recall():
    records = [make_record("c_a", doc_id="doc_a")]
    perms = MapPermissions({"alice": frozenset()})
    r = _retriever(records, perms)

    result = await r.retrieve(make_ctx(), "q", tenant_id="t1")

    assert result == []
    assert r._backend.calls == 0  # type: ignore[attr-defined]


async def test_audience_filter_is_config_driven():
    records = [
        make_record("c_in", doc_id="doc_in", audience="internal_only"),
        make_record("c_out", doc_id="doc_out", audience="customer_safe"),
    ]
    perms = MapPermissions({"alice": frozenset({"doc_in", "doc_out"})})

    internal = _retriever(records, perms)
    assert [
        x.record.chunk_id for x in await internal.retrieve(make_ctx(), "q", tenant_id="t1")
    ] == ["c_in"]

    customer = _retriever(
        records,
        perms,
        config=RetrievalConfig(audience_allowlist=frozenset({"customer_safe"})),
    )
    assert [
        x.record.chunk_id for x in await customer.retrieve(make_ctx(), "q", tenant_id="t1")
    ] == ["c_out"]


async def test_metadata_filter_applied():
    records = [
        make_record("c_hr", doc_id="doc_hr", department="hr"),
        make_record("c_eng", doc_id="doc_eng", department="eng"),
    ]
    perms = MapPermissions({"alice": frozenset({"doc_hr", "doc_eng"})})
    r = _retriever(records, perms)

    result = await r.retrieve(
        make_ctx(), "q", tenant_id="t1", metadata=MetadataFilter(departments=frozenset({"hr"}))
    )
    assert [x.record.chunk_id for x in result] == ["c_hr"]


async def test_expired_document_excluded_when_requested():
    past = datetime(2025, 1, 1, tzinfo=UTC)
    records = [
        make_record("c_old", doc_id="doc_old", effective_until=past),
        make_record("c_new", doc_id="doc_new"),
    ]
    perms = MapPermissions({"alice": frozenset({"doc_old", "doc_new"})})
    r = _retriever(records, perms)

    result = await r.retrieve(
        make_ctx(),
        "q",
        tenant_id="t1",
        metadata=MetadataFilter(exclude_expired_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    assert [x.record.chunk_id for x in result] == ["c_new"]


async def test_threshold_excludes_low_scores():
    records = [make_record("c_a", doc_id="doc_a")]
    perms = MapPermissions({"alice": frozenset({"doc_a"})})
    r = _retriever(records, perms, config=_config(min_score=1.5))

    assert await r.retrieve(make_ctx(), "q", tenant_id="t1") == []


async def test_cache_hit_never_leaks_another_users_documents():
    records = [make_record("c_a", doc_id="doc_a"), make_record("c_b", doc_id="doc_b")]
    cache = InMemoryQueryCache()
    backend = FakeBackend(records)
    repo = FakeRepository(records)

    alice = Retriever(
        backend,
        repo,
        MapPermissions({"alice": frozenset({"doc_a"})}),
        _config(),
        cache,
    )
    bob = Retriever(
        backend,
        repo,
        MapPermissions({"bob": frozenset({"doc_b"})}),
        _config(),
        cache,
    )

    assert [
        x.record.chunk_id for x in await alice.retrieve(make_ctx("alice"), "q", tenant_id="t1")
    ] == ["c_a"]
    bob_result = await bob.retrieve(make_ctx("bob"), "q", tenant_id="t1")

    assert backend.calls == 1  # 第二次走缓存
    # 缓存不按权限分区，唯一允许的结果是"Bob 自己可见"的那部分。
    # 被缓存收敛掉的候选可以少返回，但绝不能被返回 —— 安全优先于召回完整性。
    assert "c_a" not in {x.record.chunk_id for x in bob_result}
    assert all(x.record.doc_id == "doc_b" for x in bob_result)


async def test_cache_hit_still_revalidates_revoked_permission():
    records = [make_record("c_a", doc_id="doc_a"), make_record("c_b", doc_id="doc_b")]
    cache = InMemoryQueryCache()
    backend = FakeBackend(records)
    repo = FakeRepository(records)
    ctx = make_ctx("alice")

    before = Retriever(
        backend,
        repo,
        MapPermissions({"alice": frozenset({"doc_a", "doc_b"})}),
        _config(),
        cache,
    )
    first = await before.retrieve(ctx, "q", tenant_id="t1", k=5)
    assert {x.record.chunk_id for x in first} == {"c_a", "c_b"}

    after = Retriever(
        backend,
        repo,
        MapPermissions({"alice": frozenset({"doc_a", "doc_b"})}, denied_docs=frozenset({"doc_b"})),
        _config(),
        cache,
    )
    second = await after.retrieve(ctx, "q", tenant_id="t1", k=5)

    assert backend.calls == 1  # 命中缓存
    assert {x.record.chunk_id for x in second} == {"c_a"}


async def test_chunk_point_invalidation_on_cache_hit():
    records = [make_record("c_1", doc_id="doc_1"), make_record("c_2", doc_id="doc_2")]
    cache = InMemoryQueryCache()
    backend = FakeBackend(records)
    r = Retriever(
        backend,
        FakeRepository(records),
        MapPermissions({"alice": frozenset({"doc_1", "doc_2"})}),
        _config(),
        cache,
    )

    first = await r.retrieve(make_ctx(), "q", tenant_id="t1", k=5)
    assert {x.record.chunk_id for x in first} == {"c_1", "c_2"}

    await cache.invalidate_chunk("t1", "c_1")
    second = await r.retrieve(make_ctx(), "q", tenant_id="t1", k=5)

    assert backend.calls == 1  # 仍是缓存命中
    assert {x.record.chunk_id for x in second} == {"c_2"}


async def test_cache_is_tenant_scoped():
    records = [
        make_record("c_t1", doc_id="doc_t1", tenant_id="t1"),
        make_record("c_t2", doc_id="doc_t2", tenant_id="t2"),
    ]
    cache = InMemoryQueryCache()
    backend = FakeBackend(records)
    r = Retriever(
        backend,
        FakeRepository(records),
        MapPermissions({"alice": frozenset({"doc_t1", "doc_t2"})}),
        _config(),
        cache,
    )

    assert [x.record.chunk_id for x in await r.retrieve(make_ctx(), "q", tenant_id="t1")] == [
        "c_t1"
    ]
    assert [x.record.chunk_id for x in await r.retrieve(make_ctx(), "q", tenant_id="t2")] == [
        "c_t2"
    ]
    assert backend.calls == 2
