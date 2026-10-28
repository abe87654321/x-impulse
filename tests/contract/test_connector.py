"""TD-02 §7 连接器契约测试 —— 11 条。

每个连接器实现必须通过才允许合入。

对应不变量 INV-C1~C6 与映射规则 A-1~A-5。
"""

from __future__ import annotations

import pytest
from kb.ingestion.contract import (
    DeletionDetection,
    IncrementalMode,
    NotFoundError,
    Scope,
    TransientError,
)

pytestmark = pytest.mark.asyncio


# ── ① 全量拉取 ──────────────────────────────────────────────


async def test_full_scan_stable(conn, sandbox):
    """INV-C2：源无变化时两次全量结果一致。"""
    await sandbox.create_document(name="a.txt")
    a = {r.external_id async for r in conn.full_scan(Scope())}
    b = {r.external_id async for r in conn.full_scan(Scope())}
    assert a == b and len(a) > 0


async def test_scope_respected(conn, sandbox):
    """O-4 连接器侧：不得返回 Scope 之外的对象。"""
    inside = await sandbox.create_document(container="allowed")
    outside = await sandbox.create_document(container="forbidden")
    got = {r.external_id async for r in conn.full_scan(Scope(include=("allowed",)))}
    assert inside.external_id in got
    assert outside.external_id not in got


# ── ② 增量识别 ──────────────────────────────────────────────


async def test_incremental_equivalent_to_full(conn, sandbox):
    """INV-C3：incremental_scan(cursor=None) 等价于 full_scan。"""
    await sandbox.create_document(name="a.txt")
    await sandbox.create_document(name="b.txt")
    full = {r.external_id async for r in conn.full_scan(Scope())}
    inc = {e.ref.external_id async for e in conn.incremental_scan(Scope(), None)}
    assert inc == full


async def test_cursor_replay_idempotent(conn, sandbox):
    """INV-C4：同一 cursor 重放两次净效果相同。"""
    if conn.capabilities().incremental == IncrementalMode.NONE:
        pytest.skip("capabilities().incremental == none")

    doc = await sandbox.create_document(name="a.txt")
    c0 = conn.checkpoint()
    await sandbox.update_document(doc)

    first = [e async for e in conn.incremental_scan(Scope(), c0)]
    conn.resume(c0)
    second = [e async for e in conn.incremental_scan(Scope(), c0)]

    assert {e.ref.external_id for e in first} == {e.ref.external_id for e in second}


# ── R-8 稳定标识 ────────────────────────────────────────────


async def test_external_id_stable_across_rename(conn, sandbox):
    """R-8：重命名后 external_id 不变。

    做不到则增量识别与将来的实体追踪全部失效 —— 这是 hard fail，不可 skip。
    """
    before = await sandbox.create_document(name="a.docx")
    await sandbox.rename(before, "b.docx")
    after = [r async for r in conn.full_scan(Scope()) if r.path and "b.docx" in r.path]
    assert len(after) == 1
    assert after[0].external_id == before.external_id


# ── ③ 删除检测 ──────────────────────────────────────────────


async def test_deletion_detected(conn, sandbox):
    """③：按声明的方式都要能发现删除。"""
    if conn.capabilities().deletion_detection == DeletionDetection.NONE:
        pytest.skip("capabilities().deletion_detection == none")

    doc = await sandbox.create_document(name="doomed.txt")
    known = {r.external_id async for r in conn.full_scan(Scope())}
    await sandbox.delete(doc)

    dels = {e.external_id async for e in conn.detect_deletions(Scope(), known)}
    assert doc.external_id in dels


async def test_not_found_is_deletion_not_failure(conn, sandbox):
    """NotFoundError 语义：对象消失是删除事件，不是失败。"""
    ref = await sandbox.create_document(name="gone.txt")
    await sandbox.delete(ref)
    with pytest.raises(NotFoundError):
        await conn.fetch_content(ref)


# ── ④ ACL 映射 ──────────────────────────────────────────────


async def test_acl_effect_is_explicit(conn, sandbox):
    """A-1：effect 无默认值，必须显式给出。"""
    if not conn.capabilities().supports_acl:
        pytest.skip("capabilities().supports_acl == False")

    ref = await sandbox.create_document()
    acl = await conn.fetch_acl(ref)
    assert all(e.effect in ("allow", "deny") for e in acl.entries)


async def test_acl_deny_preserved(conn, sandbox):
    """★A-3：源端显式拒绝必须映射为 deny。

    不得实现为"从 allow 名单中剔除" —— 二者语义不同：
    deny 会压过用户经其他组获得的 allow，剔除不会。
    """
    if not conn.capabilities().supports_acl_deny:
        pytest.skip("capabilities().supports_acl_deny == False")

    ref = await sandbox.create_document()
    await sandbox.grant(ref, group="engineering", effect="allow")
    await sandbox.grant(ref, user="alice", effect="deny")  # alice ∈ engineering

    acl = await conn.fetch_acl(ref)
    assert any(e.effect == "deny" and e.principal_id == "alice" for e in acl.entries), (
        "显式 deny 丢失 —— 很可能被实现成了从 allow 名单剔除"
    )
    assert any(e.effect == "allow" and e.principal_id == "engineering" for e in acl.entries), (
        "组的 allow 不应因为存在 deny 而被移除"
    )


async def test_inheritance_break_reported(conn, sandbox):
    """★A-2：断开继承必须置位 inheritance_broken。"""
    if not conn.capabilities().supports_inheritance_break:
        pytest.skip("capabilities().supports_inheritance_break == False")

    ref = await sandbox.create_document_with_broken_inheritance()
    acl = await conn.fetch_acl(ref)
    assert acl.inheritance_broken is True


# ── ⑤ 错误与重试 ────────────────────────────────────────────


async def test_no_internal_retry(conn, sandbox):
    """连接器不得自行重试 —— 重试是调度器职责。

    内部重试会让限速、超时预算与断点续传全部失效。
    """
    counter = sandbox.fail_next(times=1, error="503")
    with pytest.raises(TransientError):
        _ = [r async for r in conn.full_scan(Scope())]
    assert counter.count == 1, f"源被调用了 {counter.count} 次，说明连接器内部做了重试"


# ── 能力声明自洽性 ──────────────────────────────────────────


async def test_capabilities_are_self_consistent(conn):
    """能力声明不得自相矛盾。"""
    caps = conn.capabilities()
    if caps.supports_acl_deny:
        assert caps.supports_acl, "声明支持 deny 却不支持 ACL"
    if caps.supports_inheritance_break:
        assert caps.supports_acl, "声明支持断开继承却不支持 ACL"
    if caps.rate_limit_qps is not None:
        assert caps.rate_limit_qps > 0
    assert caps.max_page_size > 0
