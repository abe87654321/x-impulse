"""恢复演练闸门 —— 删除设计 §6.3 / §7 / NFR-A-5。

恢复备份后，业务库可能回到删除之前的状态。允许对外服务前必须用
**权威外部状态**重新比对：

1. 文档抑制键命中 → 该文档不得重新进入索引。
2. 主体抑制键命中 → 含该主体的 chunk 不得进入检索。
3. KMS 侧密钥已销毁 → 即使恢复出的 `subject_keys` 行看起来仍有效，
   也必须视为不可用（这是备份回滚无法撤销的删除证据）。

本模块只做扫描与阻断，不直接删除数据；删除/隔离由调用方在隔离环境执行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from kb.compliance.pii import detect_pii
from kb.database import Database
from kb.indexing.suppression import SuppressionChecker, document_scope_key

FindingKind = Literal["document", "subject", "destroyed_key"]


@dataclass(frozen=True)
class DocumentSnapshot:
    doc_id: str
    source_id: str
    external_id: str


@dataclass(frozen=True)
class ChunkSnapshot:
    chunk_id: str
    doc_id: str
    content: str


@dataclass(frozen=True)
class RestoreFinding:
    kind: FindingKind
    object_id: str
    detail: str


@dataclass(frozen=True)
class RestoreReport:
    documents_scanned: int
    chunks_scanned: int
    findings: tuple[RestoreFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


class RestoreViolation(Exception):
    def __init__(self, report: RestoreReport) -> None:
        self.report = report
        super().__init__(f"恢复校验未通过：{len(report.findings)} 项")


class RestoreCatalog(Protocol):
    async def documents(self, tenant_id: str) -> Sequence[DocumentSnapshot]: ...

    async def chunks(self, tenant_id: str) -> Sequence[ChunkSnapshot]: ...


class KeyStateAuthority(Protocol):
    async def is_destroyed(self, tenant_id: str, subject_key: str) -> bool: ...


class RestoreGuard:
    def __init__(
        self,
        catalog: RestoreCatalog,
        suppression: SuppressionChecker,
        *,
        tenant_id: str,
        subject_hasher=None,
        key_authority: KeyStateAuthority | None = None,
    ) -> None:
        if key_authority is not None and subject_hasher is None:
            raise ValueError("启用密钥销毁校验时必须提供 subject_hasher")
        self._catalog = catalog
        self._suppression = suppression
        self._tenant_id = tenant_id
        self._subject_hasher = subject_hasher
        self._key_authority = key_authority

    async def scan(self) -> RestoreReport:
        findings: list[RestoreFinding] = []
        documents = await self._catalog.documents(self._tenant_id)
        for document in documents:
            suppressed = await self._suppression.is_suppressed(
                self._tenant_id,
                "document",
                document_scope_key(document.source_id, document.external_id),
            )
            if suppressed:
                findings.append(
                    RestoreFinding(
                        "document",
                        document.doc_id,
                        "文档抑制键命中，不得重新入库或索引",
                    )
                )

        chunks = await self._catalog.chunks(self._tenant_id)
        seen: set[tuple[str, str, str]] = {(f.kind, f.object_id, f.detail) for f in findings}
        for chunk in chunks:
            for detected in detect_pii(chunk.content):
                suppressed = await self._suppression.is_suppressed(
                    self._tenant_id, "subject", detected.normalized
                )
                if suppressed:
                    _add(
                        findings,
                        seen,
                        RestoreFinding(
                            "subject",
                            chunk.chunk_id,
                            f"主体抑制键命中（{detected.subject_type}）",
                        ),
                    )
                if self._key_authority is not None and self._subject_hasher is not None:
                    key = self._subject_hasher(detected.normalized)
                    if await self._key_authority.is_destroyed(self._tenant_id, key):
                        _add(
                            findings,
                            seen,
                            RestoreFinding(
                                "destroyed_key",
                                chunk.chunk_id,
                                f"KMS 密钥已销毁（{detected.subject_type}）",
                            ),
                        )

        return RestoreReport(
            documents_scanned=len(documents),
            chunks_scanned=len(chunks),
            findings=tuple(findings),
        )

    async def verify(self) -> RestoreReport:
        """恢复后的强制闸门：有任一命中即拒绝放行。"""
        report = await self.scan()
        if not report.passed:
            raise RestoreViolation(report)
        return report


def _add(
    findings: list[RestoreFinding],
    seen: set[tuple[str, str, str]],
    finding: RestoreFinding,
) -> None:
    key = (finding.kind, finding.object_id, finding.detail)
    if key in seen:
        return
    seen.add(key)
    findings.append(finding)


class PostgresRestoreCatalog:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or Database()

    async def documents(self, tenant_id: str) -> Sequence[DocumentSnapshot]:
        rows = await asyncio.to_thread(
            self._db.fetch,
            "SELECT doc_id, source_id, external_id FROM documents "
            "WHERE tenant_id = $1 AND status = 'indexed' ORDER BY doc_id",
            tenant_id,
        )
        return [
            DocumentSnapshot(
                doc_id=str(row["doc_id"]),
                source_id=str(row["source_id"]),
                external_id=str(row["external_id"]),
            )
            for row in rows
        ]

    async def chunks(self, tenant_id: str) -> Sequence[ChunkSnapshot]:
        rows = await asyncio.to_thread(
            self._db.fetch,
            "SELECT chunk_id, doc_id, content FROM chunks WHERE tenant_id = $1 ORDER BY chunk_id",
            tenant_id,
        )
        return [
            ChunkSnapshot(
                chunk_id=str(row["chunk_id"]),
                doc_id=str(row["doc_id"]),
                content=str(row["content"]),
            )
            for row in rows
        ]


__all__ = [
    "ChunkSnapshot",
    "DocumentSnapshot",
    "KeyStateAuthority",
    "PostgresRestoreCatalog",
    "RestoreCatalog",
    "RestoreFinding",
    "RestoreGuard",
    "RestoreReport",
    "RestoreViolation",
]
