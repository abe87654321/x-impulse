"""备份策略 6 项：推荐默认、依赖求值与安全拒绝。"""

from __future__ import annotations

import pytest

from kb.compliance.backup import UnconfiguredBackupRotation
from kb.compliance.backup_domain import CatalogBackupRotation
from kb.compliance.backup_policy import (
    BackupPolicy,
    build_rotation_from_policy,
    evaluate_backup_policy,
    resolve_backup_policy,
)
from kb.governance.settings_dag import SettingsError

RECOMMENDED = {
    "backup_system": "database_pitr",
    "rotation_days": 90,
    "persistence_location": "external_catalog",
    "scheduler": "k8s_cronjob",
    "tenant_isolation": "per_tenant",
    "catalog_mode": "manifest",
    "manifest_uri": "file:///var/lib/kb/backups/manifest.jsonl",
    "backup_encryption": "subject_dek",
    "authority_store": "kms_and_external_db",
}


def test_missing_settings_use_recommended_defaults():
    resolved = evaluate_backup_policy({})

    assert resolved.ready is True
    assert resolved.values == RECOMMENDED
    assert all(resolved.sources[key] == "recommended" for key in RECOMMENDED)
    assert resolved.sources["catalog_endpoint"] == "not_applicable"


def test_admin_overrides_are_applied():
    resolved = evaluate_backup_policy({"rotation_days": 30, "scheduler": "external_cron"})

    assert resolved.values["rotation_days"] == 30
    assert resolved.values["scheduler"] == "external_cron"
    assert resolved.sources["rotation_days"] == "admin"
    assert resolved.sources["persistence_location"] == "recommended"


def test_disabling_backup_marks_dependent_settings_not_applicable():
    resolved = evaluate_backup_policy({"backup_system": "none"})

    assert resolved.ready is True
    assert resolved.sources["rotation_days"] == "not_applicable"
    assert resolved.sources["persistence_location"] == "not_applicable"
    assert resolved.sources["scheduler"] == "not_applicable"


def test_missing_scheduler_is_rejected_when_backup_enabled():
    resolved = evaluate_backup_policy({"scheduler": "none"})

    assert resolved.ready is False
    assert any("调度器" in error for error in resolved.errors)


def test_missing_persistence_is_rejected_when_backup_enabled():
    resolved = evaluate_backup_policy({"persistence_location": "none"})

    assert resolved.ready is False
    assert any("持久化位置" in error for error in resolved.errors)


def test_db_only_authority_is_forbidden():
    resolved = evaluate_backup_policy({"authority_store": "db_only"})

    assert resolved.ready is False
    assert any("回滚删除状态" in error for error in resolved.errors)


def test_unencrypted_or_single_key_backup_is_forbidden():
    for mode in ("none", "single_long_lived"):
        resolved = evaluate_backup_policy({"backup_encryption": mode})
        assert resolved.ready is False
        assert any("加密擦除" in error or "未加密" in error for error in resolved.errors)


def test_rotation_period_range_is_checked():
    resolved = evaluate_backup_policy({"rotation_days": 5000})

    assert resolved.ready is False
    assert any("1~3650" in error for error in resolved.errors)


def test_resolve_returns_typed_policy():
    policy = resolve_backup_policy({"backup_system": "none", "rotation_days": 30})

    assert policy == BackupPolicy(
        backup_system="none",
        rotation_days=None,
        persistence_location=None,
        scheduler=None,
        tenant_isolation=None,
        catalog_mode=None,
        manifest_uri=None,
        catalog_endpoint=None,
        catalog_credential_ref=None,
        backup_encryption="subject_dek",
        authority_store="kms_and_external_db",
    )


def test_invalid_policy_raises_before_use():
    with pytest.raises(SettingsError):
        resolve_backup_policy({"authority_store": "db_only"})


def test_rotation_builder_honors_policy():
    policy = resolve_backup_policy({})
    rotation = build_rotation_from_policy(policy)
    assert isinstance(rotation, CatalogBackupRotation)
    assert rotation.rotation_days == 90

    disabled = resolve_backup_policy({"backup_system": "none"})
    assert isinstance(build_rotation_from_policy(disabled), UnconfiguredBackupRotation)


def test_http_catalog_mode_requires_endpoint_and_credential_ref():
    resolved = evaluate_backup_policy({"catalog_mode": "http_api"})

    assert resolved.ready is False
    assert any("catalog_endpoint" in error for error in resolved.errors)
    assert any("catalog_credential_ref" in error for error in resolved.errors)


def test_http_endpoint_scheme_is_validated():
    resolved = evaluate_backup_policy(
        {
            "catalog_mode": "http_api",
            "catalog_endpoint": "ftp://backup.local",
            "catalog_credential_ref": "kms://backup-token",
        }
    )

    assert resolved.ready is False
    assert any("http://" in error for error in resolved.errors)
