"""身份服务 —— 离职等即时失效事件。

FR-06-29 要求离职后立即失效，不等下一轮 ACL 同步。当前 schema 没有
"principal 状态"表，因此本实现采取最小可验证动作：删除该用户的组关系快照
与其直接 allow 条目。真实部署应由 IdP 事件驱动并配合身份状态同步，
而不是长期依赖删除 ACL 配置。
"""

from __future__ import annotations

from kb.database import Database
from kb.identity.repository import DEFAULT_TENANT

_DROP_MEMBERSHIPS_SQL = """
DELETE FROM principal_memberships WHERE tenant_id = $1 AND member_id = $2
"""

_DROP_DIRECT_ALLOW_SQL = """
DELETE FROM acl_entries
WHERE tenant_id = $1 AND principal_type = 'user'
  AND principal_id = $2 AND effect = 'allow'
"""


class IdentityService:
    def __init__(self, db: Database | None = None, tenant_id: str = DEFAULT_TENANT) -> None:
        self._db = db or Database()
        self._tenant_id = tenant_id

    def offboard(self, principal_id: str) -> None:
        self._db.execute(_DROP_MEMBERSHIPS_SQL, self._tenant_id, principal_id)
        self._db.execute(_DROP_DIRECT_ALLOW_SQL, self._tenant_id, principal_id)


__all__ = ["IdentityService"]
