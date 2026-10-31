"""备份演练的 KMS 选择：默认只允许假 KMS，真实 Vault 必须显式选择。"""

from __future__ import annotations

import pytest

from kb.backup_drill import build_drill_kms
from kb.compliance.kms import FakeKmsClient
from kb.compliance.kms_vault import KmsConfigError, VaultTransitKmsClient


def test_default_kms_is_fake():
    assert isinstance(build_drill_kms(), FakeKmsClient)


def test_vault_drill_requires_credentials():
    with pytest.raises(KmsConfigError):
        build_drill_kms("vault", {})


def test_vault_drill_returns_vault_adapter():
    client = build_drill_kms(
        "vault",
        {
            "KB_KMS_PROVIDER": "vault",
            "KB_VAULT_ADDR": "http://vault:8200",
            "KB_VAULT_TOKEN": "s.test",
        },
    )

    assert isinstance(client, VaultTransitKmsClient)


def test_unknown_kms_is_rejected():
    with pytest.raises(KmsConfigError):
        build_drill_kms("software")
