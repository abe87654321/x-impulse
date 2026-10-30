"""索引流水线 —— 文本入库、变更检测、抑制清单、ACL 与字符锚点。"""

from __future__ import annotations

from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.suppression import InMemorySuppressionChecker, document_scope_key
from kb.parsing.chunking import ChunkingConfig, chunk_document
from kb.parsing.normalize import content_hash, normalize
from tests.indexing.fakes import FakeConnector, FakeDoc, InMemoryStore

TENANT = "default"
SOURCE = "src_test"


def _pipeline(connector, store, *, suppression=None) -> IndexingPipeline:
    return IndexingPipeline(
        connector,
        store,
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=20, max_tokens=30, min_tokens=5, overlap_ratio=0.0),
        suppression=suppression,
    )


async def test_indexes_new_document_with_acl_and_anchors():
    text = "报销需在三十日内提交。\r\n附发票原件。"
    store = InMemoryStore()
    pipeline = _pipeline(FakeConnector([FakeDoc("ext-1", text, title="报销制度")]), store)

    stats = await pipeline.run()

    assert stats.indexed == 1
    assert stats.failed == 0
    prepared = store.prepared[document_id(SOURCE, "ext-1")]
    normalized = normalize(text)
    assert prepared.content_hash == content_hash(normalized)
    assert prepared.audience == "internal_only"
    assert prepared.doc_version == "1"
    assert prepared.parser_version == "text-v1"
    assert prepared.acl_entries[0].principal_id == "alice"
    for chunk in prepared.chunks:
        assert chunk.content == normalized[chunk.char_start : chunk.char_end]


async def test_unchanged_document_is_skipped():
    store = InMemoryStore()
    pipeline = _pipeline(FakeConnector([FakeDoc("ext-1", "内容不变。")]), store)

    first = await pipeline.run()
    second = await pipeline.run()

    assert first.indexed == 1
    assert second.indexed == 0
    assert second.skipped_unchanged == 1
    prepared = store.prepared[document_id(SOURCE, "ext-1")]
    assert prepared.doc_version == "1"


async def test_changed_document_increments_version_and_rechunks():
    store = InMemoryStore()
    connector = FakeConnector([FakeDoc("ext-1", "第一版内容。")])
    pipeline = _pipeline(connector, store)

    await pipeline.run()
    connector.docs["ext-1"].text = "第二版内容，已经变更。"
    stats = await pipeline.run()

    assert stats.indexed == 1
    prepared = store.prepared[document_id(SOURCE, "ext-1")]
    assert prepared.doc_version == "2"
    assert prepared.content_hash == content_hash(normalize("第二版内容，已经变更。"))


async def test_suppressed_document_is_not_indexed():
    store = InMemoryStore()
    suppression = InMemorySuppressionChecker(
        {(TENANT, "document", document_scope_key(SOURCE, "ext-1"))}
    )
    pipeline = _pipeline(
        FakeConnector([FakeDoc("ext-1", "不应入库。")]), store, suppression=suppression
    )

    stats = await pipeline.run()

    assert stats.suppressed == 1
    assert stats.indexed == 0
    assert store.prepared == {}


async def test_suppressed_chunk_drops_whole_document_when_all_chunks_hit():
    text = "唯一的一段内容。"
    normalized = normalize(text)
    hashes = {chunk.content_hash for chunk in chunk_document(normalized)}
    suppression = InMemorySuppressionChecker({(TENANT, "chunk_hash", h) for h in hashes})
    store = InMemoryStore()
    pipeline = _pipeline(FakeConnector([FakeDoc("ext-1", text)]), store, suppression=suppression)

    stats = await pipeline.run()

    assert stats.suppressed == 1
    assert store.prepared == {}


async def test_non_text_format_is_counted_as_failed():
    store = InMemoryStore()
    pipeline = _pipeline(
        FakeConnector([FakeDoc("ext-1", "binary", mime_type="application/octet-stream")]),
        store,
    )

    stats = await pipeline.run()

    assert stats.failed == 1
    assert stats.indexed == 0
    assert store.prepared == {}
    assert stats.errors


async def test_empty_acl_is_persisted_as_default_deny():
    doc = FakeDoc("ext-1", "内容。")
    doc.acl = doc.acl.__class__(
        entries=(), inherited_from=None, inheritance_broken=False, fetched_at=doc.acl.fetched_at
    )
    store = InMemoryStore()
    pipeline = _pipeline(FakeConnector([doc]), store)

    await pipeline.run()

    prepared = store.prepared[document_id(SOURCE, "ext-1")]
    assert prepared.acl_entries == ()
