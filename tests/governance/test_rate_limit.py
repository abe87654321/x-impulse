"""FR-16-01 / FR-16-02 / FR-16-03：分层限流、抽取检测、优雅拒绝。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from kb.audit.writer import AuditEvent
from kb.governance.rate_limit import (
    InMemoryWindowStore,
    RateLimitConfig,
    RateLimiter,
)
from kb.identity.permissions import Principal
from tests.retrieval.fakes import make_ctx

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, now: datetime | None = None) -> str:
        self.events.append(event)
        return str(len(self.events))


def _config(**overrides: object) -> RateLimitConfig:
    base = {
        "window_seconds": 60,
        "user_requests": 100,
        "channel_requests": 1000,
        "pair_requests": 1000,
    }
    base.update(overrides)
    return RateLimitConfig(**base)  # type: ignore[arg-type]


async def _check(limiter: RateLimiter, ctx=None, *, session="sess_1", now=NOW, channel="api"):
    return await limiter.check(
        ctx or make_ctx("alice"),
        tenant_id="t1",
        channel=channel,
        session_id=session,
        now=now,
    )


async def test_user_layer_denies_with_retry_after():
    limiter = RateLimiter(_config(user_requests=2))

    assert (await _check(limiter)).allowed
    assert (await _check(limiter)).allowed
    decision = await _check(limiter)

    assert decision.allowed is False
    assert decision.reason == "rate_limited:user"
    assert decision.retry_after_seconds == 60


async def test_channel_layer_denies_independent_of_user():
    limiter = RateLimiter(_config(channel_requests=1))

    assert (await _check(limiter, make_ctx("alice"))).allowed
    decision = await _check(limiter, make_ctx("bob"))

    assert decision.allowed is False
    assert decision.reason == "rate_limited:channel"


async def test_pair_layer_uses_principal_and_actor():
    limiter = RateLimiter(_config(pair_requests=1))
    ctx = make_ctx("alice")
    delegated = replace(ctx, actor=replace(ctx.principal))

    assert (await _check(limiter, delegated)).allowed
    decision = await _check(limiter, delegated)

    assert decision.allowed is False
    assert decision.reason == "rate_limited:pair"


async def test_different_actors_have_separate_pair_buckets():
    limiter = RateLimiter(_config(pair_requests=1))
    ctx = make_ctx("alice")
    alice_actor = replace(ctx, actor=Principal(type="agent", id="agent_a"))
    other_actor = replace(ctx, actor=Principal(type="agent", id="agent_b"))

    assert (await _check(limiter, alice_actor)).allowed
    assert (await _check(limiter, other_actor)).allowed


async def test_l0_thresholds_are_lower_than_l2():
    limiter = RateLimiter(_config(user_requests=4, l0_factor=0.25))
    l0 = replace(make_ctx("alice"), assurance_level="L0")
    l2 = replace(make_ctx("bob"), assurance_level="L2")

    assert (await _check(limiter, l0)).allowed
    assert (await _check(limiter, l0)).allowed is False
    assert (await _check(limiter, l2)).allowed
    assert (await _check(limiter, l2)).allowed


async def test_disabled_config_allows_everything():
    limiter = RateLimiter(_config(enabled=False, user_requests=1))
    for _ in range(5):
        assert (await _check(limiter)).allowed


async def test_config_from_env_overrides_defaults():
    config = RateLimitConfig.from_env(
        environ={
            "KB_RATE_LIMIT_USER_REQUESTS": "7",
            "KB_RATE_LIMIT_L0_FACTOR": "0.5",
            "KB_RATE_LIMIT_ENABLED": "false",
        }
    )

    assert config.user_requests == 7
    assert config.l0_factor == 0.5
    assert config.enabled is False


async def test_windows_expire():
    limiter = RateLimiter(_config(user_requests=1), store=InMemoryWindowStore())

    assert (await _check(limiter)).allowed
    later = NOW + timedelta(seconds=61)
    assert (await _check(limiter, now=later)).allowed


async def test_unique_chunk_threshold_alerts_and_slows_session():
    audit = _Audit()
    limiter = RateLimiter(
        _config(unique_chunks_per_window=2, slowdown_seconds=0.5),
        audit=audit,
    )
    chunks = [("chk_1", "doc_1"), ("chk_2", "doc_2"), ("chk_3", "doc_3")]

    signal = await limiter.record_recall(
        make_ctx("alice"), tenant_id="t1", session_id="sess_1", chunks=chunks, now=NOW
    )

    assert signal.alert is True
    assert signal.reason == "session_unique_chunks"
    assert signal.unique_chunks == 3
    assert [(e.category, e.action) for e in audit.events] == [
        ("security", "batch_extraction_detected")
    ]
    decision = await _check(limiter, now=NOW)
    assert decision.allowed is True
    assert decision.delay_seconds == 0.5


async def test_extraction_alert_is_emitted_once_per_window():
    audit = _Audit()
    limiter = RateLimiter(_config(unique_chunks_per_window=1), audit=audit)
    chunks = [("chk_1", "doc_1"), ("chk_2", "doc_2")]

    await limiter.record_recall(
        make_ctx("alice"), tenant_id="t1", session_id="sess_1", chunks=chunks, now=NOW
    )
    await limiter.record_recall(
        make_ctx("alice"), tenant_id="t1", session_id="sess_1", chunks=chunks, now=NOW
    )

    assert len(audit.events) == 1


async def test_doc_coverage_threshold_alerts_security():
    audit = _Audit()
    limiter = RateLimiter(
        _config(covered_docs_per_window=1),
        audit=audit,
    )
    chunks = [("chk_1", "doc_1"), ("chk_2", "doc_2")]

    signal = await limiter.record_recall(
        make_ctx("alice"), tenant_id="t1", session_id="sess_1", chunks=chunks, now=NOW
    )

    assert signal.covered_docs == 2
    assert "pair_covered_docs" in (signal.reason or "")
    assert [e.action for e in audit.events] == ["coverage_threshold_exceeded"]


async def test_denied_decision_carries_retry_hint():
    limiter = RateLimiter(_config(user_requests=1))
    ctx = make_ctx("alice")
    await limiter.check(ctx, tenant_id="t1", channel="api", session_id="s", now=NOW)
    decision = await limiter.check(ctx, tenant_id="t1", channel="api", session_id="s", now=NOW)

    assert decision.reason == "rate_limited:user"
    assert decision.retry_after_seconds == 60
