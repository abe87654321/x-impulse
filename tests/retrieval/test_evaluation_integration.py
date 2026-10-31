"""FR-04-17：从客户评测集读取基线输入。"""

from __future__ import annotations

import pytest

from kb.database import Database
from kb.retrieval.evaluation import load_eval_cases

pytestmark = pytest.mark.integration

TENANT = "t_eval_it"
SET_ID = "eset_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM eval_cases WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM eval_sets WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_loads_expected_chunks_from_eval_set():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'eval')", TENANT)
    db.execute(
        "INSERT INTO eval_sets (set_id, tenant_id, name, version) VALUES ($1,$2,'基线','v1')",
        SET_ID,
        TENANT,
    )
    db.execute(
        "INSERT INTO eval_cases "
        "(case_id, tenant_id, set_id, question, expected_chunk_ids, pii_redacted) "
        "VALUES ('case_a',$1,$2,'报销时限？',ARRAY['chk_1','chk_2'],true)",
        TENANT,
        SET_ID,
    )
    db.execute(
        "INSERT INTO eval_cases "
        "(case_id, tenant_id, set_id, question, expected_chunk_ids, pii_redacted) "
        "VALUES ('case_b',$1,$2,'年假几天？',ARRAY['chk_3'],true)",
        TENANT,
        SET_ID,
    )
    try:
        cases = load_eval_cases(db, tenant_id=TENANT, set_id=SET_ID)

        assert [case.case_id for case in cases] == ["case_a", "case_b"]
        assert cases[0].expected_chunk_ids == frozenset({"chk_1", "chk_2"})
        assert cases[1].expected_chunk_ids == frozenset({"chk_3"})
    finally:
        _cleanup()
