"""备份 manifest 工具：生成、追加、校验与可被目录适配器消费。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from kb.backup_manifest import (
    append_generation,
    init_manifest,
    main,
    validate_manifest,
)
from kb.compliance.backup_adapters import (
    FileManifestSource,
    ManifestBackupCatalog,
)

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_init_writes_valid_consumable_manifest(tmp_path):
    path = tmp_path / "manifest.jsonl"

    rows = init_manifest(path, tenant_id="default", count=3, rotation_days=90, now=NOW)

    assert len(rows) == 3
    assert validate_manifest(path) == []
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["tenant_id"] == "default"


async def test_generated_manifest_is_read_by_catalog(tmp_path):
    path = tmp_path / "manifest.jsonl"
    init_manifest(path, tenant_id="t1", count=3, rotation_days=90, now=NOW)
    append_generation(
        path,
        tenant_id="t1",
        rotation_days=90,
        backup_id="shared-1",
        created_at=NOW,
        scope="shared",
    )
    catalog = ManifestBackupCatalog(FileManifestSource(str(path)))

    generations = await catalog.list_generations("t1", now=NOW)

    assert len(generations) == 4
    assert any(generation.backup_id == "shared-1" for generation in generations)


def test_append_adds_one_generation(tmp_path):
    path = tmp_path / "manifest.jsonl"
    init_manifest(path, tenant_id="t1", count=1, rotation_days=30, now=NOW)

    row = append_generation(
        path,
        tenant_id="t1",
        rotation_days=30,
        backup_id="manual-1",
        created_at=NOW,
    )

    assert row["expires_at"] == (NOW + timedelta(days=30)).isoformat()
    assert validate_manifest(path) == []
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_validate_reports_missing_fields(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text('{"backup_id":"x","scope":"tenant"}\n', encoding="utf-8")

    errors = validate_manifest(path)

    assert any("created_at" in error for error in errors)
    assert any("tenant_id" in error for error in errors)


def test_validate_reports_bad_json(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text("not-json\n", encoding="utf-8")

    errors = validate_manifest(path)

    assert any("不是合法 JSON" in error for error in errors)


def test_tenant_scope_requires_tenant_id(tmp_path):
    with pytest.raises(ValueError):
        append_generation(
            tmp_path / "manifest.jsonl",
            tenant_id="",
            rotation_days=30,
            scope="tenant",
        )


def test_cli_init_append_validate(tmp_path, capsys):
    path = tmp_path / "manifest.jsonl"

    assert main(["init", "--path", str(path), "--tenant", "default", "--count", "2"]) == 0
    assert main(["append", "--path", str(path), "--tenant", "default", "--backup-id", "b3"]) == 0
    assert main(["validate", "--path", str(path)]) == 0

    output = capsys.readouterr().out
    assert "已写入 2 条" in output
    assert "已追加" in output
    assert "manifest 合法" in output
