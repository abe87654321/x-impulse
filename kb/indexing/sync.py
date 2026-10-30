"""源同步编排 —— 全量 / 增量 / 删除传播 + 游标与 sync_runs。

O-6：只有整批成功后才推进 cursor；失败保留旧 cursor，下轮重放。
I-9：本编排不 sleep、不自行重试；失败交给调度器。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env
from kb.indexing.deletion import DocumentDeletionService
from kb.indexing.ids import document_id
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import DocumentStore
from kb.ingestion.contract import (
    Connector,
    Cursor,
    DeletionDetection,
    Scope,
)

_LATEST_CURSOR_SQL = """
SELECT cursor_after FROM sync_runs
WHERE tenant_id = $1 AND source_id = $2
  AND status = 'succeeded' AND cursor_after IS NOT NULL
ORDER BY started_at DESC
LIMIT 1
"""

_INSERT_RUN_SQL = """
INSERT INTO sync_runs
    (run_id, tenant_id, source_id, mode, cursor_before, cursor_after,
     docs_added, docs_updated, docs_deleted, docs_suppressed, docs_failed,
     finished_at, status, error)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now(), $12, $13)
"""


@dataclass
class SyncStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    suppressed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncResult:
    mode: str
    status: str
    cursor_before: str | None
    cursor_after: str | None
    stats: SyncStats
    error: str | None = None


class SyncRunRepository(Protocol):
    async def latest_cursor(self, tenant_id: str, source_id: str) -> str | None: ...

    async def record(self, tenant_id: str, source_id: str, result: SyncResult) -> None: ...


class PostgresSyncRunRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def latest_cursor(self, tenant_id: str, source_id: str) -> str | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_LATEST_CURSOR_SQL, tenant_id, source_id)
        finally:
            await conn.close()
        return None if row is None else str(row["cursor_after"])

    async def record(self, tenant_id: str, source_id: str, result: SyncResult) -> None:
        stats = result.stats
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                _INSERT_RUN_SQL,
                f"run_{uuid.uuid4().hex}",
                tenant_id,
                source_id,
                result.mode,
                result.cursor_before,
                result.cursor_after,
                stats.added,
                stats.updated,
                stats.deleted,
                stats.suppressed,
                stats.failed,
                result.status,
                result.error,
            )
        finally:
            await conn.close()


class SourceSyncService:
    def __init__(
        self,
        connector: Connector,
        pipeline: IndexingPipeline,
        deletion: DocumentDeletionService,
        store: DocumentStore,
        runs: SyncRunRepository,
        *,
        tenant_id: str,
        source_id: str,
    ) -> None:
        self._connector = connector
        self._pipeline = pipeline
        self._deletion = deletion
        self._store = store
        self._runs = runs
        self._tenant_id = tenant_id
        self._source_id = source_id

    async def run_full(self, scope: Scope | None = None) -> SyncResult:
        cursor_before = await self._runs.latest_cursor(self._tenant_id, self._source_id)
        try:
            stats = await self._pipeline.run(scope)
            result = SyncResult(
                mode="full",
                status="succeeded",
                cursor_before=cursor_before,
                cursor_after=self._connector.checkpoint().token,
                stats=SyncStats(
                    scanned=stats.scanned,
                    added=stats.indexed,
                    deleted=0,
                    suppressed=stats.suppressed,
                    failed=stats.failed,
                    errors=list(stats.errors),
                ),
            )
        except Exception as exc:
            result = self._failed("full", cursor_before, exc)
            await self._runs.record(self._tenant_id, self._source_id, result)
            raise

        await self._runs.record(self._tenant_id, self._source_id, result)
        return result

    async def run_incremental(self, scope: Scope | None = None) -> SyncResult:
        cursor_before = await self._runs.latest_cursor(self._tenant_id, self._source_id)
        cursor = (
            Cursor(
                source_id=self._source_id,
                token=cursor_before,
                issued_at=datetime.now(UTC),
            )
            if cursor_before is not None
            else None
        )
        stats = SyncStats()
        try:
            async for event in self._connector.incremental_scan(scope or Scope(), cursor):
                stats.scanned += 1
                outcome = await self._pipeline.index_ref(event.ref)
                if outcome.status == "indexed":
                    if outcome.created:
                        stats.added += 1
                    else:
                        stats.updated += 1
                elif outcome.status == "suppressed":
                    stats.suppressed += 1
                elif outcome.status == "failed":
                    stats.failed += 1
                    if outcome.error:
                        stats.errors.append(outcome.error)

            if self._connector.capabilities().deletion_detection != DeletionDetection.NONE:
                known = await self._store.list_external_ids(self._tenant_id, self._source_id)
                async for deletion in self._connector.detect_deletions(scope or Scope(), known):
                    await self._deletion.delete_document(
                        self._tenant_id,
                        document_id(self._source_id, deletion.external_id),
                    )
                    stats.deleted += 1

            result = SyncResult(
                mode="incremental",
                status="succeeded",
                cursor_before=cursor_before,
                cursor_after=self._connector.checkpoint().token,
                stats=stats,
            )
        except Exception as exc:
            result = self._failed("incremental", cursor_before, exc)
            await self._runs.record(self._tenant_id, self._source_id, result)
            raise

        await self._runs.record(self._tenant_id, self._source_id, result)
        return result

    def _failed(self, mode: str, cursor_before: str | None, exc: Exception) -> SyncResult:
        return SyncResult(
            mode=mode,
            status="failed",
            cursor_before=cursor_before,
            cursor_after=None,
            stats=SyncStats(),
            error=f"{type(exc).__name__}: {exc}",
        )


__all__ = [
    "PostgresSyncRunRepository",
    "SourceSyncService",
    "SyncResult",
    "SyncRunRepository",
    "SyncStats",
]
