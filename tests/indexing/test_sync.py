"""增量同步编排：新增/更新、删除传播、cursor 推进与 sync_runs 记录。"""

from __future__ import annotations

from kb.indexing.deletion import DocumentDeletionService
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.sync import SourceSyncService
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.cache import InMemoryQueryCache
from tests.indexing.fakes import (
    FakeConnector,
    FakeDoc,
    InMemoryStore,
    InMemorySyncRunRepository,
)

TENANT = "default"
SOURCE = "src_sync"
COLLECTION = "sync_it"


def _stack(connector: FakeConnector):
    store = InMemoryStore()
    vectors = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=16)
    pipeline = IndexingPipeline(
        connector,
        store,
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=10, max_tokens=20, min_tokens=3, overlap_ratio=0.0),
        vector_indexer=VectorIndexer(
            embedder, vectors, collection=COLLECTION, vector_store_name="memory"
        ),
    )
    deletion = DocumentDeletionService(store, vectors, InMemoryQueryCache(), collection=COLLECTION)
    runs = InMemorySyncRunRepository()
    sync = SourceSyncService(
        connector,
        pipeline,
        deletion,
        store,
        runs,
        tenant_id=TENANT,
        source_id=SOURCE,
    )
    return sync, store, runs


async def test_incremental_adds_and_updates_and_advances_cursor():
    connector = FakeConnector([FakeDoc("ext-1", "第一版。"), FakeDoc("ext-2", "保持。")])
    sync, store, runs = _stack(connector)

    full = await sync.run_full()
    assert full.status == "succeeded"
    assert full.stats.added == 2
    assert full.cursor_after == "tok-1"

    connector.docs["ext-1"].text = "第二版，已更新。"
    connector.docs["ext-3"] = FakeDoc("ext-3", "新增文档。")
    connector.incremental_ids = ["ext-1", "ext-3"]
    connector.cursor_token = "tok-2"

    result = await sync.run_incremental()

    assert result.status == "succeeded"
    assert result.cursor_before == "tok-1"
    assert result.cursor_after == "tok-2"
    assert result.stats.updated == 1
    assert result.stats.added == 1
    assert document_id(SOURCE, "ext-3") in store.prepared
    assert runs.cursor == "tok-2"
    assert [run[2].mode for run in runs.runs] == ["full", "incremental"]


async def test_incremental_detects_deletion_and_removes_document():
    connector = FakeConnector([FakeDoc("ext-1", "保留。"), FakeDoc("ext-2", "将被删除。")])
    sync, store, _ = _stack(connector)

    await sync.run_full()
    assert document_id(SOURCE, "ext-2") in store.prepared

    connector.deleted_ids = {"ext-2"}
    connector.cursor_token = "tok-delete"
    result = await sync.run_incremental()

    assert result.stats.deleted == 1
    assert document_id(SOURCE, "ext-2") not in store.prepared
    assert document_id(SOURCE, "ext-1") in store.prepared
