"""检索与重排 —— 召回阶段过滤、融合、缓存与二次校验。"""

from kb.retrieval.cache import (
    InMemoryQueryCache,
    PostgresQueryCache,
    QueryCache,
    build_query_cache_from_env,
    make_cache_key,
)
from kb.retrieval.evaluation import (
    EvaluationCase,
    RerankDecision,
    RetrievalBaseline,
    decide_rerank,
    evaluate_baseline,
    load_eval_cases,
)
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
from kb.retrieval.rerank import (
    CrossEncoderReranker,
    RerankConfigError,
    Reranker,
    build_reranker_from_env,
)
from kb.retrieval.retriever import (
    AclPermissionChecker,
    AclRepository,
    ChunkRepository,
    PermissionChecker,
    RecallBackend,
    Retriever,
)

__all__ = [
    "AclPermissionChecker",
    "AclRepository",
    "CrossEncoderReranker",
    "EvaluationCase",
    "ChunkRecord",
    "ChunkRepository",
    "HybridRecall",
    "HybridRecallBackend",
    "HybridRetrievalService",
    "InMemoryQueryCache",
    "PostgresQueryCache",
    "MetadataFilter",
    "PgChunkRepository",
    "PermissionChecker",
    "QueryCache",
    "build_query_cache_from_env",
    "RerankConfigError",
    "RerankDecision",
    "Reranker",
    "RecallBackend",
    "RecallFilter",
    "RetrievalBaseline",
    "RetrievalConfig",
    "RetrievedChunk",
    "Retriever",
    "ScoredChunk",
    "TenantAclRepository",
    "build_reranker_from_env",
    "decide_rerank",
    "evaluate_baseline",
    "fuse",
    "load_audience_allowlist",
    "load_eval_cases",
    "make_cache_key",
    "rrf_fuse",
    "weighted_fuse",
]
