"""pytest 夹具 —— pytest 自动发现并加载本文件，测试文件无需 import。

本文件只放 @pytest.fixture。数据构造器在 builders.py（需显式 import）。

以下四个夹具依赖尚未实现的组件，未实现前统一 skip，
使纯算法测试（TABLE / 属性测试）可以先跑起来。
"""

from __future__ import annotations

import importlib

import pytest
from builders import NOW


@pytest.fixture
def frozen_now():
    return NOW


@pytest.fixture(autouse=True)
def _seed_integration_db(request):
    """只给 integration 用例铺种子数据；单元测试不碰数据库。"""
    if request.node.get_closest_marker("integration") is None:
        return
    from integration_seed import seed

    seed()


def _require(module_name: str, class_name: str):
    """组件未实现时 skip 而不是 error —— import 也必须包在 try 里。"""
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, class_name)
        return factory()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"{class_name} 尚未实现：{exc}")


@pytest.fixture
def agent_registry():
    return _require("kb.identity.registry", "AgentRegistry")


@pytest.fixture
def acl_repo():
    return _require("kb.identity.repository", "AclRepository")


@pytest.fixture
def identity():
    return _require("kb.identity.service", "IdentityService")


@pytest.fixture
def retrieval():
    return _require("kb.retrieval.service", "RetrievalService")
