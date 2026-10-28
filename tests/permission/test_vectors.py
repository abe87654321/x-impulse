"""TD-03 §7 测试向量 —— 28 条用例 + 3 个属性测试。

  A 求值表 18 条 · B 组展开 2 条 · B·5 继承链构造 5 条 · C 委派 3 条
  D 仓储层 3 条（integration）· E 属性测试 3 条

`pytest -m "not integration"` 应跑 31 条（A18 + B2 + B·5×5 + C3 + E3），
D 的 3 条被 deselect。

★ 标记的 10 条是 FR-06 验收必过项。

先跑这套（会红），再写 kb/identity/permissions.py 让它变绿。
求值函数必须是纯函数：无 IO、无全局状态。
"""

from __future__ import annotations

import pytest
from builders import (
    DOC,
    FOLDER,
    SOURCE,
    acls,
    allow_everyone,
    allow_group,
    allow_user,
    chain_doc,
    ctx_delegated,
    ctx_expired,
    ctx_l0,
    ctx_user,
    deny_group,
    deny_user,
    hierarchy_doc,
)
from kb.identity.permissions import (
    AuthorizationError,
    PermissionModelError,
    build_chain,
    effective_scope,
    evaluate,
    expand_memberships,
)

# ════════════════════════════════════════════════════════════
# A. 纯求值表 —— 18 条，直接调 evaluate()
# ════════════════════════════════════════════════════════════

# fmt: off
TABLE = [
    # (id, ctx, chain, acls, memberships, expect)

    # ── 类 1：基础 ────────────────────────────────────────
    ("1a 无条目即拒绝",
     ctx_user("alice"), chain_doc(), acls(), set(), False),

    ("1b 直接 allow",
     ctx_user("alice"), chain_doc(), acls(allow_user("alice")), set(), True),

    ("1c everyone allow",
     ctx_user("alice"), chain_doc(), acls(allow_everyone()), set(), True),

    # ── 类 2：拒绝优先（FR-06-28 核心）────────────────────
    ("2a 同级 deny 压过 allow",
     ctx_user("alice"), chain_doc(),
     acls(allow_user("alice"), deny_user("alice")), set(), False),

    ("★2b 目录级 deny 压过文档级 allow",
     ctx_user("alice"), chain_doc(),
     acls(allow_user("alice"), deny_user("alice", on=FOLDER, otype="folder")),
     set(), False),

    ("★2c 用户同属被允许组与被拒绝组",
     ctx_user("alice"), chain_doc(),
     acls(allow_group("engineering"), deny_group("contractors")),
     {"engineering", "contractors"}, False),

    ("2d 源级 deny 压过文档级 allow",
     ctx_user("alice"), chain_doc(),
     acls(allow_user("alice"), deny_user("alice", on=SOURCE, otype="source")),
     set(), False),

    # ── 类 3：继承与断开（SharePoint 语义）────────────────
    ("3a 继承 allow",
     ctx_user("alice"), chain_doc(),
     acls(allow_user("alice", on=FOLDER, otype="folder")), set(), True),

    ("★3b 断开继承后父级 allow 失效",
     ctx_user("alice"), chain_doc(broken_at="document"),
     acls(allow_user("alice", on=FOLDER, otype="folder")), set(), False),

    ("★3c 断开继承后父级 deny 也失效，本级 allow 生效",
     ctx_user("alice"), chain_doc(broken_at="document"),
     acls(allow_user("alice"), deny_user("alice", on=FOLDER, otype="folder")),
     set(), True),

    ("★3d 断开节点自身的条目仍适用（P-2 回归）",
     ctx_user("alice"), chain_doc(broken_at="folder"),
     acls(deny_user("alice", on=FOLDER, otype="folder"),
          allow_user("alice", on=SOURCE, otype="source")),
     set(), False),

    # ── 类 4：组嵌套（求值侧）──────────────────────────────
    ("4a 嵌套组继承 allow",
     ctx_user("alice"), chain_doc(), acls(allow_group("all-staff")),
     {"engineering", "all-staff"}, True),

    ("★4b 嵌套组带来的 deny 同样生效",
     ctx_user("alice"), chain_doc(),
     acls(allow_everyone(), deny_group("suspended")), {"suspended"}, False),

    # ── 类 5：L0 与主体缺失 ───────────────────────────────
    ("★5c L0 无 principal 时 user 条目不匹配",
     ctx_l0(), chain_doc(), acls(allow_user("alice")), set(), False),

    ("5d L0 可读 everyone 条目",
     ctx_l0(), chain_doc(), acls(allow_everyone()), set(), True),

    # ── 补充：适用性边界 ──────────────────────────────────
    ("x1 permission 不匹配则不适用（write 的 allow 不给 read 放行）",
     ctx_user("alice"), chain_doc(),
     acls(allow_user("alice", permission="write")), set(), False),

    ("x2 他人的 allow 不适用于本人",
     ctx_user("alice"), chain_doc(), acls(allow_user("bob")), set(), False),

    ("x3 未加入的组的 allow 不适用",
     ctx_user("alice"), chain_doc(), acls(allow_group("finance")), set(), False),
]
# fmt: on


@pytest.mark.parametrize(
    "case_id,ctx,chain,acl_map,memberships,expect",
    TABLE,
    ids=[c[0] for c in TABLE],
)
def test_evaluate_table(case_id, ctx, chain, acl_map, memberships, expect):
    assert evaluate(ctx, chain, acl_map, frozenset(memberships)) is expect


# ════════════════════════════════════════════════════════════
# B. 组展开 —— 4c / 4d
# ════════════════════════════════════════════════════════════


def test_4c_group_cycle_terminates():
    """4c：AD 允许组互相嵌套，环不得导致死循环。"""
    edges = {"alice": {"g1"}, "g1": {"g2"}, "g2": {"g1", "g3"}, "g3": {"g1"}}
    got = expand_memberships("alice", edges)
    assert got == frozenset({"g1", "g2", "g3"})


def test_4d_depth_limit_raises_not_truncates():
    """4d：超深度必须抛异常，不得静默截断 —— 截断会漏掉 deny。"""
    edges = {"alice": {"g0"}}
    for i in range(40):
        edges[f"g{i}"] = {f"g{i + 1}"}
    with pytest.raises(PermissionModelError):
        expand_memberships("alice", edges, max_depth=16)


# ════════════════════════════════════════════════════════════
# B·5. 继承链构造 —— TD-03 §3.5 的加载接缝
#
# ★ 这一节存在的理由：A 节的向量直接构造 ObjectNode，【绕过了加载层】。
#   acl_entries.inheritance_broken 由同步层写入，evaluate() 从
#   ObjectNode.inheritance_broken 读取；没人把前者翻译成后者时，
#   断开继承被静默忽略、父级 allow 继续生效 —— 那是越权，而 A 节抓不到。
# ════════════════════════════════════════════════════════════


def test_bc1_translates_broken_ids_onto_nodes():
    """B-1：broken_ids 里的 object_id，对应节点的 inheritance_broken 必须为 True。"""
    chain = build_chain(hierarchy_doc(), frozenset({FOLDER}))
    got = {n.object_id: n.inheritance_broken for n in chain}
    assert got == {DOC: False, FOLDER: True, SOURCE: False}


def test_bc2_empty_broken_ids_means_nothing_broken():
    chain = build_chain(hierarchy_doc(), frozenset())
    assert all(n.inheritance_broken is False for n in chain)


def test_bc3_order_is_preserved_self_to_root():
    """B-3：顺序必须原样保留。顺序错了 evaluate() 会从错误位置截断。"""
    chain = build_chain(hierarchy_doc(), frozenset())
    assert [n.object_id for n in chain] == [DOC, FOLDER, SOURCE]
    assert [n.object_type for n in chain] == ["document", "folder", "source"]
    assert chain[0].parent_id == FOLDER
    assert chain[-1].parent_id is None


def test_bc4_folder_break_from_acl_rows_kills_parent_allow():
    """★ 本套件最关键的一条回归 —— I-2 在【加载层】是否真的生效。

    场景：源级给了 alice allow，目录断开了继承（SharePoint 的典型配置）。
    正确行为：目录以上不可达 → 无适用 allow → 拒绝。

    若实现漏了把 acl_entries.inheritance_broken 翻译到节点上，
    这里会返回 True —— 即越权。A 节的任何一条向量都发现不了它。
    """
    chain = build_chain(hierarchy_doc(), frozenset({FOLDER}))
    acl_map = acls(allow_user("alice", on=SOURCE, otype="source"))
    assert evaluate(ctx_user("alice"), chain, acl_map, frozenset()) is False


def test_bc5_must_query_whole_chain_not_just_the_document():
    """B-1 的反面：只按文档自身查 broken_ids 是典型实现错误。

    断开继承通常设在目录上。仓储层若只对 doc_id 查 acl_entries，
    broken_ids 会是空集，上一条用例随即变红 —— 那正是它该发现的。
    """
    assert build_chain(hierarchy_doc(), frozenset({DOC}))[1].inheritance_broken is False
    assert build_chain(hierarchy_doc(), frozenset({FOLDER}))[1].inheritance_broken is True


# ════════════════════════════════════════════════════════════
# C. 委派 —— 5b / 6c
# ════════════════════════════════════════════════════════════


def test_5b_agent_scope_intersects_not_unions(agent_registry, frozen_now):
    """5b：用户有权，但 Agent 无此 scope → 收窄为拒绝。"""
    agent_registry.register("sales-copilot", granted_scope={"kb.search.sales"})
    ctx = ctx_delegated("hr-director", "sales-copilot", {"kb.search.sales", "kb.search.hr"})
    scope = effective_scope(ctx, agent_registry, now=frozen_now)
    assert scope == frozenset({"kb.search.sales"})
    assert "kb.search.hr" not in scope


def test_5b_unregistered_agent_rejected(agent_registry, frozen_now):
    ctx = ctx_delegated("alice", "unknown-agent", {"kb.search"})
    with pytest.raises(AuthorizationError):
        effective_scope(ctx, agent_registry, now=frozen_now)


def test_6c_expired_delegation_rejected(frozen_now):
    """6c：委派令牌过期即拒绝。

    ★ 本用例的 ctx.actor 为 None（人直接提问）。若实现把
    `if ctx.actor is None: return FULL_SCOPE` 写在有效期检查之前，
    过期令牌会被放行 —— 见 TD-03 D-5。
    registry 传 None：到不了用 registry 那一步才是正确实现。
    """
    with pytest.raises(AuthorizationError):
        effective_scope(ctx_expired("alice"), None, now=frozen_now)


# ════════════════════════════════════════════════════════════
# D. 需要仓储层 —— 5a / 6a / 6b
# 这三条不是 evaluate() 的性质，而是加载层与失效链路的性质。
# ════════════════════════════════════════════════════════════


@pytest.mark.integration
def test_5a_cross_tenant_invisible(acl_repo):
    """★5a：跨租户不可见。

    evaluate() 本身不接收 tenant —— 隔离由加载层保证：
    acl_repo 取出的行必须已按 tenant_id 过滤。
    """
    rows = acl_repo.load_for(tenant_id="tenant-a", object_ids=[DOC])
    assert all(r.tenant_id == "tenant-a" for r in rows)
    assert acl_repo.load_for(tenant_id="tenant-a", object_ids=["doc-of-tenant-b"]) == []


@pytest.mark.integration
def test_6a_revocation_caught_by_second_check(retrieval, acl_repo):
    """★6a：预过滤命中后，权限已回收 → 二次校验必须拒绝（TD-03 R-1）。"""
    hits = retrieval.prefilter(ctx_user("alice"), query="报销标准")
    assert hits, "前置条件：预过滤应有命中"
    acl_repo.revoke(principal_id="alice", object_id=hits[0].doc_id)
    survived = retrieval.recheck(ctx_user("alice"), hits)
    assert hits[0] not in survived


@pytest.mark.integration
def test_6b_offboarding_takes_effect_immediately(identity, retrieval):
    """★6b：离职事件后立即失效，不等下一轮 ACL 同步（FR-06-29）。"""
    assert retrieval.search(ctx_user("alice"), "报销标准")
    identity.offboard("alice")
    assert retrieval.search(ctx_user("alice"), "报销标准") == []


# ════════════════════════════════════════════════════════════
# E. 属性测试 —— 覆盖枚举用例的盲区
# ════════════════════════════════════════════════════════════

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

principals = st.sampled_from(["alice", "bob", "carol"])
groups = st.sets(st.sampled_from(["g1", "g2", "g3"]), max_size=3)
effects = st.sampled_from(["allow", "deny"])
objects = st.sampled_from([DOC, FOLDER, SOURCE])
otypes = {DOC: "document", FOLDER: "folder", SOURCE: "source"}


@st.composite
def acl_maps(draw):  # noqa: D401
    n = draw(st.integers(min_value=0, max_value=6))
    rows = []
    for _ in range(n):
        obj = draw(objects)
        eff = draw(effects)
        if draw(st.booleans()):
            rows.append(
                (allow_user if eff == "allow" else deny_user)(
                    draw(principals), on=obj, otype=otypes[obj]
                )
            )
        else:
            rows.append(
                (allow_group if eff == "allow" else deny_group)(
                    draw(st.sampled_from(["g1", "g2", "g3"])), on=obj, otype=otypes[obj]
                )
            )
    return acls(*rows)


def _applicable(chain, acl_map, principal_id, memberships):
    out, objs = [], []
    for nd in chain:
        objs.append(nd.object_id)
        if nd.inheritance_broken:
            break
    for oid in objs:
        for r in acl_map.get(oid, ()):
            if r.principal_type == "everyone":
                out.append(r)
            elif r.principal_type == "user" and r.principal_id == principal_id:
                out.append(r)
            elif r.principal_type in ("group", "role") and r.principal_id in memberships:
                out.append(r)
    return out


@given(acl_map=acl_maps(), pid=principals, memb=groups)
def test_property_deny_always_wins(acl_map, pid, memb):
    """存在任一适用 deny 时，结果必为 False。"""
    chain = chain_doc()
    app = _applicable(chain, acl_map, pid, memb)
    if any(r.effect == "deny" for r in app):
        assert evaluate(ctx_user(pid), chain, acl_map, frozenset(memb)) is False


@given(acl_map=acl_maps(), pid=principals, memb=groups)
def test_property_default_deny(acl_map, pid, memb):
    """无任何适用 allow 时必为 False。"""
    chain = chain_doc()
    app = _applicable(chain, acl_map, pid, memb)
    if not any(r.effect == "allow" for r in app):
        assert evaluate(ctx_user(pid), chain, acl_map, frozenset(memb)) is False


@given(broken=st.sampled_from([None, "document", "folder", "source"]))
def test_property_broken_inheritance_truncates(broken):
    """断开点之上的对象一律不出现在适用集合中（P-2）。"""
    chain = chain_doc(broken_at=broken)
    marker = "zzz-unreachable"
    # 只在根上放 allow；若断开点在根之下，则不可达
    acl_map = acls(allow_user(marker, on=SOURCE, otype="source"))
    got = evaluate(ctx_user(marker), chain, acl_map, frozenset())
    expected_reachable = broken in (None, "source")
    assert got is expected_reachable
