"""端到端演示：本地文件夹 → 同步 → 混合检索 → 生成与引用。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.demo import run_demo

pytestmark = pytest.mark.integration

TENANT = "default"
SOURCE = "src_demo_it"
COLLECTION = "demo_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM sync_runs WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM acl_entries WHERE source_id = $1", SOURCE)
    db.execute(
        """
        DELETE FROM chunk_vectors WHERE chunk_id IN (
            SELECT c.chunk_id FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.source_id = $1
        )
        """,
        SOURCE,
    )
    db.execute(
        "DELETE FROM chunks WHERE doc_id IN (SELECT doc_id FROM documents WHERE source_id = $1)",
        SOURCE,
    )
    db.execute(
        "DELETE FROM annotations WHERE doc_id IN "
        "(SELECT doc_id FROM documents WHERE source_id = $1)",
        SOURCE,
    )
    db.execute("DELETE FROM documents WHERE source_id = $1", SOURCE)
    db.execute("DELETE FROM sources WHERE source_id = $1", SOURCE)


def test_local_folder_to_answer(tmp_path):
    _cleanup()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "报销制度.txt").write_text(
        "量子咖啡机的报销制度：需在三十日内提交，并附发票原件。", encoding="utf-8"
    )
    (docs / "员工手册.md").write_text("员工每年享有十天年假。", encoding="utf-8")
    try:
        answer, sync_result = asyncio.run(
            run_demo(
                docs,
                "量子咖啡机",
                tenant_id=TENANT,
                principal_id="alice",
                source_id=SOURCE,
                collection=COLLECTION,
            )
        )

        assert sync_result.status == "succeeded"
        assert sync_result.stats.scanned == 2
        assert sync_result.stats.added >= 1
        assert answer.refused is False
        assert answer.citations
        assert "量子咖啡机" in answer.text
    finally:
        _cleanup()
