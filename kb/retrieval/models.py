"""检索核心类型 —— FR-04 混合检索 / 元数据过滤 / 受众过滤 / 阈值。

本模块只放数据结构与纯判定逻辑，不做 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

FusionStrategy = Literal["weighted", "rrf"]


@dataclass(frozen=True)
class ChunkRecord:
    """检索返回所需的最小 chunk 视图（含引用快照字段）。"""

    chunk_id: str
    doc_id: str
    tenant_id: str
    content: str
    chunk_version: int
    chunk_content_hash: str
    audience: str
    department: str | None
    doc_type: str | None
    effective_until: datetime | None
    status: str
    source_updated_at: datetime | None
    title: str
    seq: int
    char_start: int
    char_end: int
    page_no: int | None = None
    section_path: str | None = None
    clause_no: str | None = None
    doc_version: str = "1"


@dataclass(frozen=True)
class ScoredChunk:
    record: ChunkRecord
    score: float


@dataclass(frozen=True)
class HybridRecall:
    """两路召回的原始结果；融合由核心负责。"""

    bm25: tuple[ScoredChunk, ...]
    vector: tuple[ScoredChunk, ...]


@dataclass(frozen=True)
class RetrievedChunk:
    record: ChunkRecord
    score: float


@dataclass(frozen=True)
class MetadataFilter:
    """FR-04-06：部门 / 文档类型 / 时间范围 / 有效期。空集合 = 不过滤。"""

    departments: frozenset[str] = frozenset()
    doc_types: frozenset[str] = frozenset()
    updated_after: datetime | None = None
    exclude_expired_at: datetime | None = None

    def allows(self, record: ChunkRecord) -> bool:
        if self.departments and (record.department or "") not in self.departments:
            return False
        if self.doc_types and (record.doc_type or "") not in self.doc_types:
            return False
        if self.updated_after is not None:
            if record.source_updated_at is None or record.source_updated_at < self.updated_after:
                return False
        if (
            self.exclude_expired_at is not None
            and record.effective_until is not None
            and record.effective_until <= self.exclude_expired_at
        ):
            return False
        return True

    def signature(self) -> str:
        """稳定的过滤条件指纹，用于缓存键（不含任何权限信息）。"""
        depts = ",".join(sorted(self.departments))
        types = ",".join(sorted(self.doc_types))
        updated = self.updated_after.isoformat() if self.updated_after else ""
        expired = self.exclude_expired_at.isoformat() if self.exclude_expired_at else ""
        return f"d={depts}|t={types}|u={updated}|e={expired}"


@dataclass(frozen=True)
class RecallFilter:
    """下推到召回阶段的过滤条件。**必须先于召回应用**（I-3 / FR-04-07）。"""

    tenant_id: str
    allowed_doc_ids: frozenset[str]
    audience_allowlist: frozenset[str]
    metadata: MetadataFilter = MetadataFilter()

    def allows(self, record: ChunkRecord) -> bool:
        if record.tenant_id != self.tenant_id:
            return False
        if record.status != "indexed":
            return False
        if record.doc_id not in self.allowed_doc_ids:
            return False
        if record.audience not in self.audience_allowlist:
            return False
        return self.metadata.allows(record)


@dataclass(frozen=True)
class RetrievalConfig:
    # 受众规则由部署档案提供（FR-04-16），不得硬编码 —— 无默认值。
    audience_allowlist: frozenset[str]
    top_k: int = 10
    overfetch: int = 3
    min_score: float = 0.0
    fusion: FusionStrategy = "weighted"
    # FR-04-05：默认取向必须偏向 BM25，不得沿用向量为主的行业默认。
    bm25_weight: float = 0.65
    vector_weight: float = 0.35
    rrf_k: int = 60
    # FR-04-08：默认关闭，是否启用由 FR-04-17 的实测差值决定。
    rerank_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.audience_allowlist:
            raise ValueError("audience_allowlist 不能为空：受众规则必须显式配置")
        if self.top_k <= 0:
            raise ValueError("top_k 必须 > 0")
        if self.overfetch < 1:
            raise ValueError("overfetch 必须 >= 1")
        if self.min_score < 0:
            raise ValueError("min_score 必须 >= 0")
        if self.bm25_weight < 0 or self.vector_weight < 0:
            raise ValueError("融合权重不得为负")
        if self.bm25_weight + self.vector_weight <= 0:
            raise ValueError("融合权重之和必须 > 0")
        if self.rrf_k <= 0:
            raise ValueError("rrf_k 必须 > 0")
