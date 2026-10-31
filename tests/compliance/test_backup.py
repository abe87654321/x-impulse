"""FR-12-04 位置 8：备份轮转接口 —— 登记、到期与诚实声明。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kb.compliance.backup import (
    TrackingBackupRotation,
    UnconfiguredBackupRotation,
    build_backup_rotation_from_env,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


async def test_unconfigured_refuses_to_claim_rotation():
    backup = UnconfiguredBackupRotation()

    outcome = await backup.notify_erasure("t1", scope_type="document", scope_key="k", now=NOW)
    status = await backup.rotation_status("t1", now=NOW)

    assert outcome.status == "SKIPPED"
    assert "未接入" in outcome.detail
    assert status.configured is False
    assert status.rotation_days is None
    assert "无法确认" in backup.client_statement()


async def test_tracking_registers_expiry_and_is_idempotent():
    backup = TrackingBackupRotation(rotation_days=90)

    first = await backup.notify_erasure("t1", scope_type="document", scope_key="k", now=NOW)
    second = await backup.notify_erasure("t1", scope_type="document", scope_key="k", now=NOW)

    assert first.status == "LOGICAL_DONE"
    assert first.expires_at == NOW + timedelta(days=90)
    assert second.expires_at == first.expires_at
    status = await backup.rotation_status("t1", now=NOW)
    assert status.configured is True
    assert status.pending == 1
    assert status.next_expiry_at == first.expires_at


async def test_rotation_expires_only_matured_marks():
    backup = TrackingBackupRotation(rotation_days=30)
    await backup.notify_erasure("t1", scope_type="document", scope_key="old", now=NOW)
    await backup.notify_erasure(
        "t1", scope_type="subject", scope_key="fresh", now=NOW + timedelta(days=20)
    )

    assert await backup.rotate("t1", now=NOW + timedelta(days=10)) == 0
    assert await backup.rotate("t1", now=NOW + timedelta(days=31)) == 1
    status = await backup.rotation_status("t1", now=NOW + timedelta(days=31))
    assert status.pending == 1


async def test_status_counts_only_matching_tenant():
    backup = TrackingBackupRotation(rotation_days=30)
    await backup.notify_erasure("t1", scope_type="document", scope_key="a", now=NOW)
    await backup.notify_erasure("t2", scope_type="document", scope_key="b", now=NOW)

    assert (await backup.rotation_status("t1", now=NOW)).pending == 1
    assert (await backup.rotation_status("t2", now=NOW)).pending == 1


def test_client_statement_states_the_actual_window():
    backup = TrackingBackupRotation(rotation_days=45)

    statement = backup.client_statement()

    assert "45 天" in statement
    assert "不宣称" in statement


def test_factory_requires_explicit_rotation_period():
    assert isinstance(build_backup_rotation_from_env({}), UnconfiguredBackupRotation)


def test_factory_builds_tracking_rotation():
    backup = build_backup_rotation_from_env({"KB_BACKUP_ROTATION_DAYS": "90"})

    assert isinstance(backup, TrackingBackupRotation)
    assert backup.rotation_days == 90


def test_factory_rejects_invalid_period():
    with pytest.raises(ValueError):
        build_backup_rotation_from_env({"KB_BACKUP_ROTATION_DAYS": "0"})
    with pytest.raises(ValueError):
        build_backup_rotation_from_env({"KB_BACKUP_ROTATION_DAYS": "soon"})
