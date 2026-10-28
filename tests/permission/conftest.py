"""pytest 夹具 —— pytest 自动发现并加载本文件，测试文件无需 import。

本文件只放 @pytest.fixture。数据构造器在 builders.py（需显式 import）。

以下四个夹具依赖尚未实现的组件，未实现前统一 skip，
使纯算法测试（TABLE / 属性测试）可以先跑起来。
"""

from __future__ import annotations

import pytest
from builders import NOW


@pytest.fixture
def frozen_now():
    return NOW


def _require(name: str, factory):
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"{name} 尚未实现：{exc}")


@pytest.fixture
def agent_registry():
    from kb.identity.registry import AgentRegistry

    return _require("AgentRegistry", AgentRegistry)


@pytest.fixture
def acl_repo():
    from kb.identity.repository import AclRepository

    return _require("AclRepository", AclRepository)


@pytest.fixture
def identity():
    from kb.identity.service import IdentityService

    return _require("IdentityService", IdentityService)


@pytest.fixture
def retrieval():
    from kb.retrieval.service import RetrievalService

    return _require("RetrievalService", RetrievalService)
