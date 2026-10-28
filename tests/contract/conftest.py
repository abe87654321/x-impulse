"""pytest 夹具与参数化钩子 —— pytest 自动加载，测试文件无需 import。

本文件放：ConnectorSandbox 协议、夹具、pytest_generate_tests 参数化钩子。
辅助函数在 helpers.py；连接器登记表在 registry.py（非 pytest 约定，是本项目自定义）。

注意：pytest_generate_tests 是按目录生效的钩子。
本文件必须留在 tests/contract/ 下，不能上移到 tests/ ——
否则会试图对 permission 套件也做参数化。

对应 TD-02 §7。

每个连接器实现必须：
  1. 提供一个 ConnectorSandbox 实现（可操控的 mock 源或真实沙箱实例）
  2. 在 CONNECTORS 里注册
  3. 跑通 test_connector.py 的全部用例（不适用的可 skip，
     但 skip 必须由 capabilities() 声明驱动，不得手工 skip）
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

# kb.ingestion.contract 尚未实现时，整个契约套件跳过收集，
# 而不是让 ImportError 打断整轮 pytest（否则 permission 套件也跑不了）。
# 实现 TD-02 之后本分支自动失效。
collect_ignore: list[str] = []
try:
    from kb.ingestion.contract import Connector, DocumentRef  # noqa: F401
except ModuleNotFoundError:
    Connector = DocumentRef = object  # type: ignore[assignment,misc]
    collect_ignore = ["test_connector.py"]


@runtime_checkable
class ConnectorSandbox(Protocol):
    """可操控的源系统替身。每个连接器实现配一个。"""

    async def create_document(
        self, *, name: str = "doc.txt", container: str | None = None
    ) -> DocumentRef: ...
    async def create_document_with_broken_inheritance(self) -> DocumentRef: ...
    async def update_document(self, ref: DocumentRef | str) -> None: ...
    async def rename(self, ref: DocumentRef, new_name: str) -> None: ...
    async def delete(self, ref: DocumentRef) -> None: ...
    async def grant(
        self, ref: DocumentRef, *, effect: str, user: str | None = None, group: str | None = None
    ) -> None: ...
    def fail_next(self, *, times: int, error: str) -> CallCounter: ...
    async def reset(self) -> None: ...


class CallCounter(Protocol):
    count: int


# ── 注册表 ──────────────────────────────────────────────────
# 新增连接器时在此登记；无需改测试文件本身。


def _load_registered():
    try:
        from tests.contract.registry import CONNECTORS

        return CONNECTORS
    except ImportError:
        return {}


CONNECTORS = _load_registered()


def pytest_generate_tests(metafunc):
    """把 (conn, sandbox) 参数化到每个已注册的连接器。"""
    if "conn" in metafunc.fixturenames:
        if not CONNECTORS:
            pytest.skip("尚无已注册的连接器实现（见 tests/contract/registry.py）")
        metafunc.parametrize("connector_name", sorted(CONNECTORS), scope="function")


@pytest.fixture
async def sandbox(connector_name):
    factory = CONNECTORS[connector_name]["sandbox"]
    sb = factory()
    await sb.reset()
    yield sb


@pytest.fixture
async def conn(connector_name, sandbox) -> Connector:
    factory = CONNECTORS[connector_name]["connector"]
    return factory(sandbox)
