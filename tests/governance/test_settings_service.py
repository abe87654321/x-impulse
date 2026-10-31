"""管理面设置：角色授权、配置校验、审计与审批接缝。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kb.audit.writer import AuditEvent
from kb.governance.settings_dag import SettingsError
from kb.governance.settings_service import (
    ROLE_EMPLOYEE,
    ROLE_IMPLEMENTATION_CONSULTANT,
    ROLE_SECURITY_AUDITOR,
    ROLE_SYSTEM_ADMIN,
    ApprovalRequired,
    BackupSettingsService,
    SettingsWriteDenied,
    build_settings_service_from_env,
    consultant_write_until_from_env,
)
from kb.governance.settings_store import InMemorySettingsStore


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, now=None) -> str:
        self.events.append(event)
        return "1"


class _RequireApproval:
    async def check(self, tenant_id, changes, *, approved_by):
        if approved_by is None:
            raise ApprovalRequired("需要审批人")


def _service(audit=None, approval=None) -> BackupSettingsService:
    return BackupSettingsService(InMemorySettingsStore(), audit=audit, approval=approval)


async def test_snapshot_uses_recommended_defaults():
    service = _service()

    resolved = await service.snapshot("t1")

    assert resolved.ready is True
    assert resolved.values["rotation_days"] == 90
    assert resolved.sources["rotation_days"] == "recommended"


async def test_employee_cannot_write_settings():
    service = _service()

    with pytest.raises(SettingsWriteDenied):
        await service.update([ROLE_EMPLOYEE], "t1", {"rotation_days": 30}, updated_by="alice")


async def test_system_admin_can_write_valid_settings():
    store = InMemorySettingsStore()
    service = BackupSettingsService(store)

    result = await service.update(
        [ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 30}, updated_by="admin"
    )

    assert result.settings.values["rotation_days"] == 30
    assert result.settings.sources["rotation_days"] == "admin"
    assert (await store.load("t1")) == {"rotation_days": 30}


async def test_invalid_combination_is_rejected_and_not_stored():
    store = InMemorySettingsStore()
    service = BackupSettingsService(store)

    with pytest.raises(SettingsError):
        await service.update(
            [ROLE_SYSTEM_ADMIN],
            "t1",
            {"authority_store": "db_only"},
            updated_by="admin",
        )

    assert await store.load("t1") == {}


async def test_unknown_setting_is_rejected():
    service = _service()

    with pytest.raises(SettingsError):
        await service.update([ROLE_SYSTEM_ADMIN], "t1", {"nope": 1}, updated_by="admin")


async def test_changes_are_audited():
    audit = _Audit()
    service = _service(audit=audit)

    await service.update([ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 45}, updated_by="admin")

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.category == "admin"
    assert event.action == "backup_settings_updated"
    assert event.payload == {"changes": {"rotation_days": {"old": None, "new": 45}}}


async def test_noop_update_is_not_audited():
    audit = _Audit()
    service = _service(audit=audit)
    await service.update([ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 45}, updated_by="admin")
    audit.events.clear()

    result = await service.update(
        [ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 45}, updated_by="admin"
    )

    assert result.changed == {}
    assert audit.events == []


async def test_approval_hook_is_enforced_when_configured():
    service = _service(approval=_RequireApproval())

    with pytest.raises(ApprovalRequired):
        await service.update([ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 30}, updated_by="admin")
    result = await service.update(
        [ROLE_SYSTEM_ADMIN],
        "t1",
        {"rotation_days": 30},
        updated_by="admin",
        approved_by="reviewer",
    )
    assert result.settings.values["rotation_days"] == 30


async def test_reader_roles():
    service = _service()

    with pytest.raises(SettingsWriteDenied):
        await service.snapshot_for([ROLE_EMPLOYEE], "t1")
    resolved = await service.snapshot_for([ROLE_SECURITY_AUDITOR], "t1")
    assert resolved.ready is True


async def test_empty_update_is_rejected():
    service = _service()

    with pytest.raises(ValueError):
        await service.update([ROLE_SYSTEM_ADMIN], "t1", {}, updated_by="admin")


def _clock_at(moment: datetime):
    return lambda: moment


async def test_consultant_can_write_inside_poc_window():
    deadline = datetime(2026, 12, 31, tzinfo=UTC)
    store = InMemorySettingsStore()
    service = BackupSettingsService(
        store,
        consultant_write_until=deadline,
        clock=_clock_at(deadline - timedelta(days=1)),
    )

    result = await service.update(
        [ROLE_IMPLEMENTATION_CONSULTANT],
        "t1",
        {"rotation_days": 30},
        updated_by="consultant",
    )

    assert result.settings.values["rotation_days"] == 30
    assert service.consultant_can_write() is True


async def test_consultant_becomes_read_only_at_deadline():
    deadline = datetime(2026, 12, 31, tzinfo=UTC)
    service = BackupSettingsService(
        InMemorySettingsStore(),
        consultant_write_until=deadline,
        clock=_clock_at(deadline),
    )

    assert service.consultant_read_only() is True
    with pytest.raises(SettingsWriteDenied):
        await service.update(
            [ROLE_IMPLEMENTATION_CONSULTANT],
            "t1",
            {"rotation_days": 30},
            updated_by="consultant",
        )
    resolved = await service.snapshot_for([ROLE_IMPLEMENTATION_CONSULTANT], "t1")
    assert resolved.ready is True


async def test_consultant_is_read_only_without_explicit_window():
    service = _service()

    assert service.consultant_read_only() is True
    with pytest.raises(SettingsWriteDenied):
        await service.update(
            [ROLE_IMPLEMENTATION_CONSULTANT],
            "t1",
            {"rotation_days": 30},
            updated_by="consultant",
        )


async def test_system_admin_unaffected_by_consultant_expiry():
    deadline = datetime(2026, 12, 31, tzinfo=UTC)
    service = BackupSettingsService(
        InMemorySettingsStore(),
        consultant_write_until=deadline,
        clock=_clock_at(deadline + timedelta(days=1)),
    )

    result = await service.update(
        [ROLE_SYSTEM_ADMIN], "t1", {"rotation_days": 30}, updated_by="admin"
    )

    assert result.settings.values["rotation_days"] == 30


def test_consultant_window_env_parsing():
    assert consultant_write_until_from_env({}) is None
    parsed = consultant_write_until_from_env({"KB_CONSULTANT_WRITE_UNTIL": "2026-12-31T00:00:00Z"})
    assert parsed == datetime(2026, 12, 31, tzinfo=UTC)
    naive = consultant_write_until_from_env({"KB_CONSULTANT_WRITE_UNTIL": "2026-12-31T00:00:00"})
    assert naive == datetime(2026, 12, 31, tzinfo=UTC)
    with pytest.raises(ValueError):
        consultant_write_until_from_env({"KB_CONSULTANT_WRITE_UNTIL": "not-a-date"})


def test_factory_wires_consultant_window_from_env():
    service = build_settings_service_from_env(
        InMemorySettingsStore(),
        environ={"KB_CONSULTANT_WRITE_UNTIL": "2026-12-31T00:00:00Z"},
        clock=_clock_at(datetime(2026, 6, 1, tzinfo=UTC)),
    )

    assert service.consultant_can_write() is True
