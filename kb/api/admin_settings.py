"""管理面备份设置 API —— 图形界面与 Agent 提案共用同一套契约。

- GET  /admin/settings/backup/schema   设置项描述与推荐值
- GET  /admin/settings/backup          当前值、来源与错误
- PUT  /admin/settings/backup          管理员提交变更（执行）
- POST /admin/settings/backup/proposals Agent 生成提案（不执行，B′ 档）

身份只接受显式 `x-principal-id` 与 `x-roles`；缺失即拒绝，不匿名降级（I-10）。
Agent 不得通过 PUT 执行管理动作（A-13 / NG-4 / NG-7）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from kb.governance.settings_dag import SettingsError
from kb.governance.settings_service import (
    ApprovalRequired,
    BackupSettingsService,
    SettingsWriteDenied,
)

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


def create_admin_settings_app(service: BackupSettingsService) -> ASGIApp:
    async def app(scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        headers = _headers(scope)
        principal = headers.get("x-principal-id")
        if not principal:
            await _json(send, 401, {"error": "missing_principal"})
            return
        roles = [role.strip() for role in headers.get("x-roles", "").split(",") if role.strip()]
        tenant_id = headers.get("x-tenant-id", "")
        if not tenant_id:
            await _json(send, 400, {"error": "missing_tenant"})
            return

        try:
            if method == "GET" and path == "/admin/settings/backup/schema":
                described = await service.describe_for(roles)
                await _json(send, 200, {"settings": described})
                return
            if method == "GET" and path == "/admin/settings/backup":
                await _snapshot(service, roles, tenant_id, send)
                return
            if method == "PUT" and path == "/admin/settings/backup":
                if headers.get("x-actor-type", "human").lower() == "agent":
                    await _json(
                        send,
                        403,
                        {
                            "error": "agent_execution_forbidden",
                            "message": "Agent 只能提交提案，不能直接执行管理动作",
                            "proposal_endpoint": "/admin/settings/backup/proposals",
                        },
                    )
                    return
                body = await _read_body(receive)
                updates = _updates(body)
                result = await service.update(
                    roles,
                    tenant_id,
                    updates,
                    updated_by=principal,
                    approved_by=headers.get("x-approved-by"),
                )
                await _json(
                    send,
                    200,
                    {
                        "values": result.settings.values,
                        "sources": result.settings.sources,
                        "changed": {key: list(change) for key, change in result.changed.items()},
                    },
                )
                return
            if method == "POST" and path == "/admin/settings/backup/proposals":
                body = await _read_body(receive)
                proposed = await service.propose(tenant_id, _updates(body))
                await _json(
                    send,
                    200,
                    {
                        "executed": False,
                        "ready": proposed.ready,
                        "values": proposed.values,
                        "sources": proposed.sources,
                        "errors": proposed.errors,
                    },
                )
                return
            await _json(send, 404, {"error": "not_found"})
        except SettingsWriteDenied as exc:
            await _json(send, 403, {"error": "forbidden", "message": str(exc)})
        except ApprovalRequired as exc:
            await _json(send, 409, {"error": "approval_required", "message": str(exc)})
        except SettingsError as exc:
            await _json(send, 400, {"error": "invalid_settings", "message": str(exc)})
        except (ValueError, json.JSONDecodeError):
            await _json(send, 400, {"error": "invalid_request"})

    return app


async def _snapshot(
    service: BackupSettingsService, roles: list[str], tenant_id: str, send: Send
) -> None:
    resolved = await service.snapshot_for(roles, tenant_id)
    await _json(
        send,
        200,
        {
            "ready": resolved.ready,
            "values": resolved.values,
            "sources": resolved.sources,
            "errors": resolved.errors,
        },
    )


def _headers(scope: Mapping[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


async def _read_body(receive: Receive) -> dict[str, Any]:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
    raw = b"".join(chunks)
    payload = json.loads(raw or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("body must be object")
    return payload


def _updates(body: Mapping[str, Any]) -> dict[str, object]:
    updates = body.get("updates")
    if not isinstance(updates, dict):
        raise ValueError("updates 必须是对象")
    return dict(updates)


async def _json(send: Send, status: int, payload: Mapping[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = ["ASGIApp", "create_admin_settings_app"]
