"""测试数据构造器 —— 普通模块，测试文件里显式 import。

不是 pytest 约定文件。这里只有纯函数，没有夹具。
夹具在 conftest.py。

对应 TD-03 §7 测试向量。
运行前提：`kb/identity/permissions.py` 尚不存在时导入会失败 —— 这是预期的起点。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

# 被测模块（尚未实现时此处 ImportError，属预期）
from kb.identity.permissions import (  # noqa: F401
    AclRow,
    AuthorizationError,
    DelegationContext,
    ObjectNode,
    PermissionModelError,
    Principal,
    build_chain,
    effective_scope,
    evaluate,
    expand_memberships,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# ── 对象链构造器 ────────────────────────────────────────────
# chain 顺序恒为 [self, parent, ..., root]，见 TD-03 §3

DOC = "doc-1"
FOLDER = "folder-1"
SOURCE = "source-1"


def node(
    object_id: str, object_type: str, parent_id: str | None = None, broken: bool = False
) -> ObjectNode:
    return ObjectNode(
        object_type=object_type,
        object_id=object_id,
        parent_id=parent_id,
        inheritance_broken=broken,
    )


def chain_doc(broken_at: str | None = None) -> list[ObjectNode]:
    """完整三级链：文档 → 目录 → 源。broken_at 指定在哪一级断开继承。"""
    return [
        node(DOC, "document", FOLDER, broken=(broken_at == "document")),
        node(FOLDER, "folder", SOURCE, broken=(broken_at == "folder")),
        node(SOURCE, "source", None, broken=(broken_at == "source")),
    ]


def hierarchy_doc() -> list[tuple[str, str, str | None]]:
    """build_chain 的输入：[(object_id, object_type, parent_id)]，自身 → 根。

    对应 TD-03 §3.5。注意这里【不含】断开继承信息 —— 那是 broken_ids
    的职责，它来自 acl_entries.inheritance_broken。
    """
    return [
        (DOC, "document", FOLDER),
        (FOLDER, "folder", SOURCE),
        (SOURCE, "source", None),
    ]


# ── ACL 条目构造器 ──────────────────────────────────────────


def _row(
    object_id: str, object_type: str, ptype: str, pid: str, effect: str, permission: str = "read"
) -> AclRow:
    return AclRow(
        object_type=object_type,
        object_id=object_id,
        principal_type=ptype,
        principal_id=pid,
        effect=effect,
        permission=permission,
        scope_level=object_type,
        inherited_from=None,
    )


def allow_user(
    pid: str, on: str = DOC, otype: str = "document", permission: str = "read"
) -> AclRow:
    return _row(on, otype, "user", pid, "allow", permission)


def deny_user(pid: str, on: str = DOC, otype: str = "document") -> AclRow:
    return _row(on, otype, "user", pid, "deny")


def allow_group(gid: str, on: str = DOC, otype: str = "document") -> AclRow:
    return _row(on, otype, "group", gid, "allow")


def deny_group(gid: str, on: str = DOC, otype: str = "document") -> AclRow:
    return _row(on, otype, "group", gid, "deny")


def allow_everyone(on: str = DOC, otype: str = "document") -> AclRow:
    return _row(on, otype, "everyone", "*", "allow")


def acls(*rows: AclRow) -> dict[str, list[AclRow]]:
    """按 object_id 分组。"""
    out: dict[str, list[AclRow]] = {}
    for r in rows:
        out.setdefault(r.object_id, []).append(r)
    return out


# ── 身份上下文构造器 ────────────────────────────────────────


def ctx_user(principal_id: str = "alice", *, expires_in_sec: int = 300) -> DelegationContext:
    return DelegationContext(
        principal=Principal("user", principal_id),
        actor=None,
        scope=frozenset(),
        assurance_level="L2",
        expires_at=NOW + timedelta(seconds=expires_in_sec),
        evidence={"method": "test"},
    )


def ctx_delegated(
    principal_id: str, agent_id: str, agent_scope: set[str], *, expires_in_sec: int = 300
) -> DelegationContext:
    return DelegationContext(
        principal=Principal("user", principal_id),
        actor=Principal("agent", agent_id),
        scope=frozenset(agent_scope),
        assurance_level="L2",
        expires_at=NOW + timedelta(seconds=expires_in_sec),
        evidence={"method": "test-delegated"},
    )


def ctx_l0(service_account: str = "svc-public") -> DelegationContext:
    """L0 公开层服务账号：principal 为 None（架构 §5.8）。"""
    return DelegationContext(
        principal=None,
        actor=Principal("agent", service_account),
        scope=frozenset({"kb.search"}),
        assurance_level="L0",
        expires_at=NOW + timedelta(seconds=300),
        evidence={"method": "service-account"},
    )


def ctx_expired(principal_id: str = "alice") -> DelegationContext:
    return replace(ctx_user(principal_id), expires_at=NOW - timedelta(seconds=1))
