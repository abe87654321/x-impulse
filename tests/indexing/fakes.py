"""索引流水线测试替身。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from kb.indexing.models import ChunkVectorEntry, PreparedDocument, StoredDocument
from kb.ingestion.contract import (
    AclEntry,
    AclSet,
    Capabilities,
    ChangeEvent,
    Connector,
    Cursor,
    DeletionDetection,
    DeletionEvent,
    DocumentRef,
    IncrementalMode,
    RawDocument,
    Scope,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


@dataclass
class FakeDoc:
    external_id: str
    text: str
    title: str = "文档"
    mime_type: str = "text/plain"
    acl: AclSet = field(
        default_factory=lambda: AclSet(
            entries=(AclEntry("user", "alice", "allow"),),
            inherited_from=None,
            inheritance_broken=False,
            fetched_at=NOW,
        )
    )

    @property
    def ref(self) -> DocumentRef:
        return DocumentRef(
            external_id=self.external_id,
            path=f"{self.title}.txt",
            version="1",
            updated_at=NOW,
            size_bytes=len(self.text.encode()),
            container_id=None,
        )


class FakeConnector(Connector):
    def __init__(self, docs: list[FakeDoc], *, supports_acl: bool = True) -> None:
        self.docs = {doc.external_id: doc for doc in docs}
        self._supports_acl = supports_acl
        self.incremental_ids: list[str] = []
        self.deleted_ids: set[str] = set()
        self.cursor_token = "tok-1"
        self.resume_token: str | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            incremental=IncrementalMode.TIMESTAMP,
            deletion_detection=DeletionDetection.ENUMERATE,
            supports_acl=self._supports_acl,
            supports_acl_deny=False,
            supports_inheritance_break=False,
            supports_webhook=False,
            supports_field_level_acl=False,
            max_page_size=100,
            rate_limit_qps=None,
            binary_formats=frozenset({"text/plain"}),
        )

    async def full_scan(self, scope: Scope) -> AsyncIterator[DocumentRef]:
        for doc in self.docs.values():
            yield doc.ref

    async def incremental_scan(
        self, scope: Scope, cursor: Cursor | None
    ) -> AsyncIterator[ChangeEvent]:
        for external_id in self.incremental_ids:
            doc = self.docs.get(external_id)
            if doc is not None:
                yield ChangeEvent(kind="updated", ref=doc.ref)

    async def detect_deletions(
        self, scope: Scope, known_ids: set[str]
    ) -> AsyncIterator[DeletionEvent]:
        for external_id in sorted(self.deleted_ids & known_ids):
            yield DeletionEvent(
                external_id=external_id,
                detected_by=DeletionDetection.ENUMERATE,
                detected_at=NOW,
            )

    async def fetch_acl(self, ref: DocumentRef) -> AclSet:
        return self.docs[ref.external_id].acl

    async def fetch_content(self, ref: DocumentRef) -> RawDocument:
        doc = self.docs[ref.external_id]
        return RawDocument(
            ref=doc.ref,
            content=doc.text.encode("utf-8"),
            mime_type=doc.mime_type,
            metadata={"title": doc.title},
        )

    def checkpoint(self) -> Cursor:
        return Cursor(source_id="fake-source", token=self.cursor_token, issued_at=NOW)

    def resume(self, cursor: Cursor) -> None:
        self.resume_token = cursor.token


class InMemorySyncRunRepository:
    def __init__(self) -> None:
        self.runs: list[tuple[str, str, object]] = []
        self.cursor: str | None = None

    async def latest_cursor(self, tenant_id: str, source_id: str) -> str | None:
        return self.cursor

    async def record(self, tenant_id: str, source_id: str, result) -> None:
        self.runs.append((tenant_id, source_id, result))
        if result.status == "succeeded":
            self.cursor = result.cursor_after


class InMemoryStore:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str, str], StoredDocument] = {}
        self.prepared: dict[str, PreparedDocument] = {}
        self.chunk_vectors: dict[str, ChunkVectorEntry] = {}

    async def get_document(
        self, tenant_id: str, source_id: str, external_id: str
    ) -> StoredDocument | None:
        return self.documents.get((tenant_id, source_id, external_id))

    async def apply(self, prepared: PreparedDocument) -> None:
        key = (prepared.tenant_id, prepared.source_id, prepared.external_id)
        self.documents[key] = StoredDocument(
            doc_id=prepared.doc_id,
            content_hash=prepared.content_hash,
            version=prepared.doc_version,
        )
        self.prepared[prepared.doc_id] = prepared

    async def record_chunk_vectors(
        self, tenant_id: str, entries: Sequence[ChunkVectorEntry]
    ) -> None:
        for entry in entries:
            self.chunk_vectors[entry.chunk_id] = entry

    async def list_external_ids(self, tenant_id: str, source_id: str) -> set[str]:
        return {
            prepared.external_id
            for prepared in self.prepared.values()
            if prepared.tenant_id == tenant_id and prepared.source_id == source_id
        }

    async def list_chunk_ids(self, tenant_id: str, doc_id: str) -> list[str]:
        prepared = self.prepared.get(doc_id)
        if prepared is None:
            return []
        return [chunk.chunk_id for chunk in prepared.chunks]

    async def delete_document(self, tenant_id: str, doc_id: str) -> None:
        self.prepared.pop(doc_id, None)
        for key, document in list(self.documents.items()):
            if document.doc_id == doc_id:
                self.documents.pop(key, None)
        for chunk_id, entry in list(self.chunk_vectors.items()):
            if entry.chunk_id == chunk_id and chunk_id not in {
                chunk.chunk_id for prepared in self.prepared.values() for chunk in prepared.chunks
            }:
                self.chunk_vectors.pop(chunk_id, None)
