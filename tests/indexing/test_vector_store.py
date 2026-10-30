"""内存向量库：召回阶段必须按租户/文档/受众过滤（I-3）。"""

from __future__ import annotations

from kb.indexing.vector_store import InMemoryVectorStore, VectorPoint


async def test_search_filters_before_returning():
    store = InMemoryVectorStore()
    await store.ensure_collection("c", 2)
    await store.upsert(
        "c",
        [
            VectorPoint("chk_a", "doc_a", "t1", "internal_only", (1.0, 0.0)),
            VectorPoint("chk_b", "doc_b", "t1", "internal_only", (1.0, 0.0)),
            VectorPoint("chk_c", "doc_c", "t2", "internal_only", (1.0, 0.0)),
            VectorPoint("chk_d", "doc_d", "t1", "customer_safe", (1.0, 0.0)),
        ],
    )

    hits = await store.search(
        "c",
        (1.0, 0.0),
        tenant_id="t1",
        allowed_doc_ids=frozenset({"doc_a", "doc_d"}),
        audience_allowlist=frozenset({"internal_only"}),
        limit=10,
    )

    assert [hit.chunk_id for hit in hits] == ["chk_a"]


async def test_search_with_empty_allowlist_returns_nothing():
    store = InMemoryVectorStore()
    await store.ensure_collection("c", 2)
    await store.upsert("c", [VectorPoint("chk_a", "doc_a", "t1", "internal_only", (1.0, 0.0))])

    hits = await store.search(
        "c",
        (1.0, 0.0),
        tenant_id="t1",
        allowed_doc_ids=frozenset(),
        audience_allowlist=frozenset({"internal_only"}),
        limit=10,
    )

    assert hits == []


async def test_delete_document_removes_only_that_document():
    store = InMemoryVectorStore()
    await store.ensure_collection("c", 2)
    await store.upsert(
        "c",
        [
            VectorPoint("chk_a", "doc_a", "t1", "internal_only", (1.0, 0.0)),
            VectorPoint("chk_b", "doc_b", "t1", "internal_only", (1.0, 0.0)),
        ],
    )

    await store.delete_document("c", tenant_id="t1", doc_id="doc_a")
    hits = await store.search(
        "c",
        (1.0, 0.0),
        tenant_id="t1",
        allowed_doc_ids=frozenset({"doc_a", "doc_b"}),
        audience_allowlist=frozenset({"internal_only"}),
        limit=10,
    )

    assert [hit.chunk_id for hit in hits] == ["chk_b"]
