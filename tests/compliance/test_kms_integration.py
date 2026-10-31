"""KMS 适配层与 subject_keys 持久化的集成验证。"""

from __future__ import annotations

import asyncio
import json

import pytest

from kb.compliance.crypto import KeyDestroyedError
from kb.compliance.kms import FakeKmsClient, KmsKeyManager, PostgresSubjectKeyStore
from kb.database import Database

pytestmark = pytest.mark.integration

TENANT = "t_kms_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM subject_keys WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_kms_manager_persists_ref_and_destroys_key():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'kms')", TENANT)
    try:
        client = FakeKmsClient()
        manager = KmsKeyManager(client, PostgresSubjectKeyStore())

        asyncio.run(manager.register(TENANT, "subject_a"))
        ciphertext = asyncio.run(manager.encrypt(TENANT, "subject_a", "13800138000"))
        assert asyncio.run(manager.decrypt(TENANT, "subject_a", ciphertext)) == "13800138000"

        row = db.fetch(
            "SELECT dek_ref, kek_ref, destroyed_at FROM subject_keys "
            "WHERE tenant_id = $1 AND subject_key = $2",
            TENANT,
            "subject_a",
        )[0]
        ref = json.loads(str(row["dek_ref"]))
        assert ref["key_ref"].startswith("fake-kms-")
        assert ref["wrapped"]
        assert row["kek_ref"] == "kms:v1"
        assert row["destroyed_at"] is None

        assert asyncio.run(manager.destroy(TENANT, "subject_a", "PIPL-47")) is True
        destroyed = db.fetch(
            "SELECT destroyed_at, destruction_evidence FROM subject_keys "
            "WHERE tenant_id = $1 AND subject_key = $2",
            TENANT,
            "subject_a",
        )[0]
        assert destroyed["destroyed_at"] is not None
        assert "fake-kms-evidence" in str(destroyed["destruction_evidence"])
        with pytest.raises(KeyDestroyedError):
            asyncio.run(manager.decrypt(TENANT, "subject_a", ciphertext))
    finally:
        _cleanup()
