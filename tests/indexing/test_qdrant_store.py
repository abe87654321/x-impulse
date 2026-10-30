"""Qdrant REST 适配器 —— 请求形状与响应解析（不连真实服务）。"""

from __future__ import annotations

import json

from kb.indexing.vector_store import QdrantVectorStore, VectorPoint


class FakeTransport:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, url, headers, body, timeout):
        parsed = json.loads(body) if body else None
        self.calls.append((method, url, parsed))
        return self.responses.pop(0)


async def test_ensure_collection_creates_when_missing():
    transport = FakeTransport([(404, {}), (200, {})])
    store = QdrantVectorStore(base_url="http://qdrant:6333/", transport=transport)

    await store.ensure_collection("kb", 16)

    assert transport.calls[0][:2] == ("GET", "http://qdrant:6333/collections/kb")
    method, url, body = transport.calls[1]
    assert method == "PUT"
    assert url == "http://qdrant:6333/collections/kb"
    assert body == {"vectors": {"size": 16, "distance": "Cosine"}}


async def test_upsert_uses_stable_uuid_and_payload():
    transport = FakeTransport([(200, {})])
    store = QdrantVectorStore(transport=transport)

    await store.upsert(
        "kb",
        [VectorPoint("chk_a", "doc_a", "t1", "internal_only", (0.1, 0.2))],
    )

    _, url, body = transport.calls[0]
    assert url.endswith("/collections/kb/points?wait=true")
    point = body["points"][0]
    assert point["payload"] == {
        "chunk_id": "chk_a",
        "doc_id": "doc_a",
        "tenant_id": "t1",
        "audience": "internal_only",
    }
    assert len(point["id"]) == 36  # UUID


async def test_search_pushes_permission_filter_to_qdrant():
    transport = FakeTransport(
        [
            (
                200,
                {
                    "result": [
                        {
                            "score": 0.9,
                            "payload": {"chunk_id": "chk_a", "doc_id": "doc_a"},
                        }
                    ]
                },
            )
        ]
    )
    store = QdrantVectorStore(transport=transport)

    hits = await store.search(
        "kb",
        (0.1, 0.2),
        tenant_id="t1",
        allowed_doc_ids=frozenset({"doc_a"}),
        audience_allowlist=frozenset({"internal_only"}),
        limit=5,
    )

    _, _, body = transport.calls[0]
    must = body["filter"]["must"]
    assert {"key": "tenant_id", "match": {"value": "t1"}} in must
    assert {"key": "doc_id", "match": {"any": ["doc_a"]}} in must
    assert {"key": "audience", "match": {"any": ["internal_only"]}} in must
    assert hits[0].chunk_id == "chk_a"
    assert hits[0].score == 0.9


async def test_search_empty_allowlist_skips_network():
    transport = FakeTransport([])
    store = QdrantVectorStore(transport=transport)

    hits = await store.search(
        "kb",
        (1.0,),
        tenant_id="t1",
        allowed_doc_ids=frozenset(),
        audience_allowlist=frozenset({"internal_only"}),
        limit=5,
    )

    assert hits == []
    assert transport.calls == []


async def test_delete_document_uses_filter():
    transport = FakeTransport([(200, {})])
    store = QdrantVectorStore(transport=transport)

    await store.delete_document("kb", tenant_id="t1", doc_id="doc_a")

    _, url, body = transport.calls[0]
    assert url.endswith("/collections/kb/points/delete?wait=true")
    must = body["filter"]["must"]
    assert {"key": "doc_id", "match": {"value": "doc_a"}} in must
