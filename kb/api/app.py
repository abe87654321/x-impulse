"""最小 ASGI HTTP 层 —— /health 与 /v1/ask。

身份：只接受显式 `x-principal-id`。缺失即 401，不做匿名降级（I-10）。
真实部署必须在网关完成 IdP/令牌校验；本层不自行解析或放宽认证。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from kb.api.chat_service import ChatService
from kb.identity.permissions import FULL_SCOPE, DelegationContext, Principal

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


def create_app(service: ChatService) -> ASGIApp:
    async def app(scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        if method == "GET" and path == "/health":
            await _json(send, 200, {"status": "ok"})
            return

        if method == "POST" and path == "/v1/ask":
            await _handle_ask(service, scope, receive, send)
            return

        await _json(send, 404, {"error": "not_found"})

    return app


async def _handle_ask(
    service: ChatService,
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    principal_id = headers.get("x-principal-id")
    if not principal_id:
        await _json(send, 401, {"error": "missing_principal"})
        return

    body = await _read_body(receive)
    try:
        payload = json.loads(body or b"{}")
        tenant_id = str(payload["tenant_id"])
        question = str(payload["question"])
    except (KeyError, TypeError, ValueError):
        await _json(send, 400, {"error": "invalid_request"})
        return

    channel = headers.get("x-channel", "api")
    session_id = headers.get("x-session-id")
    now = datetime.now(UTC)
    ctx = DelegationContext(
        principal=Principal(type="user", id=principal_id),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=now + timedelta(minutes=5),
        evidence={"channel": channel, "auth": "gateway"},
    )
    try:
        result = await service.ask(ctx, tenant_id, question, session_id=session_id, channel=channel)
    except Exception:  # noqa: BLE001
        await _json(send, 500, {"error": "internal_error"})
        return

    answer = result.answer
    await _json(
        send,
        200,
        {
            "session_id": result.session_id,
            "query_id": result.query_id,
            "answer_id": result.answer_id,
            "text": answer.text,
            "refused": answer.refused,
            "refusal_reason": answer.refusal_reason,
            "citations": [asdict(citation) for citation in answer.citations],
        },
    )


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
    return b"".join(chunks)


async def _json(send: Send, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = ["ASGIApp", "create_app"]
