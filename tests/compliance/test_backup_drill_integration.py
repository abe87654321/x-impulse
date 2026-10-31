"""自检脚本：轮转 + 恢复演练端到端必须可跑且清理干净。"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from kb.backup_drill import run
from kb.database import Database

pytestmark = pytest.mark.integration

TENANT = "drill_restore_it"


def test_drill_runs_end_to_end_and_cleans_up(tmp_path, capsys):
    manifest = tmp_path / "manifest.jsonl"
    args = argparse.Namespace(
        manifest=str(manifest),
        tenant=TENANT,
        dsn=None,
        rotation_days=90,
    )

    code = asyncio.run(run(args))

    assert code == 0
    output = capsys.readouterr().out
    assert "恢复被阻断（符合预期）" in output
    assert "[document]" in output
    assert "[subject]" in output
    assert "[destroyed_key]" in output
    assert manifest.exists()
    assert Database().fetch("SELECT 1 FROM tenants WHERE tenant_id = $1", TENANT) == []
