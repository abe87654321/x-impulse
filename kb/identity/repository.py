"""ACL 仓储层 —— 把 acl_entries / principal_memberships 读成求值输入。

关键安全约束：
- `load_for` 必须按 tenant_id 过滤；跨租户隔离由加载层保证（TD-03 5a）。
- `inheritance_broken` 只在这里读取，交给 build_chain 翻译成节点属性（I-2）。
- 本层不做求值，只做加载与映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from kb.database import Database
from kb.identity.permissions import (
    AclRow,
    Effect,
    PrincipalType,
    ScopeLevel,
    expand_memberships,
)

DEFAULT_TENANT = "default"

_LOAD_SQL = """
SELECT tenant_id, object_type, object_id, principal_type, principal_id,
       effect, permission, scope_level, inherited_from, inheritance_broken, source_id
FROM acl_entries
WHERE tenant_id = $1 AND object_id = ANY($2::text[])
ORDER BY object_id, acl_id
"""

_BROKEN_SQL = """
SELECT DISTINCT object_id
FROM acl_entries
WHERE tenant_id = $1 AND inheritance_broken AND object_id = ANY($2::text[])
"""

_MEMBERSHIPS_SQL = """
SELECT member_id, group_id
FROM principal_memberships
WHERE tenant_id = $1
"""

_REVOKE_SQL = """
DELETE FROM acl_entries
WHERE tenant_id = $1 AND principal_type = 'user'
  AND principal_id = $2 AND object_id = $3 AND effect = 'allow'
"""


@dataclass(frozen=True)
class StoredAclRow:
    tenant_id: str
    object_type: ScopeLevel
    object_id: str
    principal_type: PrincipalType
    principal_id: str
    effect: Effect
    permission: str
    scope_level: ScopeLevel
    inherited_from: str | None
    inheritance_broken: bool
    source_id: str

    def to_acl_row(self) -> AclRow:
        return AclRow(
            object_type=self.object_type,
            object_id=self.object_id,
            principal_type=self.principal_type,
            principal_id=self.principal_id,
            effect=self.effect,
            permission=self.permission,
            scope_level=self.scope_level,
            inherited_from=self.inherited_from,
        )


class AclRepository:
    def __init__(self, db: Database | None = None) -> None:
        self._db = db or Database()

    def load_for(self, tenant_id: str, object_ids: list[str]) -> list[StoredAclRow]:
        if not object_ids:
            return []
        rows = self._db.fetch(_LOAD_SQL, tenant_id, list(object_ids))
        return [_to_stored(row) for row in rows]

    def load_acl_rows(self, tenant_id: str, object_ids: list[str]) -> dict[str, list[AclRow]]:
        grouped: dict[str, list[AclRow]] = {}
        for stored in self.load_for(tenant_id, object_ids):
            grouped.setdefault(stored.object_id, []).append(stored.to_acl_row())
        return grouped

    def load_broken_ids(self, tenant_id: str, object_ids: list[str]) -> frozenset[str]:
        if not object_ids:
            return frozenset()
        rows = self._db.fetch(_BROKEN_SQL, tenant_id, list(object_ids))
        return frozenset(str(row["object_id"]) for row in rows)

    def load_memberships(self, tenant_id: str, principal_id: str) -> frozenset[str]:
        rows = self._db.fetch(_MEMBERSHIPS_SQL, tenant_id)
        edges: dict[str, set[str]] = {}
        for row in rows:
            member = str(row["member_id"])
            group = str(row["group_id"])
            edges.setdefault(member, set()).add(group)
        return expand_memberships(principal_id, edges)

    def revoke(self, principal_id: str, object_id: str, *, tenant_id: str = DEFAULT_TENANT) -> str:
        """撤销某用户在某对象上的直接 allow（显式 deny 同理应收敛）。"""
        return self._db.execute(_REVOKE_SQL, tenant_id, principal_id, object_id)


def _to_stored(row: dict[str, object]) -> StoredAclRow:
    return StoredAclRow(
        tenant_id=str(row["tenant_id"]),
        object_type=_scope(row["object_type"]),
        object_id=str(row["object_id"]),
        principal_type=_principal_type(row["principal_type"]),
        principal_id=str(row["principal_id"]),
        effect=_effect(row["effect"]),
        permission=str(row["permission"]),
        scope_level=_scope(row["scope_level"]),
        inherited_from=_optional_str(row["inherited_from"]),
        inheritance_broken=bool(row["inheritance_broken"]),
        source_id=str(row["source_id"]),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _effect(value: object) -> Effect:
    text = str(value)
    if text not in ("allow", "deny"):
        raise ValueError(f"非法 effect: {text}")
    return cast(Effect, text)


def _principal_type(value: object) -> PrincipalType:
    text = str(value)
    if text not in ("user", "group", "role", "everyone"):
        raise ValueError(f"非法 principal_type: {text}")
    return cast(PrincipalType, text)


def _scope(value: object) -> ScopeLevel:
    text = str(value)
    if text not in ("source", "folder", "document", "field"):
        raise ValueError(f"非法 scope_level: {text}")
    return cast(ScopeLevel, text)


__all__ = ["AclRepository", "DEFAULT_TENANT", "StoredAclRow"]
