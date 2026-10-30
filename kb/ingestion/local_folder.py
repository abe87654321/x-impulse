"""本地文件夹连接器 —— 首个连接器实现（决策 A-12）。

能力边界（POSIX 语义，见 TD-02 §8）：

- ① 全量拉取  ✅
- ② 增量识别  ✅（TIMESTAMP）
- ③ 删除检测  ✅（ENUMERATE）
- ④ ACL 映射  ✅ 仅 POSIX/NTFS；无显式 Deny、无断开继承
- ⑤ 内容抽取  ✅
- ⑥ 断点续传  ✅

本实现不自行重试：TransientError 直接上抛，由调度器负责（I-9）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kb.ingestion.contract import (
    AclSet,
    Capabilities,
    ChangeEvent,
    Connector,
    Cursor,
    DeletionDetection,
    DeletionEvent,
    DocumentRef,
    IncrementalMode,
    NotFoundError,
    PermanentError,
    RawDocument,
    Scope,
)


@dataclass(frozen=True)
class FolderEntry:
    """文件系统里的一条记录。连接器只读它，不解释其存储方式。"""

    external_id: str
    name: str
    container_id: str | None
    version: str
    updated_at: datetime
    size_bytes: int
    mime_type: str
    content: bytes
    acl: AclSet


class FolderSource(Protocol):
    """连接器依赖的最小源接口。测试替身与真实文件系统都实现它。"""

    @property
    def source_id(self) -> str: ...

    def now(self) -> datetime: ...

    def entries(self) -> tuple[FolderEntry, ...]: ...


class LocalFolderConnector(Connector):
    _SUPPORTED_VERSIONS = frozenset({1})

    def __init__(self, source: FolderSource) -> None:
        self._source = source
        self._resume_token: str | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            incremental=IncrementalMode.TIMESTAMP,
            deletion_detection=DeletionDetection.ENUMERATE,
            supports_acl=True,
            supports_acl_deny=False,
            supports_inheritance_break=False,
            supports_webhook=False,
            supports_field_level_acl=False,
            max_page_size=1000,
            rate_limit_qps=None,
            binary_formats=frozenset(
                {"text/plain", "text/markdown", "application/pdf", "application/octet-stream"}
            ),
        )

    # ── ① 全量拉取 ──────────────────────────────────────

    async def full_scan(self, scope: Scope) -> AsyncIterator[DocumentRef]:
        for entry in self._source.entries():
            if self._in_scope(entry, scope):
                yield self._to_ref(entry)

    # ── ② 增量识别 ──────────────────────────────────────

    async def incremental_scan(
        self, scope: Scope, cursor: Cursor | None
    ) -> AsyncIterator[ChangeEvent]:
        since = self._resolve_cursor(cursor)
        for entry in self._source.entries():
            if not self._in_scope(entry, scope):
                continue
            if cursor is None:
                yield ChangeEvent(kind="created", ref=self._to_ref(entry))
                continue
            if since is None or entry.updated_at > since:
                yield ChangeEvent(kind="updated", ref=self._to_ref(entry))

    # ── ③ 删除检测 ──────────────────────────────────────

    async def detect_deletions(
        self, scope: Scope, known_ids: set[str]
    ) -> AsyncIterator[DeletionEvent]:
        current = {
            entry.external_id for entry in self._source.entries() if self._in_scope(entry, scope)
        }
        detected_at = self._source.now()
        for external_id in sorted(known_ids - current):
            yield DeletionEvent(
                external_id=external_id,
                detected_by=DeletionDetection.ENUMERATE,
                detected_at=detected_at,
            )

    # ── ④ ACL 映射 ──────────────────────────────────────

    async def fetch_acl(self, ref: DocumentRef) -> AclSet:
        return self._find(ref).acl

    # ── ⑤ 内容抽取 ──────────────────────────────────────

    async def fetch_content(self, ref: DocumentRef) -> RawDocument:
        entry = self._find(ref)
        return RawDocument(
            ref=self._to_ref(entry),
            content=entry.content,
            mime_type=entry.mime_type,
            metadata={"name": entry.name},
        )

    # ── ⑥ 断点续传 ──────────────────────────────────────

    def checkpoint(self) -> Cursor:
        now = self._source.now()
        return Cursor(
            source_id=self._source.source_id,
            token=now.isoformat(),
            issued_at=now,
        )

    def resume(self, cursor: Cursor) -> None:
        self._validate_cursor(cursor)
        self._resume_token = cursor.token

    # ── 内部 ────────────────────────────────────────────

    def _resolve_cursor(self, cursor: Cursor | None) -> datetime | None:
        if cursor is None:
            return None
        self._validate_cursor(cursor)
        token = self._resume_token if self._resume_token is not None else cursor.token
        return datetime.fromisoformat(token)

    def _validate_cursor(self, cursor: Cursor) -> None:
        if cursor.source_id != self._source.source_id:
            raise PermanentError(
                f"游标属于 source {cursor.source_id!r}，当前连接器为 {self._source.source_id!r}"
            )
        if cursor.version not in self._SUPPORTED_VERSIONS:
            raise PermanentError(f"不支持的游标版本 {cursor.version}")

    def _find(self, ref: DocumentRef) -> FolderEntry:
        for entry in self._source.entries():
            if entry.external_id == ref.external_id:
                return entry
        raise NotFoundError(f"对象不存在：{ref.external_id}")

    @staticmethod
    def _in_scope(entry: FolderEntry, scope: Scope) -> bool:
        container = entry.container_id
        if scope.include and container not in scope.include:
            return False
        if container is not None and container in scope.exclude:
            return False
        return not (scope.since is not None and entry.updated_at < scope.since)

    @staticmethod
    def _to_ref(entry: FolderEntry) -> DocumentRef:
        return DocumentRef(
            external_id=entry.external_id,
            path=entry.name,
            version=entry.version,
            updated_at=entry.updated_at,
            size_bytes=entry.size_bytes,
            container_id=entry.container_id,
        )
