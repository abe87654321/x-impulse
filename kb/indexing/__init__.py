"""索引流水线 —— 连接器数据落库、切分与 ACL 同步。"""

from kb.indexing.ids import acl_id, chunk_id, document_id
from kb.indexing.models import (
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

__all__ = [
    "TEXT_PARSER_VERSION",
    "DocumentStore",
    "InMemorySuppressionChecker",
    "IndexingPipeline",
    "IndexingStats",
    "NullSuppressionChecker",
    "Parser",
    "PostgresDocumentStore",
    "PostgresSuppressionChecker",
    "PreparedChunk",
    "PreparedDocument",
    "StoredDocument",
    "SuppressionChecker",
    "TextParser",
    "acl_id",
    "chunk_id",
    "document_id",
]
