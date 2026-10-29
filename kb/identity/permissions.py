"""权限求值 —— TD-03 §2/§3/§3.5/§4/§6。

本模块是安全关键：逻辑错误即越权。evaluate / build_chain / expand_memberships
都是纯函数，无 IO、无全局状态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from kb.identity.registry import AgentRegistry

Effect = Literal["allow", "deny"]
PrincipalType = Literal["user", "group", "role", "everyone"]
ScopeLevel = Literal["source", "folder", "document", "field"]
AssuranceLevel = Literal["L0", "L1", "L2", "L3"]

FULL_SCOPE: frozenset[str] = frozenset({"*"})


class PermissionModelError(Exception):
    """权限模型本身不成立（如组展开超深度）。"""


class AuthorizationError(Exception):
    """授权断言不成立（如委派过期、Agent 未登记）。"""


@dataclass(frozen=True)
class Principal:
    type: PrincipalType
    id: str


@dataclass(frozen=True)
class DelegationContext:
    """架构 §5.3。核心逻辑只读此结构，不感知 IdP 协议。"""

    principal: Principal | None
    actor: Principal | None
    scope: frozenset[str]
    assurance_level: AssuranceLevel
    expires_at: datetime
    evidence: Mapping[str, object]


@dataclass(frozen=True)
class AclRow:
    """来自 acl_entries 表，见 TD-01 §4。

    ★ 不含 inheritance_broken：断开继承是【对象】属性，只在 ObjectNode 上表达。
    """

    object_type: ScopeLevel
    object_id: str
    principal_type: PrincipalType
    principal_id: str
    effect: Effect
    permission: str
    scope_level: ScopeLevel
    inherited_from: str | None


@dataclass(frozen=True)
class ObjectNode:
    """被保护对象在继承链上的一个节点。"""

    object_type: ScopeLevel
    object_id: str
    parent_id: str | None
    inheritance_broken: bool


def evaluate(
    ctx: DelegationContext,
    chain: list[ObjectNode],
    acls: Mapping[str, Sequence[AclRow]],
    memberships: frozenset[str],
    permission: str = "read",
) -> bool:
    """FR-06-28：任一适用 deny 命中即拒绝；否则至少一条适用 allow；默认拒绝。"""

    applicable_objects: list[str] = []
    for node in chain:
        applicable_objects.append(node.object_id)
        if node.inheritance_broken:
            break

    def matches_principal(row: AclRow) -> bool:
        if row.principal_type == "everyone":
            return True
        if row.principal_type == "user":
            return ctx.principal is not None and row.principal_id == ctx.principal.id
        return row.principal_id in memberships

    applicable = [
        row
        for oid in applicable_objects
        for row in acls.get(oid, ())
        if row.permission == permission and matches_principal(row)
    ]

    if any(row.effect == "deny" for row in applicable):
        return False

    if any(row.effect == "allow" for row in applicable):
        return True

    return False


def build_chain(
    hierarchy: list[tuple[str, ScopeLevel, str | None]],
    broken_ids: frozenset[str],
) -> list[ObjectNode]:
    """把"哪些对象断开了继承"从条目侧搬到节点侧 —— §3.5 的唯一翻译点。"""

    return [
        ObjectNode(
            object_type=otype,
            object_id=oid,
            parent_id=parent,
            inheritance_broken=(oid in broken_ids),
        )
        for oid, otype, parent in hierarchy
    ]


def expand_memberships(
    principal_id: str,
    edges: Mapping[str, set[str]],
    max_depth: int = 16,
) -> frozenset[str]:
    """传递闭包（含嵌套组）。超深度抛异常，不静默截断。"""

    seen: set[str] = set()
    frontier = {principal_id}
    depth = 0
    while frontier and depth < max_depth:
        nxt: set[str] = set()
        for m in frontier:
            for g in edges.get(m, ()):
                if g not in seen:
                    seen.add(g)
                    nxt.add(g)
        frontier = nxt
        depth += 1
    if frontier:
        raise PermissionModelError(f"group nesting exceeded depth {max_depth} for {principal_id}")
    return frozenset(seen)


def effective_scope(
    ctx: DelegationContext,
    registry: AgentRegistry | None,
    *,
    now: datetime,
) -> frozenset[str]:
    """架构 §5.9：有效权限 = min(用户 ACL, Agent scope)。"""

    if ctx.expires_at <= now:
        raise AuthorizationError("delegation expired")

    if ctx.actor is None:
        return FULL_SCOPE

    if registry is None:
        raise AuthorizationError("agent registry required for delegated context")
    agent = registry.get(ctx.actor.id)
    if agent is None:
        raise AuthorizationError("unregistered agent")

    return ctx.scope & agent.granted_scope
