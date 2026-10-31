"""调用量与 token 计量 —— FR-16-04（P1）。

为将来按量计费预留计量基础。当前按租户/主体累计调用次数、答案字符数与
模型报告的 token 数。真实 token 数由模型适配器上报；未上报时保持 0，
不做估算冒充计量值。

默认实现是内存版，仅适合单进程；生产应换成持久化实现。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class UsageRecord:
    tenant_id: str
    principal_id: str | None = None
    actor_id: str | None = None
    department: str | None = None
    calls: int = 0
    answer_chars: int = 0
    tokens: int = 0


class UsageMeter(Protocol):
    def record(self, record: UsageRecord, *, now: datetime | None = None) -> None: ...

    def totals(self, tenant_id: str, *, department: str | None = None) -> dict[str, int]: ...


class InMemoryUsageMeter:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str | None], list[UsageRecord]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, record: UsageRecord, *, now: datetime | None = None) -> None:
        del now
        with self._lock:
            self._records[(record.tenant_id, record.department)].append(record)

    def totals(self, tenant_id: str, *, department: str | None = None) -> dict[str, int]:
        with self._lock:
            records = list(self._records.get((tenant_id, department), ()))
        return {
            "calls": sum(record.calls for record in records),
            "answer_chars": sum(record.answer_chars for record in records),
            "tokens": sum(record.tokens for record in records),
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


def usage_record(
    *,
    tenant_id: str,
    principal_id: str | None,
    actor_id: str | None,
    answer_text: str,
    tokens: int = 0,
    department: str | None = None,
) -> UsageRecord:
    return UsageRecord(
        tenant_id=tenant_id,
        principal_id=principal_id,
        actor_id=actor_id,
        department=department,
        calls=1,
        answer_chars=len(answer_text),
        tokens=max(0, tokens),
    )


__all__ = [
    "InMemoryUsageMeter",
    "UsageMeter",
    "UsageRecord",
    "usage_record",
]
