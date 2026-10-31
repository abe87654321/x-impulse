"""备份策略的设置项 DAG —— 管理面可选的 6 项与推荐默认值。

管理员可以只改其中几项；缺失项采用 `recommended` 并标记来源为
`recommended`（而不是假装是管理员确认过的值）。求值按依赖顺序进行。

安全默认：任何会让“恢复备份后删除状态回滚”或“无法做主体级加密擦除”
的组合都直接判为错误，不提供静默降级。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kb.compliance.backup import BackupRotation, UnconfiguredBackupRotation
from kb.governance.settings_dag import (
    ResolvedSettings,
    SettingsDAG,
    SettingsError,
    SettingSpec,
)

if TYPE_CHECKING:
    from kb.compliance.backup_adapters import (
        BackupCatalogRegistry,
        CatalogDependencies,
    )
    from kb.compliance.backup_domain import TenantIsolation

BACKUP_SYSTEMS = ("none", "database_pitr", "object_storage", "vm_snapshot", "external_vendor")
PERSISTENCE_LOCATIONS = ("none", "external_catalog", "audit_log", "new_table")
SCHEDULERS = ("none", "external_cron", "k8s_cronjob", "ops_platform")
ENCRYPTION_MODES = ("none", "single_long_lived", "backup_key", "subject_dek")
AUTHORITY_STORES = ("db_only", "kms_only", "kms_and_external_db")
TENANT_ISOLATIONS = ("per_tenant", "shared", "global")
CATALOG_MODES = ("manifest", "http_api", "s3_api")


def _backup_enabled(values: Mapping[str, object]) -> bool:
    return values.get("backup_system", "none") != "none"


def _check_rotation_days(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    del values
    if isinstance(raw, int) and not 1 <= raw <= 3650:
        return ("轮转周期应在 1~3650 天之间",)
    return ()


def _check_persistence(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    if raw == "none" and _backup_enabled(values):
        return ("备份已启用但没有持久化位置，删除登记会丢失",)
    return ()


def _check_scheduler(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    if raw == "none" and _backup_enabled(values):
        return ("备份已启用但没有调度器，轮转不会执行",)
    return ()


def _check_encryption(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    del values
    if raw == "none":
        return ("备份未加密，密钥销毁无法使数据不可读",)
    if raw == "single_long_lived":
        return ("单一长期密钥无法支持主体级加密擦除",)
    return ()


def _check_endpoint(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    del values
    if isinstance(raw, str) and not raw.startswith(("http://", "https://")):
        return ("目录 API 地址必须以 http:// 或 https:// 开头",)
    return ()


def _check_authority(raw: object, values: Mapping[str, object]) -> tuple[str, ...]:
    if raw == "db_only":
        return ("仅依赖业务库会随备份恢复回滚删除状态，禁止",)
    if raw in {"kms_only", "kms_and_external_db"} and values.get("backup_encryption") not in {
        "subject_dek",
        "backup_key",
    }:
        return ("密钥权威存储与当前加密方式不匹配",)
    return ()


def backup_settings_dag() -> SettingsDAG:
    return SettingsDAG(
        (
            SettingSpec(
                key="backup_system",
                title="备份系统类型",
                kind="choice",
                options=BACKUP_SYSTEMS,
                recommended="database_pitr",
            ),
            SettingSpec(
                key="rotation_days",
                title="备份轮转周期（天）",
                kind="int",
                recommended=90,
                depends_on=("backup_system",),
                applies_when=_backup_enabled,
                check=_check_rotation_days,
            ),
            SettingSpec(
                key="persistence_location",
                title="删除登记持久化位置",
                kind="choice",
                options=PERSISTENCE_LOCATIONS,
                recommended="external_catalog",
                depends_on=("backup_system",),
                applies_when=_backup_enabled,
                check=_check_persistence,
            ),
            SettingSpec(
                key="scheduler",
                title="轮转任务归属",
                kind="choice",
                options=SCHEDULERS,
                recommended="k8s_cronjob",
                depends_on=("rotation_days",),
                applies_when=_backup_enabled,
                check=_check_scheduler,
            ),
            SettingSpec(
                key="tenant_isolation",
                title="备份的租户隔离方式",
                kind="choice",
                options=TENANT_ISOLATIONS,
                recommended="per_tenant",
                depends_on=("backup_system",),
                applies_when=_backup_enabled,
            ),
            SettingSpec(
                key="catalog_mode",
                title="备份目录接入方式",
                kind="choice",
                options=CATALOG_MODES,
                recommended="manifest",
                depends_on=("backup_system",),
                applies_when=_backup_enabled,
            ),
            SettingSpec(
                key="manifest_uri",
                title="manifest 目录位置",
                kind="string",
                recommended="file:///var/lib/kb/backups/manifest.jsonl",
                depends_on=("catalog_mode",),
                applies_when=lambda values: (
                    _backup_enabled(values) and values.get("catalog_mode") == "manifest"
                ),
            ),
            SettingSpec(
                key="catalog_endpoint",
                title="目录 API / S3 地址",
                kind="string",
                depends_on=("catalog_mode",),
                applies_when=lambda values: (
                    _backup_enabled(values) and values.get("catalog_mode") in ("http_api", "s3_api")
                ),
                check=_check_endpoint,
            ),
            SettingSpec(
                key="catalog_credential_ref",
                title="目录 API / S3 凭据引用（只存引用名，不存密钥）",
                kind="string",
                depends_on=("catalog_mode",),
                applies_when=lambda values: (
                    _backup_enabled(values) and values.get("catalog_mode") in ("http_api", "s3_api")
                ),
            ),
            SettingSpec(
                key="catalog_bucket",
                title="S3 备份桶",
                kind="string",
                depends_on=("catalog_mode",),
                applies_when=lambda values: (
                    _backup_enabled(values) and values.get("catalog_mode") == "s3_api"
                ),
            ),
            SettingSpec(
                key="catalog_prefix",
                title="S3 备份前缀",
                kind="string",
                recommended="kb-backups/",
                depends_on=("catalog_mode",),
                applies_when=lambda values: (
                    _backup_enabled(values) and values.get("catalog_mode") == "s3_api"
                ),
            ),
            SettingSpec(
                key="backup_encryption",
                title="备份加密方式",
                kind="choice",
                options=ENCRYPTION_MODES,
                recommended="subject_dek",
                check=_check_encryption,
            ),
            SettingSpec(
                key="authority_store",
                title="抑制清单与密钥销毁状态的权威源",
                kind="choice",
                options=AUTHORITY_STORES,
                recommended="kms_and_external_db",
                depends_on=("backup_encryption",),
                check=_check_authority,
            ),
        )
    )


@dataclass(frozen=True)
class BackupPolicy:
    backup_system: str
    rotation_days: int | None
    persistence_location: str | None
    scheduler: str | None
    tenant_isolation: str | None
    catalog_mode: str | None
    manifest_uri: str | None
    catalog_endpoint: str | None
    catalog_credential_ref: str | None
    catalog_bucket: str | None
    catalog_prefix: str | None
    backup_encryption: str
    authority_store: str


def evaluate_backup_policy(overrides: Mapping[str, object]) -> ResolvedSettings:
    """给管理面用：返回推荐值、来源与全部错误，不抛异常。"""
    return backup_settings_dag().resolve(overrides)


def resolve_backup_policy(overrides: Mapping[str, object]) -> BackupPolicy:
    resolved = evaluate_backup_policy(overrides)
    resolved.require_ready()
    values = resolved.values
    return BackupPolicy(
        backup_system=str(values["backup_system"]),
        rotation_days=_optional_int(values.get("rotation_days")),
        persistence_location=_optional_str(values.get("persistence_location")),
        scheduler=_optional_str(values.get("scheduler")),
        tenant_isolation=_optional_str(values.get("tenant_isolation")),
        catalog_mode=_optional_str(values.get("catalog_mode")),
        manifest_uri=_optional_str(values.get("manifest_uri")),
        catalog_endpoint=_optional_str(values.get("catalog_endpoint")),
        catalog_credential_ref=_optional_str(values.get("catalog_credential_ref")),
        catalog_bucket=_optional_str(values.get("catalog_bucket")),
        catalog_prefix=_optional_str(values.get("catalog_prefix")),
        backup_encryption=str(values["backup_encryption"]),
        authority_store=str(values["authority_store"]),
    )


def build_rotation_from_policy(
    policy: BackupPolicy,
    *,
    dependencies: CatalogDependencies | None = None,
    registry: BackupCatalogRegistry | None = None,
) -> BackupRotation:
    """按策略构造目录驱动的轮转实现；扩展只需注册新的目录适配器。"""
    from kb.compliance.backup_adapters import (
        CatalogDependencies,
        default_backup_registry,
    )
    from kb.compliance.backup_domain import CatalogBackupRotation

    if policy.backup_system == "none":
        return UnconfiguredBackupRotation()
    if policy.rotation_days is None:
        raise SettingsError("备份已启用但缺少轮转周期")
    if policy.catalog_mode is None:
        raise SettingsError("备份已启用但缺少目录接入方式")
    settings = {
        "manifest_uri": policy.manifest_uri,
        "catalog_endpoint": policy.catalog_endpoint,
        "catalog_credential_ref": policy.catalog_credential_ref,
        "catalog_bucket": policy.catalog_bucket,
        "catalog_prefix": policy.catalog_prefix,
    }
    catalog = (registry or default_backup_registry()).create(
        policy.backup_system,
        policy.catalog_mode,
        settings,
        dependencies or CatalogDependencies(),
    )
    return CatalogBackupRotation(
        catalog,
        rotation_days=policy.rotation_days,
        tenant_isolation=_isolation(policy.tenant_isolation),
    )


def _isolation(value: str | None) -> TenantIsolation:
    if value in ("per_tenant", "shared", "global"):
        return value  # type: ignore[return-value]
    return "per_tenant"


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "AUTHORITY_STORES",
    "BACKUP_SYSTEMS",
    "BackupPolicy",
    "ENCRYPTION_MODES",
    "PERSISTENCE_LOCATIONS",
    "CATALOG_MODES",
    "SCHEDULERS",
    "TENANT_ISOLATIONS",
    "backup_settings_dag",
    "build_rotation_from_policy",
    "evaluate_backup_policy",
    "resolve_backup_policy",
]
