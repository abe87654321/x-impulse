"""ASGI 层：身份头、路由、请求/响应形状。"""

from __future__ import annotations

import json

from kb.api.app import create_app
from kb.api.chat_service import ChatResult
from kb.generation.citations import Answer


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def ask(self, ctx, tenant_id, question, *, session_id=None, channel="api"):
        self.calls.append((ctx.principal.id if ctx.principal else "", tenant_id, session_id))
        answer = Answer(
            text="知识库中未找到足够依据，无法回答该问题。",
            sentences=(),
            citations=(),
            refused=True,
            refusal_reason="no_evidence",
        )
        return ChatResult(
            session_id=session_id or "sess_new",
            query_id="qry_1",
            answer_id="ans_1",
            answer=answer,
        )


async def _call(app, method, path, *, headers=None, body=b""):
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

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = sent[0]["status"]
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload)


async def test_health():
    status, payload = await _call(create_app(_FakeService()), "GET", "/health")
    assert status == 200
    assert payload == {"status": "ok"}


async def test_missing_principal_is_rejected():
    status, payload = await _call(
        create_app(_FakeService()),
        "POST",
        "/v1/ask",
        body=json.dumps({"tenant_id": "t1", "question": "q"}).encode(),
    )
    assert status == 401
    assert payload["error"] == "missing_principal"


async def test_ask_returns_answer_and_passes_identity():
    service = _FakeService()
    status, payload = await _call(
        create_app(service),
        "POST",
        "/v1/ask",
        headers={"x-principal-id": "alice", "x-session-id": "sess_1"},
        body=json.dumps({"tenant_id": "t1", "question": "问题"}).encode(),
    )
    assert status == 200
    assert payload["refused"] is True
    assert payload["query_id"] == "qry_1"
    assert service.calls == [("alice", "t1", "sess_1")]


async def test_invalid_body_is_400():
    status, payload = await _call(
        create_app(_FakeService()),
        "POST",
        "/v1/ask",
        headers={"x-principal-id": "alice"},
        body=b"{}",
    )
    assert status == 400
    assert payload["error"] == "invalid_request"
