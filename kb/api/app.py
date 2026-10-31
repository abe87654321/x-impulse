"""最小 ASGI HTTP 层 —— /health、/v1/ask 与 /v1/ask/stream。

身份：只接受显式 `x-principal-id`。缺失即 401，不做匿名降级（I-10）。
真实部署必须在网关完成 IdP/令牌校验；本层不自行解析或放宽认证。

限流触发时返回 429 + `Retry-After`，不静默丢弃（FR-16-03）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from kb.api.chat_service import ChatService, ChatStreamEvent
from kb.governance.rate_limit import RateLimited
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

        if method == "POST" and path == "/v1/ask/stream":
            await _handle_ask_stream(service, scope, receive, send)
            return

        await _json(send, 404, {"error": "not_found"})

    return app


async def _handle_ask(
    service: ChatService,
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    request = await _read_request(receive, scope)
    if request is None:
        await _json(send, 400, {"error": "invalid_request"})
        return
    principal_id, tenant_id, question, channel, session_id = request
    if not principal_id:
        await _json(send, 401, {"error": "missing_principal"})
        return

    ctx = _context(principal_id, channel)
    try:
        result = await service.ask(ctx, tenant_id, question, session_id=session_id, channel=channel)
    except RateLimited as exc:
        await _rate_limited(send, exc)
        return
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


async def _handle_ask_stream(
    service: ChatService,
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    request = await _read_request(receive, scope)
    if request is None:
        await _json(send, 400, {"error": "invalid_request"})
        return
    principal_id, tenant_id, question, channel, session_id = request
    if not principal_id:
        await _json(send, 401, {"error": "missing_principal"})
        return

    ctx = _context(principal_id, channel)
    stream = service.ask_stream(ctx, tenant_id, question, session_id=session_id, channel=channel)

    # 先取第一个事件：限流/建查询失败时还能返回普通 JSON 错误，而不是半个 SSE。
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        await _json(send, 500, {"error": "empty_stream"})
        return
    except RateLimited as exc:
        await _rate_limited(send, exc)
        return
    except Exception:  # noqa: BLE001
        await _json(send, 500, {"error": "internal_error"})
        return

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache"),
            ],
        }
    )
    await _sse(send, first)
    try:
        async for event in stream:
            await _sse(send, event)
    except Exception:  # noqa: BLE001
        await _sse_event(send, "error", {"error": "internal_error"})
    finally:
        await stream.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _context(principal_id: str, channel: str) -> DelegationContext:
    now = datetime.now(UTC)
    return DelegationContext(
        principal=Principal(type="user", id=principal_id),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=now + timedelta(minutes=5),
        evidence={"channel": channel, "auth": "gateway"},
    )


async def _read_request(
    receive: Receive, scope: dict[str, Any]
) -> tuple[str | None, str, str, str, str | None] | None:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    body = await _read_body(receive)
    try:
        payload = json.loads(body or b"{}")
        tenant_id = str(payload["tenant_id"])
        question = str(payload["question"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        headers.get("x-principal-id"),
        tenant_id,
        question,
        headers.get("x-channel", "api"),
        headers.get("x-session-id"),
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


async def _rate_limited(send: Send, exc: RateLimited) -> None:
    decision = exc.decision
    await _json(
        send,
        429,
        {
            "error": "rate_limited",
            "reason": decision.reason,
            "retry_after_seconds": decision.retry_after_seconds,
            "message": "请求过于频繁，请稍后重试。",
        },
        headers=[(b"retry-after", str(decision.retry_after_seconds).encode("latin-1"))],
    )


async def _sse(send: Send, event: ChatStreamEvent) -> None:
    if event.kind == "sentence":
        await _sse_event(
            send,
            "sentence",
            {
                "sentence_index": event.sentence_index,
                "text": event.text,
                "citations": [asdict(citation) for citation in event.citations],
            },
        )
    elif event.kind == "refusal":
        await _sse_event(send, "refusal", {"text": event.text})
    elif event.kind == "done":
        await _sse_event(
            send,
            "done",
            {
                "session_id": event.session_id,
                "query_id": event.query_id,
                "answer_id": event.answer_id,
                "refused": event.refused,
                "citations": [asdict(citation) for citation in event.citations],
            },
        )
    else:
        await _sse_event(send, "error", {"error": event.error or "internal_error"})


async def _sse_event(send: Send, event: str, payload: dict[str, object]) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    body = f"event: {event}\ndata: {data}\n\n".encode()
    await send({"type": "http.response.body", "body": body, "more_body": True})


async def _json(
    send: Send,
    status: int,
    payload: dict[str, object],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response_headers = [(b"content-type", b"application/json; charset=utf-8")]
    if headers:
        response_headers.extend(headers)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = ["ASGIApp", "create_app"]
