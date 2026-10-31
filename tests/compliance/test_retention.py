"""T-3 到期清除入口的时间参数解析。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kb.retention import _parse_now


def test_parse_now_accepts_iso_and_z_suffix():
    assert _parse_now("2026-12-31T00:00:00Z") == datetime(2026, 12, 31, tzinfo=UTC)
    assert _parse_now("2026-12-31T00:00:00") == datetime(2026, 12, 31, tzinfo=UTC)


def test_parse_now_rejects_invalid_value():
    with pytest.raises(SystemExit):
        _parse_now("not-a-date")
