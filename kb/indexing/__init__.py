"""索引流水线 —— 连接器数据落库、切分与 ACL 同步。"""

from kb.indexing.deletion import (
    CacheInvalidator,
    DeletionResult,
    DocumentDeletionService,
)
from kb.indexing.embedding import Embedder, HashingEmbedder
from kb.indexing.ids import acl_id, chunk_id, document_id
from kb.indexing.models import (
    ChunkVectorEntry,
    IndexingStats,
    PreparedChunk,
    PreparedDocument,
    StoredDocument,
)
from kb.indexing.parser import TEXT_PARSER_VERSION, Parser, TextParser
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import DocumentStore, PostgresDocumentStore
from kb.indexing.suppression import (
    InMemorySuppressionChecker,
    NullSuppressionChecker,
    PostgresSuppressionChecker,
    SuppressionChecker,
)
from kb.indexing.sync import (
    PostgresSyncRunRepository,
    SourceSyncService,
    SyncResult,
    SyncRunRepository,
    SyncStats,
)
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorHit,
    VectorPoint,
    VectorStore,
)

__all__ = [
    "TEXT_PARSER_VERSION",
    "CacheInvalidator",
    "ChunkVectorEntry",
    "DeletionResult",
    "DocumentDeletionService",
    "DocumentStore",
    "Embedder",
    "HashingEmbedder",
    "InMemorySuppressionChecker",
    "InMemoryVectorStore",
    "IndexingPipeline",
    "IndexingStats",
    "NullSuppressionChecker",
    "Parser",
    "PostgresDocumentStore",
    "PostgresSyncRunRepository",
    "PostgresSuppressionChecker",
    "PreparedChunk",
    "PreparedDocument",
    "SourceSyncService",
    "StoredDocument",
    "QdrantVectorStore",
    "SuppressionChecker",
    "SyncResult",
    "SyncRunRepository",
    "SyncStats",
    "TextParser",
    "VectorHit",
    "VectorIndexer",
    "VectorPoint",
    "VectorStore",
    "acl_id",
    "chunk_id",
    "document_id",
]
