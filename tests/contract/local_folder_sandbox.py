"""本地文件夹连接器的测试替身。

同时实现 tests/contract/conftest.py 的 ConnectorSandbox 协议与
kb.ingestion.local_folder.FolderSource —— 前者给契约测试操控，后者给连接器读。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from kb.ingestion.contract import (
    AclEntry,
    AclSet,
    DocumentRef,
    NotFoundError,
    TransientError,
)
from kb.ingestion.local_folder import FolderEntry

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class _CallCounter:
    def __init__(self) -> None:
        self.count = 0


@dataclass
class _Doc:
    external_id: str
    name: str
    container_id: str | None
    content: bytes = b"hello"
    mime_type: str = "text/plain"
    updated_at: datetime = _EPOCH
    version: int = 1
    inheritance_broken: bool = False
    acl_entries: list[AclEntry] = field(default_factory=list)


class LocalFolderSandbox:
    def __init__(self, source_id: str = "local") -> None:
        self._source_id = source_id
        self._docs: dict[str, _Doc] = {}
        self._clock = _EPOCH
        self._seq = 0
        self._counter = _CallCounter()
        self._fail_remaining = 0

    # ── FolderSource ────────────────────────────────────

    @property
    def source_id(self) -> str:
        return self._source_id

    def now(self) -> datetime:
        return self._clock

    def entries(self) -> tuple[FolderEntry, ...]:
        self._counter.count += 1
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise TransientError("503 Service Unavailable")
        return tuple(self._to_entry(doc) for doc in self._docs.values())

    # ── ConnectorSandbox ────────────────────────────────

    async def create_document(
        self, *, name: str = "doc.txt", container: str | None = None
    ) -> DocumentRef:
        doc = self._new_doc(name=name, container=container)
        doc.acl_entries.append(
            AclEntry(
                principal_type="everyone",
                principal_id="*",
                effect="allow",
                scope_level="source",
            )
        )
        return self._to_ref(doc)

    async def create_document_with_broken_inheritance(self) -> DocumentRef:
        doc = self._new_doc(name="broken.txt", container=None)
        doc.inheritance_broken = True
        return self._to_ref(doc)

    async def update_document(self, ref: DocumentRef | str) -> None:
        doc = self._lookup(ref)
        self._tick()
        doc.updated_at = self._clock
        doc.version += 1
        doc.content = b"updated"

    async def rename(self, ref: DocumentRef, new_name: str) -> None:
        doc = self._lookup(ref)
        self._tick()
        doc.name = new_name
        doc.updated_at = self._clock
        doc.version += 1

    async def delete(self, ref: DocumentRef) -> None:
        external_id = ref.external_id if isinstance(ref, DocumentRef) else ref
        self._docs.pop(external_id, None)

    async def grant(
        self,
        ref: DocumentRef,
        *,
        effect: str,
        user: str | None = None,
        group: str | None = None,
    ) -> None:
        doc = self._lookup(ref)
        self._tick()
        if user is not None:
            principal_type, principal_id = "user", user
        elif group is not None:
            principal_type, principal_id = "group", group
        else:
            raise ValueError("grant 需要 user 或 group")
        doc.acl_entries.append(
            AclEntry(
                principal_type=principal_type,  # type: ignore[arg-type]
                principal_id=principal_id,
                effect=effect,  # type: ignore[arg-type]
            )
        )

    def fail_next(self, *, times: int, error: str) -> _CallCounter:
        self._fail_remaining = times
        self._counter = _CallCounter()
        return self._counter

    async def reset(self) -> None:
        self._docs.clear()
        self._clock = _EPOCH
        self._seq = 0
        self._counter = _CallCounter()
        self._fail_remaining = 0

    # ── 内部 ────────────────────────────────────────────

    def _tick(self) -> None:
        self._clock += timedelta(seconds=1)

    def _new_doc(self, *, name: str, container: str | None) -> _Doc:
        self._tick()
        self._seq += 1
        doc = _Doc(
            external_id=f"{self._source_id}:{self._seq}",
            name=name,
            container_id=container,
            updated_at=self._clock,
        )
        self._docs[doc.external_id] = doc
        return doc

    def _lookup(self, ref: DocumentRef | str) -> _Doc:
        external_id = ref.external_id if isinstance(ref, DocumentRef) else ref
        try:
            return self._docs[external_id]
        except KeyError:
            raise NotFoundError(f"对象不存在：{external_id}") from None

    def _to_ref(self, doc: _Doc) -> DocumentRef:
        return DocumentRef(
            external_id=doc.external_id,
            path=doc.name,
            version=str(doc.version),
            updated_at=doc.updated_at,
            size_bytes=len(doc.content),
            container_id=doc.container_id,
        )

    def _to_entry(self, doc: _Doc) -> FolderEntry:
        return FolderEntry(
            external_id=doc.external_id,
            name=doc.name,
            container_id=doc.container_id,
            version=str(doc.version),
            updated_at=doc.updated_at,
            size_bytes=len(doc.content),
            mime_type=doc.mime_type,
            content=doc.content,
            acl=AclSet(
                entries=tuple(doc.acl_entries),
                inherited_from=None,
                inheritance_broken=doc.inheritance_broken,
                fetched_at=self._clock,
            ),
        )
