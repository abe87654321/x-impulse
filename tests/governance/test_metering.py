"""FR-16-04：按租户/部门累计调用量与 token 计量。"""

from __future__ import annotations

from kb.governance.metering import InMemoryUsageMeter, usage_record


def test_accumulates_calls_and_answer_chars_per_tenant():
    meter = InMemoryUsageMeter()
    meter.record(
        usage_record(tenant_id="t1", principal_id="alice", actor_id=None, answer_text="答案")
    )
    meter.record(
        usage_record(tenant_id="t1", principal_id="bob", actor_id=None, answer_text="三字")
    )
    meter.record(usage_record(tenant_id="t2", principal_id="alice", actor_id=None, answer_text="x"))

    assert meter.totals("t1") == {"calls": 2, "answer_chars": 4, "tokens": 0}
    assert meter.totals("t2")["calls"] == 1


def test_department_scoped_totals():
    meter = InMemoryUsageMeter()
    meter.record(
        usage_record(
            tenant_id="t1", principal_id="alice", actor_id=None, answer_text="x", department="eng"
        )
    )
    meter.record(
        usage_record(
            tenant_id="t1", principal_id="bob", actor_id=None, answer_text="y", department="sales"
        )
    )

    assert meter.totals("t1", department="eng")["calls"] == 1
    assert meter.totals("t1")["calls"] == 0


def test_tokens_are_only_recorded_when_reported():
    meter = InMemoryUsageMeter()
    meter.record(
        usage_record(
            tenant_id="t1", principal_id="alice", actor_id=None, answer_text="x", tokens=42
        )
    )

    assert meter.totals("t1")["tokens"] == 42
