"""契约测试辅助 —— 普通模块，测试文件里显式 import。

不是 pytest 约定文件。夹具在 conftest.py。
"""

from __future__ import annotations

import pytest
from kb.ingestion.contract import Connector


def skip_unless(conn: Connector, attr: str, expected=True) -> None:
    """按能力声明跳过。

    跳过【必须】由 capabilities() 驱动，不得手写 pytest.skip ——
    否则"因为难做所以跳过"会伪装成"因为源不支持所以跳过"。
    """
    caps = conn.capabilities()
    actual = getattr(caps, attr)
    if actual != expected:
        pytest.skip(f"capabilities().{attr} == {actual!r}，本用例不适用")
