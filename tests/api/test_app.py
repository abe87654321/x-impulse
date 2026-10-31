"""ASGI 层：身份头、路由、请求/响应形状。"""

from __future__ import annotations

import json

from kb.api.app import create_app
from kb.api.chat_service import ChatResult, ChatStreamEvent
from kb.generation.citations import Answer
from kb.governance.rate_limit import RateDecision, RateLimited


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.rate_limit: RateDecision | None = None

    async def ask(self, ctx, tenant_id, question, *, session_id=None, channel="api"):
        self.calls.append((ctx.principal.id if ctx.principal else "", tenant_id, session_id))
        if self.rate_limit is not None:
            raise RateLimited(self.rate_limit)
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

    async def ask_stream(self, ctx, tenant_id, question, *, session_id=None, channel="api"):
        self.calls.append((ctx.principal.id if ctx.principal else "", tenant_id, session_id))
        if self.rate_limit is not None:
            raise RateLimited(self.rate_limit)
        yield ChatStreamEvent(kind="sentence", text="事实句。", sentence_index=0)
        yield ChatStreamEvent(
            kind="done",
            session_id=session_id or "sess_new",
            query_id="qry_1",
            answer_id="ans_1",
            refused=False,
        )


async def _call_raw(app, method, path, *, headers=None, body=b""):
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
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in sent[0].get("headers", [])
    }
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, response_headers, payload


async def _call(app, method, path, *, headers=None, body=b""):
    status, _, payload = await _call_raw(app, method, path, headers=headers, body=body)
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


async def test_stream_returns_sse_events():
    service = _FakeService()
    status, headers, payload = await _call_raw(
        create_app(service),
        "POST",
        "/v1/ask/stream",
        headers={"x-principal-id": "alice", "x-session-id": "sess_1"},
        body=json.dumps({"tenant_id": "t1", "question": "问题"}).encode(),
    )

    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    text = payload.decode()
    assert "event: sentence" in text
    assert "事实句。" in text
    assert "event: done" in text
    assert service.calls == [("alice", "t1", "sess_1")]


async def test_stream_missing_principal_is_401():
    status, _, payload = await _call_raw(
        create_app(_FakeService()),
        "POST",
        "/v1/ask/stream",
        body=json.dumps({"tenant_id": "t1", "question": "q"}).encode(),
    )

    assert status == 401
    assert json.loads(payload)["error"] == "missing_principal"


async def test_rate_limit_returns_429_with_retry_after():
    service = _FakeService()
    service.rate_limit = RateDecision.deny("rate_limited:user", 60)
    status, headers, payload = await _call_raw(
        create_app(service),
        "POST",
        "/v1/ask",
        headers={"x-principal-id": "alice"},
        body=json.dumps({"tenant_id": "t1", "question": "q"}).encode(),
    )

    assert status == 429
    assert headers["retry-after"] == "60"
    body = json.loads(payload)
    assert body["error"] == "rate_limited"
    assert body["reason"] == "rate_limited:user"


async def test_stream_rate_limit_returns_429_before_sse():
    service = _FakeService()
    service.rate_limit = RateDecision.deny("rate_limited:channel", 30)
    status, _, payload = await _call_raw(
        create_app(service),
        "POST",
        "/v1/ask/stream",
        headers={"x-principal-id": "alice"},
        body=json.dumps({"tenant_id": "t1", "question": "q"}).encode(),
    )

    assert status == 429
    assert json.loads(payload)["retry_after_seconds"] == 30
