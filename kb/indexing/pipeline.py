"""索引流水线：连接器 → 解析 → 规范化 → 切分 → 落库（含 ACL）。

本切片只处理文本类源，且只做全量扫描的 upsert：
- 内容未变（content_hash 相同）跳过重切分
- ACL 与内容在同一事务落库（O-3）
- 抑制清单在文档与 chunk 两个粒度前置检查（O-1/O-2）
- 删除传播由后续 sync 编排负责，不在本流水线

逐文档失败只计数并继续；数据库错误直接上抛（基础设施故障不应被吞掉）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from kb.indexing.ids import chunk_id as make_chunk_id
from kb.indexing.ids import document_id as make_document_id
from kb.indexing.models import (
    IndexingStats,
    PreparedChunk,
    PreparedDocument,
    ReindexOutcome,
    StoredDocument,
)
from kb.indexing.parser import Parser, TextParser
from kb.indexing.store import DocumentStore
from kb.indexing.suppression import NullSuppressionChecker, SuppressionChecker
from kb.indexing.vector_index import VectorIndexer
from kb.ingestion.contract import (
    AclSet,
    Connector,
    ConnectorError,
    DocumentRef,
    RawDocument,
    Scope,
)
from kb.parsing.chunking import (
    Chunk,
    ChunkingConfig,
    TokenCounter,
    chunk_document,
    estimate_tokens,
)
from kb.parsing.normalize import (
    NORMALIZATION_VERSION,
    content_hash,
    normalize,
)


class SubjectIndexer(Protocol):
    async def index_chunk(self, tenant_id: str, chunk_id: str, text: str) -> int: ...


class IndexingPipeline:
    def __init__(
        self,
        connector: Connector,
        store: DocumentStore,
        *,
        tenant_id: str,
        source_id: str,
        parser: Parser | None = None,
        chunking: ChunkingConfig | None = None,
        token_counter: TokenCounter = estimate_tokens,
        suppression: SuppressionChecker | None = None,
        vector_indexer: VectorIndexer | None = None,
        subject_indexer: SubjectIndexer | None = None,
    ) -> None:
        self._connector = connector
        self._store = store
        self._tenant_id = tenant_id
        self._source_id = source_id
        self._parser = parser or TextParser()
        self._chunking = chunking or ChunkingConfig()
        self._token_counter = token_counter
        self._suppression = suppression or NullSuppressionChecker()
        self._vector_indexer = vector_indexer
        self._subject_indexer = subject_indexer

    async def run(self, scope: Scope | None = None) -> IndexingStats:
        scanned = indexed = skipped = suppressed = failed = 0
        errors: list[str] = []

        async for ref in self._connector.full_scan(scope or Scope()):
            scanned += 1
            outcome = await self.index_ref(ref)
            if outcome.status == "indexed":
                indexed += 1
            elif outcome.status == "skipped_unchanged":
                skipped += 1
            elif outcome.status == "suppressed":
                suppressed += 1
            else:
                failed += 1
                if outcome.error:
                    errors.append(outcome.error)

        return IndexingStats(
            scanned=scanned,
            indexed=indexed,
            skipped_unchanged=skipped,
            suppressed=suppressed,
            failed=failed,
            errors=tuple(errors),
        )

    async def index_ref(self, ref: DocumentRef) -> ReindexOutcome:
        """索引单个文档。增量同步也走这里。"""
        try:
            if await self._suppression.is_suppressed(self._tenant_id, "document", ref.external_id):
                return ReindexOutcome(status="suppressed")

            raw = await self._connector.fetch_content(ref)
            text = self._parser.parse(raw)
            normalized = normalize(text)
            digest = content_hash(normalized)

            existing = await self._store.get_document(
                self._tenant_id, self._source_id, ref.external_id
            )
            if existing is not None and existing.content_hash == digest:
                return ReindexOutcome(status="skipped_unchanged")

            chunks = chunk_document(normalized, self._chunking, token_counter=self._token_counter)
            kept = await self._drop_suppressed_chunks(chunks)
            if not kept:
                return ReindexOutcome(status="suppressed")

            acl = await self._acl_for(ref)
            prepared = self._prepare(ref, raw, normalized, digest, existing, kept, acl)
            await self._store.apply(prepared)
            if self._vector_indexer is not None:
                await self._vector_indexer.index(prepared, self._store)
            if self._subject_indexer is not None:
                for chunk in prepared.chunks:
                    await self._subject_indexer.index_chunk(
                        self._tenant_id, chunk.chunk_id, chunk.content
                    )
            return ReindexOutcome(status="indexed", created=existing is None)
        except ConnectorError as exc:
            return ReindexOutcome(status="failed", error=f"{ref.external_id}: {exc}")

    async def _drop_suppressed_chunks(self, chunks: Sequence[Chunk]) -> list[Chunk]:
        kept: list[Chunk] = []
        for chunk in chunks:
            if await self._suppression.is_suppressed(
                self._tenant_id, "chunk_hash", chunk.content_hash
            ):
                continue
            kept.append(chunk)
        return kept

    async def _acl_for(self, ref: DocumentRef) -> AclSet:
        if not self._connector.capabilities().supports_acl:
            return AclSet(
                entries=(),
                inherited_from=None,
                inheritance_broken=False,
                fetched_at=datetime.now(UTC),
            )
        return await self._connector.fetch_acl(ref)

    def _prepare(
        self,
        ref: DocumentRef,
        raw: RawDocument,
        normalized: str,
        digest: str,
        existing: StoredDocument | None,
        chunks: Sequence[Chunk],
        acl: AclSet,
    ) -> PreparedDocument:
        metadata = raw.metadata
        return PreparedDocument(
            tenant_id=self._tenant_id,
            doc_id=make_document_id(self._source_id, ref.external_id),
            source_id=self._source_id,
            external_id=ref.external_id,
            title=_metadata_str(metadata, "title") or ref.path or ref.external_id,
            url=_metadata_str(metadata, "url"),
            author=_metadata_str(metadata, "author"),
            doc_type=_metadata_str(metadata, "doc_type"),
            department=_metadata_str(metadata, "department"),
            content_hash=digest,
            parser_version=self._parser.version,
            normalization_version=NORMALIZATION_VERSION,
            audience="internal_only",
            doc_version=_next_version(existing),
            source_updated_at=ref.updated_at,
            chunks=tuple(
                PreparedChunk(
                    chunk_id=make_chunk_id(
                        make_document_id(self._source_id, ref.external_id), chunk.seq
                    ),
                    seq=chunk.seq,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    token_count=chunk.token_count,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    page_no=chunk.page_no,
                    section_path=chunk.section_path,
                    clause_no=chunk.clause_no,
                    chunker_version=chunk.chunker_version,
                )
                for chunk in chunks
            ),
            acl_entries=tuple(acl.entries),
            inheritance_broken=acl.inheritance_broken,
        )


def _metadata_str(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return None if value is None else str(value)


def _next_version(existing: StoredDocument | None) -> str:
    if existing is None:
        return "1"
    try:
        return str(int(existing.version) + 1)
    except ValueError:
        return "1"
