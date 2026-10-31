"""备份与归档的删除出口 —— 删除设计 §6.3 / FR-12-04 位置 8。

备份介质无法就地改写，因此删除不是"擦除文件"，而是三件事的组合：

1. 登记该对象已进入删除范围（供恢复流程比对抑制清单）。
2. 密钥销毁使备份中的 PII 不可读（由 crypto.py 的主体系密钥完成）。
3. 按轮转周期让含旧数据的介质自然过期，并给出可举证的时间点。

本模块只定义接口与**登记/到期跟踪**实现。真实介质的销毁由外部备份系统
负责；`TrackingBackupRotation` 记录义务与到期时间，不代表介质已被物理清除。
E-3（轮转周期）仍未由客户确认，因此周期必须显式配置。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

BackupStatus = Literal["LOGICAL_DONE", "SKIPPED", "FAILED"]


@dataclass(frozen=True)
class BackupErasureOutcome:
    status: BackupStatus
    detail: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class BackupRotationStatus:
    configured: bool
    rotation_days: int | None
    pending: int
    next_expiry_at: datetime | None


class BackupRotation(Protocol):
    async def notify_erasure(
        self,
        tenant_id: str,
        *,
        scope_type: str,
        scope_key: str,
        now: datetime | None = None,
    ) -> BackupErasureOutcome: ...

    async def rotation_status(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> BackupRotationStatus: ...

    async def rotate(self, tenant_id: str, *, now: datetime | None = None) -> int: ...

    def client_statement(self) -> str: ...


class UnconfiguredBackupRotation:
    """未接入备份系统。删除证明必须显式声明未覆盖，不得假装已处理。"""

    async def notify_erasure(
        self,
        tenant_id: str,
        *,
        scope_type: str,
        scope_key: str,
        now: datetime | None = None,
    ) -> BackupErasureOutcome:
        return BackupErasureOutcome("SKIPPED", "未接入备份介质与轮转")

    async def rotation_status(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> BackupRotationStatus:
        return BackupRotationStatus(
            configured=False, rotation_days=None, pending=0, next_expiry_at=None
        )

    async def rotate(self, tenant_id: str, *, now: datetime | None = None) -> int:
        return 0

    def client_statement(self) -> str:
        return "备份介质尚未接入本系统，无法确认备份中的相关数据处理状态。"


@dataclass
class _BackupMark:
    marked_at: datetime
    expires_at: datetime


class TrackingBackupRotation:
    """登记删除范围并按轮转周期到期；只做记账，不物理销毁介质。"""

    def __init__(self, *, rotation_days: int) -> None:
        if rotation_days <= 0:
            raise ValueError("rotation_days 必须 > 0")
        self._rotation_days = rotation_days
        self._marks: dict[tuple[str, str, str], _BackupMark] = {}

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
        moment = now or datetime.now(UTC)
        key = (tenant_id, scope_type, scope_key)
        mark = self._marks.get(key)
        if mark is None:
            mark = _BackupMark(
                marked_at=moment,
                expires_at=moment + timedelta(days=self._rotation_days),
            )
            self._marks[key] = mark
        return BackupErasureOutcome(
            "LOGICAL_DONE",
            f"已登记删除范围；限制处理至 {mark.expires_at.date().isoformat()} 轮转",
            expires_at=mark.expires_at,
        )

    async def rotation_status(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> BackupRotationStatus:
        moment = now or datetime.now(UTC)
        pending = [
            mark
            for (tenant, _scope_type, _scope_key), mark in self._marks.items()
            if tenant == tenant_id and mark.expires_at > moment
        ]
        next_expiry = min((mark.expires_at for mark in pending), default=None)
        return BackupRotationStatus(
            configured=True,
            rotation_days=self._rotation_days,
            pending=len(pending),
            next_expiry_at=next_expiry,
        )

    async def rotate(self, tenant_id: str, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        expired = [
            key
            for key, mark in self._marks.items()
            if key[0] == tenant_id and mark.expires_at <= moment
        ]
        for key in expired:
            del self._marks[key]
        return len(expired)

    def client_statement(self) -> str:
        return (
            "备份介质无法就地改写，本系统不宣称备份已被物理清除；"
            f"备份介质将在 {self._rotation_days} 天轮转周期内自然过期。"
            "对客表述前须确认备份系统已停止除存储与安全保护外的处理。"
        )


def build_backup_rotation_from_env(
    environ: Mapping[str, str] | None = None,
) -> BackupRotation:
    env = os.environ if environ is None else environ
    raw = env.get("KB_BACKUP_ROTATION_DAYS")
    if raw is None or not raw.strip():
        return UnconfiguredBackupRotation()
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError("KB_BACKUP_ROTATION_DAYS 不是整数") from exc
    if days <= 0:
        raise ValueError("KB_BACKUP_ROTATION_DAYS 必须 > 0")
    return TrackingBackupRotation(rotation_days=days)


__all__ = [
    "BackupErasureOutcome",
    "BackupRotation",
    "BackupRotationStatus",
    "BackupStatus",
    "TrackingBackupRotation",
    "UnconfiguredBackupRotation",
    "build_backup_rotation_from_env",
]
