"""备份目录领域模型 —— 与厂商无关的端口与轮转语义。

领域层只关心三件事：备份代次、租户边界、到期时间。具体是数据库 PITR、
对象存储、虚拟机快照还是第三方平台，由适配器实现 `BackupCatalog`。

扩展方式：新增 `BackupCatalog` 适配器并在注册表登记，不改领域层。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from kb.compliance.backup import (
    BackupErasureOutcome,
    BackupRotationStatus,
    TrackingBackupRotation,
)

TenantIsolation = Literal["per_tenant", "shared", "global"]
BackupScope = Literal["tenant", "shared", "global"]


class BackupCatalogError(Exception):
    """目录不可用或返回结构不合法。"""


@dataclass(frozen=True)
class BackupGeneration:
    backup_id: str
    created_at: datetime
    expires_at: datetime
    scope: BackupScope = "tenant"
    tenant_id: str | None = None
    encryption_key_ref: str | None = None
    status: str = "available"


class BackupCatalog(Protocol):
    """备份目录端口。**必须**在返回前应用租户过滤。"""

    async def list_generations(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> Sequence[BackupGeneration]: ...

    async def expire(self, backup_ids: Sequence[str], *, tenant_id: str, evidence: str) -> int: ...


class CatalogBackupRotation:
    """基于目录的轮转实现，替代只记账的内存版本。"""

    def __init__(
        self,
        catalog: BackupCatalog,
        *,
        rotation_days: int,
        tenant_isolation: TenantIsolation = "per_tenant",
    ) -> None:
        if rotation_days <= 0:
            raise ValueError("rotation_days 必须 > 0")
        self._catalog = catalog
        self._rotation_days = rotation_days
        self._isolation = tenant_isolation

    @property
    def rotation_days(self) -> int:
        return self._rotation_days

    async def notify_erasure(
        self,
        tenant_id: str,
        *,
        scope_type: str,
        scope_key: str,
        now: datetime | None = None,
    ) -> BackupErasureOutcome:
        del scope_type, scope_key
        moment = now or datetime.now(UTC)
        generations = await self._catalog.list_generations(tenant_id, now=moment)
        if not generations:
            return BackupErasureOutcome(
                "LOGICAL_DONE", "目录中无匹配备份代次；无需轮转", expires_at=None
            )
        expires_at = max(generation.expires_at for generation in generations)
        return BackupErasureOutcome(
            "LOGICAL_DONE",
            f"已登记 {len(generations)} 个备份代次；最晚于 {expires_at.date().isoformat()} 轮转",
            expires_at=expires_at,
        )

    async def rotation_status(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> BackupRotationStatus:
        moment = now or datetime.now(UTC)
        generations = await self._catalog.list_generations(tenant_id, now=moment)
        pending = [generation for generation in generations if generation.expires_at > moment]
        return BackupRotationStatus(
            configured=True,
            rotation_days=self._rotation_days,
            pending=len(pending),
            next_expiry_at=min((g.expires_at for g in pending), default=None),
        )

    async def rotate(self, tenant_id: str, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        generations = await self._catalog.list_generations(tenant_id, now=moment)
        due = [g.backup_id for g in generations if g.expires_at <= moment]
        if not due:
            return 0
        evidence = f"rotation:{self._isolation}:{moment.date().isoformat()}"
        return await self._catalog.expire(due, tenant_id=tenant_id, evidence=evidence)

    def client_statement(self) -> str:
        return TrackingBackupRotation(rotation_days=self._rotation_days).client_statement()


def ensure_not_global_purge(tenant_isolation: TenantIsolation) -> None:
    """共享/全局备份不能按单租户物理清除，只能加密擦除 + 到期轮转。"""
    if tenant_isolation == "global":
        raise BackupCatalogError("全局备份不支持按租户物理清除，只能加密擦除并等待轮转")


__all__ = [
    "BackupCatalog",
    "BackupCatalogError",
    "BackupGeneration",
    "BackupScope",
    "CatalogBackupRotation",
    "TenantIsolation",
    "ensure_not_global_purge",
]
