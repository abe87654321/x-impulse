"""PostgresSyncRunRepository 的游标与运行记录。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.indexing.sync import PostgresSyncRunRepository, SyncResult, SyncStats

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_sync_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM sync_runs WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def test_cursor_persists_only_on_success():
    _cleanup()
    Database().execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', '同步游标集成测试')
        ON CONFLICT (source_id) DO NOTHING
        """,
        SOURCE,
        TENANT,
    )
    try:
        repo = PostgresSyncRunRepository()
        assert asyncio.run(repo.latest_cursor(TENANT, SOURCE)) is None

        ok = SyncResult(
            mode="incremental",
            status="succeeded",
            cursor_before=None,
            cursor_after="tok-ok",
            stats=SyncStats(scanned=1, added=1),
        )
        asyncio.run(repo.record(TENANT, SOURCE, ok))
        assert asyncio.run(repo.latest_cursor(TENANT, SOURCE)) == "tok-ok"

        failed = SyncResult(
            mode="incremental",
            status="failed",
            cursor_before="tok-ok",
            cursor_after=None,
            stats=SyncStats(),
            error="boom",
        )
        asyncio.run(repo.record(TENANT, SOURCE, failed))

        # O-6：失败不得推进 cursor。
        assert asyncio.run(repo.latest_cursor(TENANT, SOURCE)) == "tok-ok"
        rows = Database().fetch(
            "SELECT status, docs_added, error FROM sync_runs "
            "WHERE source_id = $1 ORDER BY started_at",
            SOURCE,
        )
        assert [row["status"] for row in rows] == ["succeeded", "failed"]
        assert rows[0]["docs_added"] == 1
        assert rows[1]["error"] == "boom"
    finally:
        _cleanup()
