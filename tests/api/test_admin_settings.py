"""管理面备份设置 API：GUI 契约与 Agent 提案边界。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kb.api.admin_settings import create_admin_settings_app
from kb.governance.settings_service import BackupSettingsService
from kb.governance.settings_store import InMemorySettingsStore


async def _call(app, method, path, *, headers=None, body=None):
    sent: list[dict] = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ],
    }
    raw = b"" if body is None else json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = sent[0]["status"]
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload)


def _app() -> tuple[object, InMemorySettingsStore]:
    store = InMemorySettingsStore()
    return create_admin_settings_app(BackupSettingsService(store)), store


def _headers(**extra: str) -> dict[str, str]:
    base = {
        "x-principal-id": "admin",
        "x-roles": "system_admin",
        "x-tenant-id": "t1",
    }
    base.update(extra)
    return base


async def test_schema_exposes_recommended_values():
    app, _ = _app()
    status, payload = await _call(app, "GET", "/admin/settings/backup/schema", headers=_headers())

    assert status == 200
    settings = {item["key"]: item for item in payload["settings"]}
    assert settings["rotation_days"]["recommended"] == 90
    assert "backup_system" in settings


async def test_snapshot_marks_recommended_sources():
    app, _ = _app()
    status, payload = await _call(app, "GET", "/admin/settings/backup", headers=_headers())

    assert status == 200
    assert payload["ready"] is True
    assert payload["sources"]["rotation_days"] == "recommended"


async def test_admin_can_update_settings():
    app, store = _app()
    status, payload = await _call(
        app,
        "PUT",
        "/admin/settings/backup",
        headers=_headers(),
        body={"updates": {"rotation_days": 30}},
    )

    assert status == 200
    assert payload["values"]["rotation_days"] == 30
    assert await store.load("t1") == {"rotation_days": 30}


async def test_employee_is_forbidden():
    app, _ = _app()
    status, payload = await _call(
        app,
        "PUT",
        "/admin/settings/backup",
        headers=_headers(**{"x-roles": "employee"}),
        body={"updates": {"rotation_days": 30}},
    )

    assert status == 403
    assert payload["error"] == "forbidden"


async def test_agent_cannot_execute_management_action():
    app, store = _app()
    status, payload = await _call(
        app,
        "PUT",
        "/admin/settings/backup",
        headers=_headers(**{"x-actor-type": "agent"}),
        body={"updates": {"rotation_days": 30}},
    )

    assert status == 403
    assert payload["error"] == "agent_execution_forbidden"
    assert await store.load("t1") == {}


async def test_agent_can_submit_proposal_without_writing():
    app, store = _app()
    status, payload = await _call(
        app,
        "POST",
        "/admin/settings/backup/proposals",
        headers=_headers(**{"x-actor-type": "agent"}),
        body={"updates": {"rotation_days": 30}},
    )

    assert status == 200
    assert payload["executed"] is False
    assert payload["values"]["rotation_days"] == 30
    assert await store.load("t1") == {}


async def test_invalid_combination_is_rejected():
    app, _ = _app()
    status, payload = await _call(
        app,
        "PUT",
        "/admin/settings/backup",
        headers=_headers(),
        body={"updates": {"authority_store": "db_only"}},
    )

    assert status == 400
    assert payload["error"] == "invalid_settings"


async def test_missing_principal_is_401():
    app, _ = _app()
    status, payload = await _call(
        app,
        "GET",
        "/admin/settings/backup",
        headers={"x-roles": "system_admin", "x-tenant-id": "t1"},
    )

    assert status == 401
    assert payload["error"] == "missing_principal"


async def test_consultant_is_forbidden_after_poc_window():
    store = InMemorySettingsStore()
    service = BackupSettingsService(
        store,
        consultant_write_until=datetime(2026, 1, 1, tzinfo=UTC),
        clock=lambda: datetime(2026, 6, 1, tzinfo=UTC),
    )
    app = create_admin_settings_app(service)

    status, payload = await _call(
        app,
        "PUT",
        "/admin/settings/backup",
        headers=_headers(**{"x-roles": "implementation_consultant"}),
        body={"updates": {"rotation_days": 30}},
    )

    assert status == 403
    assert payload["error"] == "forbidden"
    assert await store.load("t1") == {}
