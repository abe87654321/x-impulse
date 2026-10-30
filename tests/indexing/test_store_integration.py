"""PostgresDocumentStore 的集成测试 —— 需要 `make up`。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.indexing.embedding import HashingEmbedder
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import PostgresDocumentStore
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.parsing.chunking import ChunkingConfig
from kb.parsing.normalize import content_hash, normalize
from tests.indexing.fakes import FakeConnector, FakeDoc

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_index_it"
EXTERNAL = "ext-it-1"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM acl_entries WHERE source_id = $1", SOURCE)
    db.execute(
        """
        DELETE FROM chunk_vectors cv USING chunks c
        WHERE c.chunk_id = cv.chunk_id AND c.doc_id = $1
        """,
        document_id(SOURCE, EXTERNAL),
    )
    db.execute("DELETE FROM chunks WHERE doc_id = $1", document_id(SOURCE, EXTERNAL))
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def _seed_source() -> None:
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '索引集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )


def _run(connector: FakeConnector) -> object:
    pipeline = IndexingPipeline(
        connector,
        PostgresDocumentStore(),
        tenant_id=TENANT,
        source_id=SOURCE,
        chunking=ChunkingConfig(target_tokens=20, max_tokens=30, min_tokens=5, overlap_ratio=0.0),
        vector_indexer=VectorIndexer(
            HashingEmbedder(dimension=16),
            InMemoryVectorStore(),
            collection="test_chunks",
            vector_store_name="memory",
        ),
    )
    return asyncio.run(pipeline.run())


def test_postgres_store_roundtrip_and_versions():
    _cleanup()
    _seed_source()
    try:
        connector = FakeConnector([FakeDoc(EXTERNAL, "第一版内容。")])
        first = _run(connector)
        assert first.indexed == 1  # type: ignore[attr-defined]

        db = Database()
        docs = db.fetch(
            "SELECT doc_id, version, audience, parser_version, content_hash "
            "FROM documents WHERE source_id = $1",
            SOURCE,
        )
        assert len(docs) == 1
        doc = docs[0]
        assert doc["audience"] == "internal_only"
        assert doc["parser_version"] == "text-v1"
        assert doc["version"] == "1"
        assert doc["content_hash"] == content_hash(normalize("第一版内容。"))

        chunks = db.fetch(
            "SELECT content, char_start, char_end, version FROM chunks WHERE doc_id = $1",
            doc["doc_id"],
        )
        assert chunks
        normalized = normalize("第一版内容。")
        for chunk in chunks:
            assert (
                chunk["content"]
                == normalized[int(str(chunk["char_start"])) : int(str(chunk["char_end"]))]
            )
            assert chunk["version"] == 1

        vectors = db.fetch(
            "SELECT count(*) AS n FROM chunk_vectors cv JOIN chunks c "
            "ON c.chunk_id = cv.chunk_id WHERE c.doc_id = $1",
            doc["doc_id"],
        )
        assert vectors[0]["n"] == len(chunks)

        acls = db.fetch(
            "SELECT principal_id, effect, inheritance_broken FROM acl_entries WHERE object_id = $1",
            doc["doc_id"],
        )
        assert acls == [{"principal_id": "alice", "effect": "allow", "inheritance_broken": False}]

        second = _run(connector)
        assert second.skipped_unchanged == 1  # type: ignore[attr-defined]

        connector.docs[EXTERNAL].text = "第二版内容，已变更。"
        third = _run(connector)
        assert third.indexed == 1  # type: ignore[attr-defined]

        docs = db.fetch("SELECT version FROM documents WHERE source_id = $1", SOURCE)
        assert docs[0]["version"] == "2"
        chunks = db.fetch(
            "SELECT version FROM chunks WHERE doc_id = $1", document_id(SOURCE, EXTERNAL)
        )
        assert chunks[0]["version"] == 2
        vectors = db.fetch(
            "SELECT count(*) AS n FROM chunk_vectors cv JOIN chunks c "
            "ON c.chunk_id = cv.chunk_id WHERE c.doc_id = $1",
            document_id(SOURCE, EXTERNAL),
        )
        assert vectors[0]["n"] == 1
    finally:
        _cleanup()
