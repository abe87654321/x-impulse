"""按租户设置动态选择备份轮转实现。

删除服务只持有一个 `BackupRotation` 对象，但备份配置是租户级设置。本类
在每次调用时读取 `service_settings`，未显式配置 `backup_system` 时保持
`UnconfiguredBackupRotation`，不拿推荐默认值冒充已接入。
"""

from __future__ import annotations

from datetime import datetime

from kb.compliance.backup import (
    BackupErasureOutcome,
    BackupRotation,
    BackupRotationStatus,
    UnconfiguredBackupRotation,
)
from kb.compliance.backup_adapters import BackupCatalogRegistry, CatalogDependencies
from kb.compliance.backup_policy import (
    build_rotation_from_policy,
    resolve_backup_policy,
)
from kb.governance.settings_store import SettingsStore


class PolicyBackupRotation:
    def __init__(
        self,
        store: SettingsStore,
        dependencies: CatalogDependencies | None = None,
        *,
        registry: BackupCatalogRegistry | None = None,
    ) -> None:
        self._store = store
        self._dependencies = dependencies or CatalogDependencies()
        self._registry = registry

    async def _rotation(self, tenant_id: str) -> BackupRotation:
        values = dict(await self._store.load(tenant_id))
        if "backup_system" not in values:
            return UnconfiguredBackupRotation()
        policy = resolve_backup_policy(values)
        return build_rotation_from_policy(
            policy,
            dependencies=self._dependencies,
            registry=self._registry,
        )

    async def notify_erasure(
        self,
        tenant_id: str,
        *,
        scope_type: str,
        scope_key: str,
        now: datetime | None = None,
    ) -> BackupErasureOutcome:
        rotation = await self._rotation(tenant_id)
        return await rotation.notify_erasure(
            tenant_id, scope_type=scope_type, scope_key=scope_key, now=now
        )

    async def rotation_status(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> BackupRotationStatus:
        rotation = await self._rotation(tenant_id)
        return await rotation.rotation_status(tenant_id, now=now)

    async def rotate(self, tenant_id: str, *, now: datetime | None = None) -> int:
        rotation = await self._rotation(tenant_id)
        return await rotation.rotate(tenant_id, now=now)

    def client_statement(self) -> str:
        return (
            "备份轮转按租户设置与外部备份系统执行；本系统不宣称备份介质已被物理清除，"
            "只陈述受限处理、轮转到期与恢复时比对抑制清单。"
        )


__all__ = ["PolicyBackupRotation"]
