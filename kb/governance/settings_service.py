"""管理面设置项的权限、校验、审计与审批接缝。

角色取自 PRD FR-06-05 的六类内置角色。当前只做单人授权，不要求双人复核；
审批通过 `ApprovalPolicy` 接缝预留，将来可替换为双人审批而不改调用方。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from kb.audit.writer import AuditEvent, AuditStore
from kb.governance.settings_dag import ResolvedSettings, SettingsError
from kb.governance.settings_store import SettingsStore

ROLE_EMPLOYEE = "employee"
ROLE_EXPERT = "expert"
ROLE_KNOWLEDGE_ADMIN = "knowledge_admin"
ROLE_SYSTEM_ADMIN = "system_admin"
ROLE_SECURITY_AUDITOR = "security_auditor"
ROLE_IMPLEMENTATION_CONSULTANT = "implementation_consultant"

ALL_ROLES = (
    ROLE_EMPLOYEE,
    ROLE_EXPERT,
    ROLE_KNOWLEDGE_ADMIN,
    ROLE_SYSTEM_ADMIN,
    ROLE_SECURITY_AUDITOR,
    ROLE_IMPLEMENTATION_CONSULTANT,
)

DEFAULT_SETTINGS_WRITERS = frozenset({ROLE_SYSTEM_ADMIN, ROLE_IMPLEMENTATION_CONSULTANT})
DEFAULT_SETTINGS_READERS = frozenset(
    {ROLE_SYSTEM_ADMIN, ROLE_SECURITY_AUDITOR, ROLE_IMPLEMENTATION_CONSULTANT}
)


class SettingsWriteDenied(Exception):
    """当前角色无权修改设置。"""


class ApprovalRequired(Exception):
    """该变更需要审批，但未提供审批人。"""


class ApprovalPolicy(Protocol):
    async def check(
        self,
        tenant_id: str,
        changes: Mapping[str, tuple[object, object]],
        *,
        approved_by: str | None,
    ) -> None: ...


class NoApprovalPolicy:
    """当前默认：单人授权即可。将来替换为双人审批实现。"""

    async def check(
        self,
        tenant_id: str,
        changes: Mapping[str, tuple[object, object]],
        *,
        approved_by: str | None,
    ) -> None:
        del tenant_id, changes, approved_by


@dataclass(frozen=True)
class SettingsChangeResult:
    settings: ResolvedSettings
    changed: Mapping[str, tuple[object, object]]


class BackupSettingsService:
    def __init__(
        self,
        store: SettingsStore,
        *,
        audit: AuditStore | None = None,
        approval: ApprovalPolicy | None = None,
        writers: frozenset[str] = DEFAULT_SETTINGS_WRITERS,
        readers: frozenset[str] = DEFAULT_SETTINGS_READERS,
    ) -> None:
        self._store = store
        self._audit = audit
        self._approval: ApprovalPolicy = approval or NoApprovalPolicy()
        self._writers = writers
        self._readers = readers

    async def snapshot(self, tenant_id: str) -> ResolvedSettings:
        """内部读取（启动装配用）；管理面读取用 `snapshot_for`。"""
        from kb.compliance.backup_policy import evaluate_backup_policy

        return evaluate_backup_policy(await self._store.load(tenant_id))

    async def snapshot_for(self, roles: Sequence[str], tenant_id: str) -> ResolvedSettings:
        self._require(roles, self._readers, "读取备份设置")
        return await self.snapshot(tenant_id)

    async def describe_for(self, roles: Sequence[str]) -> tuple[dict[str, object], ...]:
        self._require(roles, self._readers, "读取备份设置")
        from kb.compliance.backup_policy import backup_settings_dag

        return backup_settings_dag().describe()

    async def propose(self, tenant_id: str, updates: Mapping[str, object]) -> ResolvedSettings:
        """只校验并返回提案，不写库。供 Agent 的 B′ 档使用。"""
        from kb.compliance.backup_policy import evaluate_backup_policy

        merged = {**dict(await self._store.load(tenant_id)), **updates}
        return evaluate_backup_policy(merged)

    async def update(
        self,
        roles: Sequence[str],
        tenant_id: str,
        updates: Mapping[str, object],
        *,
        updated_by: str,
        approved_by: str | None = None,
    ) -> SettingsChangeResult:
        self._require(roles, self._writers, "修改备份设置")
        if not updates:
            raise ValueError("没有需要修改的设置项")

        from kb.compliance.backup_policy import evaluate_backup_policy

        current = dict(await self._store.load(tenant_id))
        merged = {**current, **updates}
        resolved = evaluate_backup_policy(merged)
        if not resolved.ready:
            raise SettingsError("；".join(resolved.errors))

        changes = {
            key: (current.get(key), value)
            for key, value in updates.items()
            if current.get(key) != value
        }
        await self._approval.check(tenant_id, changes, approved_by=approved_by)
        for key, value in updates.items():
            await self._store.put(tenant_id, key, value, updated_by)
        if changes:
            await self._audit_change(tenant_id, updated_by, changes)
        return SettingsChangeResult(settings=resolved, changed=changes)

    async def _audit_change(
        self,
        tenant_id: str,
        updated_by: str,
        changes: Mapping[str, tuple[object, object]],
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                category="admin",
                action="backup_settings_updated",
                tenant_id=tenant_id,
                principal_id=updated_by,
                target_type="settings",
                target_id="backup",
                payload={
                    "changes": {
                        key: {"old": old, "new": new} for key, (old, new) in changes.items()
                    }
                },
            ),
            now=datetime.now(UTC),
        )

    @staticmethod
    def _require(roles: Sequence[str], allowed: frozenset[str], action: str) -> None:
        if not set(roles).intersection(allowed):
            raise SettingsWriteDenied(
                f"角色 {sorted(set(roles))} 无权{action}；允许角色 {sorted(allowed)}"
            )


__all__ = [
    "ALL_ROLES",
    "ApprovalPolicy",
    "ApprovalRequired",
    "BackupSettingsService",
    "DEFAULT_SETTINGS_READERS",
    "DEFAULT_SETTINGS_WRITERS",
    "NoApprovalPolicy",
    "ROLE_EMPLOYEE",
    "ROLE_EXPERT",
    "ROLE_IMPLEMENTATION_CONSULTANT",
    "ROLE_KNOWLEDGE_ADMIN",
    "ROLE_SECURITY_AUDITOR",
    "ROLE_SYSTEM_ADMIN",
    "SettingsChangeResult",
    "SettingsWriteDenied",
]
