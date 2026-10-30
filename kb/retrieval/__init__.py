"""检索与重排 —— 召回阶段过滤、融合、缓存与二次校验。"""

from kb.retrieval.cache import InMemoryQueryCache, make_cache_key
from kb.retrieval.fusion import fuse, rrf_fuse, weighted_fuse
from kb.retrieval.models import (
    ChunkRecord,
    HybridRecall,
    MetadataFilter,
    RecallFilter,
    RetrievalConfig,
    RetrievedChunk,
    ScoredChunk,
)
from kb.retrieval.pg_adapters import (
    HybridRecallBackend,
    HybridRetrievalService,
    PgChunkRepository,
    TenantAclRepository,
    load_audience_allowlist,
)
from kb.retrieval.retriever import (
    AclPermissionChecker,
    AclRepository,
    ChunkRepository,
    PermissionChecker,
    QueryCache,
    RecallBackend,
    Retriever,
)

__all__ = [
    "AclPermissionChecker",
    "AclRepository",
    "ChunkRecord",
    "ChunkRepository",
    "HybridRecall",
    "HybridRecallBackend",
    "HybridRetrievalService",
    "InMemoryQueryCache",
    "MetadataFilter",
    "PgChunkRepository",
    "PermissionChecker",
    "QueryCache",
    "RecallBackend",
    "RecallFilter",
    "RetrievalConfig",
    "RetrievedChunk",
    "Retriever",
    "ScoredChunk",
    "TenantAclRepository",
    "fuse",
    "load_audience_allowlist",
    "make_cache_key",
    "rrf_fuse",
    "weighted_fuse",
]
