"""分层限流与批量抽取检测 —— FR-16-01 / FR-16-02 / FR-16-03。

设计要点：

- 阈值全部来自 `RateLimitConfig`（可由环境变量覆盖），不得散落硬编码。
- 状态通过 `WindowStore` 注入。默认的内存实现只用于开发与单进程演示；
  生产必须换成共享存储（Redis / Postgres），否则多副本各自计数会放宽限制。
- 限流按用户、按渠道、按 `(principal, actor)` 二元组分别计数。
- 批量抽取检测看两个信号：单会话窗口内 unique chunk 数、二元组窗口内
  unique 文档数。命中即写 security 审计并降速，不静默丢弃（FR-16-03）。
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kb.audit.writer import AuditEvent, AuditStore
from kb.identity.permissions import DelegationContext


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = True
    window_seconds: int = 60
    user_requests: int = 60
    channel_requests: int = 300
    pair_requests: int = 120
    # 架构 §5.13：L0 无最终用户可归因，阈值应显著低于 L2/L3。
    l0_factor: float = 0.25
    chunk_window_seconds: int = 600
    unique_chunks_per_window: int = 300
    doc_window_seconds: int = 86_400
    covered_docs_per_window: int = 1_000
    slowdown_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds 必须 > 0")
        for name in (
            "user_requests",
            "channel_requests",
            "pair_requests",
            "unique_chunks_per_window",
            "covered_docs_per_window",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须 > 0")
        if self.chunk_window_seconds <= 0 or self.doc_window_seconds <= 0:
            raise ValueError("检测窗口必须 > 0")
        if not 0 < self.l0_factor <= 1:
            raise ValueError("l0_factor 必须落在 (0, 1]")
        if self.slowdown_seconds < 0:
            raise ValueError("slowdown_seconds 不得为负")

    @classmethod
    def from_env(
        cls, prefix: str = "KB_RATE_LIMIT_", environ: Mapping[str, str] | None = None
    ) -> RateLimitConfig:
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for name, current in (
            ("enabled", cls.enabled),
            ("window_seconds", cls.window_seconds),
            ("user_requests", cls.user_requests),
            ("channel_requests", cls.channel_requests),
            ("pair_requests", cls.pair_requests),
            ("l0_factor", cls.l0_factor),
            ("chunk_window_seconds", cls.chunk_window_seconds),
            ("unique_chunks_per_window", cls.unique_chunks_per_window),
            ("doc_window_seconds", cls.doc_window_seconds),
            ("covered_docs_per_window", cls.covered_docs_per_window),
            ("slowdown_seconds", cls.slowdown_seconds),
        ):
            raw = env.get(f"{prefix}{name.upper()}")
            if raw is None:
                continue
            if isinstance(current, bool):
                values[name] = raw.strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(current, int):
                values[name] = int(raw)
            elif isinstance(current, float):
                values[name] = float(raw)
        return cls(**values)


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    reason: str | None = None
    retry_after_seconds: int = 0
    delay_seconds: float = 0.0

    @classmethod
    def permit(cls, *, delay_seconds: float = 0.0) -> RateDecision:
        return cls(allowed=True, delay_seconds=delay_seconds)

    @classmethod
    def deny(cls, reason: str, retry_after_seconds: int) -> RateDecision:
        return cls(allowed=False, reason=reason, retry_after_seconds=retry_after_seconds)


@dataclass(frozen=True)
class ExtractionSignal:
    alert: bool
    reason: str | None
    unique_chunks: int
    covered_docs: int


class RateLimited(Exception):
    """触发限流。上层必须转成明确错误码与重试建议（FR-16-03）。"""

    def __init__(self, decision: RateDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason or "rate_limited")


class WindowStore(Protocol):
    """滑动窗口计数存储。生产实现必须是共享存储。"""

    def record(self, key: str, value: str, *, now: datetime, window_seconds: int) -> int: ...

    def unique_count(self, key: str, *, now: datetime, window_seconds: int) -> int: ...

    def mark_once(self, key: str, *, now: datetime, window_seconds: int) -> bool: ...


@dataclass
class _Window:
    events: deque[tuple[datetime, str]] = field(default_factory=deque)


class InMemoryWindowStore:
    """单进程滑动窗口。仅用于开发/测试；多副本部署会各算各的。"""

    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}

    def _prune(self, key: str, now: datetime, window_seconds: int) -> _Window:
        window = self._windows.setdefault(key, _Window())
        cutoff = now - timedelta(seconds=window_seconds)
        while window.events and window.events[0][0] <= cutoff:
            window.events.popleft()
        return window

    def record(self, key: str, value: str, *, now: datetime, window_seconds: int) -> int:
        window = self._prune(key, now, window_seconds)
        window.events.append((now, value))
        return len(window.events)

    def unique_count(self, key: str, *, now: datetime, window_seconds: int) -> int:
        window = self._prune(key, now, window_seconds)
        return len({value for _, value in window.events})

    def mark_once(self, key: str, *, now: datetime, window_seconds: int) -> bool:
        window = self._prune(key, now, window_seconds)
        if window.events:
            return False
        window.events.append((now, "1"))
        return True


class RateLimiter:
    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        store: WindowStore | None = None,
        audit: AuditStore | None = None,
    ) -> None:
        self.config = config or RateLimitConfig.from_env()
        self._store: WindowStore = store or InMemoryWindowStore()
        self._audit = audit

    async def check(
        self,
        ctx: DelegationContext,
        *,
        tenant_id: str,
        channel: str,
        session_id: str,
        now: datetime | None = None,
    ) -> RateDecision:
        if not self.config.enabled:
            return RateDecision.permit()

        moment = now or datetime.now(UTC)
        principal_id = ctx.principal.id if ctx.principal else "anonymous"
        actor_id = ctx.actor.id if ctx.actor else "-"
        factor = self.config.l0_factor if ctx.assurance_level == "L0" else 1.0

        layers = (
            (
                f"rate:user:{tenant_id}:{principal_id}",
                _scaled(self.config.user_requests, factor),
                "rate_limited:user",
            ),
            (
                f"rate:channel:{tenant_id}:{channel}",
                _scaled(self.config.channel_requests, factor),
                "rate_limited:channel",
            ),
            (
                f"rate:pair:{tenant_id}:{principal_id}:{actor_id}",
                _scaled(self.config.pair_requests, factor),
                "rate_limited:pair",
            ),
        )
        for key, limit, reason in layers:
            count = self._store.record(
                key, "1", now=moment, window_seconds=self.config.window_seconds
            )
            if count > limit:
                return RateDecision.deny(reason, self.config.window_seconds)

        delay = self._slowdown_delay(tenant_id, session_id, moment)
        return RateDecision.permit(delay_seconds=delay)

    async def record_recall(
        self,
        ctx: DelegationContext,
        *,
        tenant_id: str,
        session_id: str,
        chunks: Sequence[tuple[str, str]],
        now: datetime | None = None,
    ) -> ExtractionSignal:
        if not self.config.enabled or not chunks:
            return ExtractionSignal(False, None, 0, 0)

        moment = now or datetime.now(UTC)
        principal_id = ctx.principal.id if ctx.principal else "anonymous"
        actor_id = ctx.actor.id if ctx.actor else "-"

        session_key = f"extract:chunks:{tenant_id}:{session_id}"
        for chunk_id, _doc_id in chunks:
            self._store.record(
                session_key, chunk_id, now=moment, window_seconds=self.config.chunk_window_seconds
            )
        unique_chunks = self._store.unique_count(
            session_key, now=moment, window_seconds=self.config.chunk_window_seconds
        )

        pair_key = f"extract:docs:{tenant_id}:{principal_id}:{actor_id}"
        for _chunk_id, doc_id in chunks:
            self._store.record(
                pair_key, doc_id, now=moment, window_seconds=self.config.doc_window_seconds
            )
        covered_docs = self._store.unique_count(
            pair_key, now=moment, window_seconds=self.config.doc_window_seconds
        )

        reasons: list[str] = []
        if unique_chunks > self.config.unique_chunks_per_window:
            reasons.append("session_unique_chunks")
            self._store.record(
                f"slow:{tenant_id}:{session_id}",
                "1",
                now=moment,
                window_seconds=self.config.chunk_window_seconds,
            )
            if self._store.mark_once(
                f"alert:chunks:{tenant_id}:{session_id}",
                now=moment,
                window_seconds=self.config.chunk_window_seconds,
            ):
                await self._alert(
                    ctx,
                    tenant_id=tenant_id,
                    action="batch_extraction_detected",
                    target_type="session",
                    target_id=session_id,
                    payload={"unique_chunks": unique_chunks, "reason": "session_unique_chunks"},
                    now=moment,
                )

        if covered_docs > self.config.covered_docs_per_window:
            reasons.append("pair_covered_docs")
            if self._store.mark_once(
                f"alert:docs:{tenant_id}:{principal_id}:{actor_id}",
                now=moment,
                window_seconds=self.config.doc_window_seconds,
            ):
                await self._alert(
                    ctx,
                    tenant_id=tenant_id,
                    action="coverage_threshold_exceeded",
                    target_type="principal",
                    target_id=principal_id,
                    payload={"covered_docs": covered_docs, "reason": "pair_covered_docs"},
                    now=moment,
                )

        return ExtractionSignal(
            alert=bool(reasons),
            reason=",".join(reasons) or None,
            unique_chunks=unique_chunks,
            covered_docs=covered_docs,
        )

    def _slowdown_delay(self, tenant_id: str, session_id: str, now: datetime) -> float:
        slow = self._store.unique_count(
            f"slow:{tenant_id}:{session_id}",
            now=now,
            window_seconds=self.config.chunk_window_seconds,
        )
        return self.config.slowdown_seconds if slow else 0.0

    async def _alert(
        self,
        ctx: DelegationContext,
        *,
        tenant_id: str,
        action: str,
        target_type: str,
        target_id: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                category="security",
                action=action,
                tenant_id=tenant_id,
                principal_id=ctx.principal.id if ctx.principal else None,
                actor_id=ctx.actor.id if ctx.actor else None,
                delegation_chain=(ctx.actor.id,) if ctx.actor else (),
                assurance_level=ctx.assurance_level,
                target_type=target_type,
                target_id=target_id,
                payload=payload,
            ),
            now=now,
        )


def _scaled(limit: int, factor: float) -> int:
    return max(1, int(limit * factor))


__all__ = [
    "ExtractionSignal",
    "InMemoryWindowStore",
    "RateDecision",
    "RateLimitConfig",
    "RateLimited",
    "RateLimiter",
    "WindowStore",
]
