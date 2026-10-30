"""索引流水线的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kb.ingestion.contract import AclEntry


@dataclass(frozen=True)
class StoredDocument:
    doc_id: str
    content_hash: str
    version: str


@dataclass(frozen=True)
class PreparedChunk:
    chunk_id: str
    seq: int
    content: str
    content_hash: str
    token_count: int
    char_start: int
    char_end: int
    page_no: int | None
    section_path: str | None
    clause_no: str | None
    chunker_version: str


@dataclass(frozen=True)
class PreparedDocument:
    tenant_id: str
    doc_id: str
    source_id: str
    external_id: str
    title: str
    url: str | None
    author: str | None
    doc_type: str | None
    department: str | None
    content_hash: str
    parser_version: str
    normalization_version: str
    audience: str
    doc_version: str
    source_updated_at: datetime | None
    chunks: tuple[PreparedChunk, ...]
    acl_entries: tuple[AclEntry, ...]
    inheritance_broken: bool


@dataclass(frozen=True)
class ChunkVectorEntry:
    chunk_id: str
    model_name: str
    model_version: str
    vector_store: str
    collection: str


ReindexStatus = str  # indexed | skipped_unchanged | suppressed | failed


@dataclass(frozen=True)
class ReindexOutcome:
    status: ReindexStatus
    error: str | None = None
    created: bool = False


@dataclass(frozen=True)
class IndexingStats:
    scanned: int = 0
    indexed: int = 0
    skipped_unchanged: int = 0
    suppressed: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()
