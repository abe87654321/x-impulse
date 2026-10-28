# TD-03 权限求值设计 / Permission Evaluation Design

| 项 | 内容 |
| --- | --- |
| 编号 | TD-03 |
| 版本 | v1.1 |
| 上游 | PRD v1.6 FR-06-28（拒绝优先）· FR-06-02/03（继承与实时校验）· FR-06-29（离职失效）· FR-04-07（召回阶段过滤）· 架构 v1.4 §5.3/§5.9 |
| 目标读者 | **编码 agent** |
| 安全等级 | **本文档描述的逻辑出错不是 bug，是越权事故。** 所有分支必须有对应测试向量（§7） |
| 编号约定 | 引用的 `FR-06-20` / `FR-06-21` 是**架构文档 §8.1 的 Phase 3 提案编号**，尚未并入 PRD；其要求（委派链审计、禁止运行时降级）在本期即须遵守 |

---

## 1. 一句话规格 / The Spec in One Sentence

> 一个主体能读一个对象，当且仅当：**没有任何适用的 deny 命中，且至少有一条适用的 allow 命中。**
> 默认拒绝。

其余全部内容都是在精确定义"适用"二字。

---

## 2. 输入结构 / Inputs

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

Effect        = Literal["allow", "deny"]
PrincipalType = Literal["user", "group", "role", "everyone"]
ScopeLevel    = Literal["source", "folder", "document", "field"]
AssuranceLevel = Literal["L0", "L1", "L2", "L3"]

@dataclass(frozen=True)
class Principal:
    type: PrincipalType
    id:   str

@dataclass(frozen=True)
class DelegationContext:
    """架构 §5.3。核心逻辑只读此结构，不感知 IdP 协议。"""
    principal:       Principal | None      # 问责主体（人）；L0 时为 None
    actor:           Principal | None      # 代劳 Agent；人直接提问时为 None
    scope:           frozenset[str]        # Agent 被授予的能力范围
    assurance_level: AssuranceLevel
    expires_at:      datetime
    evidence:        dict                  # 该身份断言的来源，供审计

@dataclass(frozen=True)
class AclRow:
    """来自 acl_entries 表，见 TD-01 §4。

    ★ 注意本结构【不含】inheritance_broken。
    acl_entries 表里有这一列，但求值时它不从条目上读 —— 断开继承是
    【对象】的属性，不是【条目】的属性，只在 ObjectNode 上表达。
    两处都放会出现"条目说断了、节点说没断"的二义状态，而 evaluate()
    只看节点，于是条目侧的值被静默忽略 —— 那是一次越权。见 §3.5 与 P-5。
    """
    object_type:        ScopeLevel
    object_id:          str
    principal_type:     PrincipalType
    principal_id:       str
    effect:             Effect
    permission:         str
    scope_level:        ScopeLevel
    inherited_from:     str | None

@dataclass(frozen=True)
class ObjectNode:
    """被保护对象在继承链上的一个节点。"""
    object_type:        ScopeLevel
    object_id:          str
    parent_id:          str | None
    inheritance_broken: bool
```

---

## 3. 求值算法 / Evaluation Algorithm

```python
def evaluate(
    ctx: DelegationContext,
    chain: list[ObjectNode],          # 从对象自身到根，顺序：[self, parent, ..., root]
    acls: dict[str, list[AclRow]],    # object_id -> 该对象上直接设置的条目
    memberships: frozenset[str],      # 该 principal 展开后的全部 group/role id
    permission: str = "read",
) -> bool:
    """FR-06-28。返回 True 表示允许。"""

    # ── 步骤 1：确定适用的对象集合（处理断开继承）──────────────
    applicable_objects: list[str] = []
    for node in chain:
        applicable_objects.append(node.object_id)
        if node.inheritance_broken:
            break                     # 该节点断开继承，其祖先条目全部不适用

    # ── 步骤 2：收集适用条目 ──────────────────────────────
    #   适用 = 对象在 applicable_objects 内 且 主体匹配 且 权限匹配
    def matches_principal(row: AclRow) -> bool:
        if row.principal_type == "everyone":
            return True
        if row.principal_type == "user":
            return ctx.principal is not None and row.principal_id == ctx.principal.id
        return row.principal_id in memberships      # group / role

    applicable = [
        row
        for oid in applicable_objects
        for row in acls.get(oid, ())
        if row.permission == permission and matches_principal(row)
    ]

    # ── 步骤 3：拒绝优先 —— 任一 deny 命中即拒绝 ──────────────
    #   注意：不比较 scope_level。目录级 deny 压过文档级 allow。
    if any(row.effect == "deny" for row in applicable):
        return False

    # ── 步骤 4：至少一条 allow ────────────────────────────
    if any(row.effect == "allow" for row in applicable):
        return True

    # ── 步骤 5：默认拒绝 ─────────────────────────────────
    return False
```

### 3.5 继承链的构造 / Building the Chain（接缝规格，不可省略）

`evaluate()` 的 `chain` 参数必须由下面这个**纯函数**产出。
**这一步此前只在 §5 的伪代码里以 `chain_of(doc_id)` 出现过一次，没有规格 ——
那是本设计最危险的缺口：`acl_entries.inheritance_broken` 由同步层写入，
而 `evaluate()` 从 `ObjectNode.inheritance_broken` 读取，若无人把前者翻译成后者，
断开继承被静默忽略，父级 allow 继续生效。**

```python
def build_chain(
    hierarchy: list[tuple[str, ScopeLevel, str | None]],
    #   [(object_id, object_type, parent_id)]，顺序必须是 [自身, 父, ..., 根]
    #   来源：documents.source_id / 目录层级，由仓储层按对象类型查出
    broken_ids: frozenset[str],
    #   来源：SELECT DISTINCT object_id FROM acl_entries
    #        WHERE tenant_id = ? AND inheritance_broken = true AND object_id = ANY(?)
    #   ★ 必须对 hierarchy 里的【每一个】object_id 都查，不只是文档自身
) -> list[ObjectNode]:
    """把"哪些对象断开了继承"从条目侧搬到节点侧。"""
    return [
        ObjectNode(
            object_type=otype,
            object_id=oid,
            parent_id=parent,
            inheritance_broken=(oid in broken_ids),   # ★ 唯一的翻译点
        )
        for oid, otype, parent in hierarchy
    ]
```

```
B-1  broken_ids 必须覆盖链上【全部】对象。只查文档自身是一个典型错误：
     SharePoint 的断开继承通常设在【目录】上，漏查目录等于漏掉绝大多数真实场景。

B-2  acl_entries 中同一 object_id 的多行若 inheritance_broken 取值不一致，
     按 OR 处理（任一行为 true 即视为断开）—— 从严。
     同时这是数据质量告警项：同步层不应产生这种不一致。

B-3  hierarchy 的顺序由调用方保证，build_chain 不排序也不校验。
     顺序错了 evaluate() 会从错误的位置截断 —— 仓储层必须有自己的测试。

B-4  本函数无 IO、无全局状态，必须可单元测试。它是 I-2 的真正守门人：
     evaluate() 的测试向量直接构造 ObjectNode，【绕过了这一步】，
     所以仅靠那 26 条向量无法证明断开继承在生产中生效。
```

---

**五条必须理解的性质**

```
P-0  `acls` 这个 dict 以 object_id 单独做键，**不含 object_type**。
     因此 object_id 必须在**单个租户内全局唯一、跨对象类型不重复**。
     本项目靠 ID 前缀保证（`src_…` / `fld_…` / `doc_…`），但
     **schema 没有也无法强制它**（acl_entries.object_id 是裸 text，无 FK）。
     连接器把源系统标识映射成 object_id 时必须加类型前缀 —— 否则某源系统里
     目录与文档共用同一标识串时，目录的 deny 会被当成文档的 deny（或反之）。
     这条列为 Q-21 待决：是否改为按 (object_type, object_id) 做键。

P-1  步骤 3 不做层级比较。这是有意的：目录级 deny 优先于文档级 allow。
     若按"更具体者优先"实现，NTFS 语义会被破坏，且产生越权。

P-2  步骤 1 的 break 位置在 append 之后。断开继承的节点自身的条目仍然适用，
     只是它的祖先不适用。写成 break 在 append 之前是一个经典越权 bug。

P-3  memberships 必须是展开后的传递闭包（含嵌套组），见 §4。
     **它是 group 与 role 的并集**：`matches_principal` 对 principal_type
     为 "group" 与 "role" 的条目做同样的成员判定，不区分类型。
     前提不变量：**group id 与 role id 共用一个命名空间且不得撞号**
     （靠 `grp_…` / `role_…` 前缀保证）。撞号会让一个同名的组满足
     role 条目，构成越权。严格做法（按类型分别匹配）列为 Q-22。

P-5  断开继承只在 ObjectNode 上表达，不在 AclRow 上。
     `acl_entries.inheritance_broken` 这一列只被 §3.5 的 build_chain 读取一次，
     翻译成节点属性；此后求值过程不再看条目上的任何继承标志。
     **这是单一读取点，不要在别处再读它。**

P-4  L0 场景 ctx.principal 为 None：user 类条目永不匹配，
     只有 everyone 与 L0 服务账号所属 role 能匹配 —— 这正是
     架构 §5.8「L0 仅访问全员可见内容」的执行方式。
```

---

## 4. 组展开 / Group Expansion

```python
def expand_memberships(
    principal_id: str,
    edges: dict[str, set[str]],     # member_id -> {group_id}
    max_depth: int = 16,
) -> frozenset[str]:
    """传递闭包，带环检测与深度上限。"""
    seen: set[str] = set()
    frontier = {principal_id}
    depth = 0
    while frontier and depth < max_depth:
        nxt: set[str] = set()
        for m in frontier:
            for g in edges.get(m, ()):
                if g not in seen:
                    seen.add(g)
                    nxt.add(g)
        frontier = nxt
        depth += 1
    if frontier:
        raise PermissionModelError(
            f"group nesting exceeded depth {max_depth} for {principal_id}")
    return frozenset(seen)
```

```
G-1  环必须能终止（AD 允许组互相嵌套）。用 seen 集合天然去环。
G-2  超过深度上限抛异常，不静默截断 —— 截断会漏掉 deny，造成越权。
G-3  展开结果可缓存，TTL ≤ 60s，且必须能被 FR-06-29 离职事件立即失效。
```

---

## 5. 检索路径的应用 / Application in Retrieval

**FR-04-07 要求在召回阶段过滤，禁止先召回后遮蔽。** 后者会通过答案内容泄露无权信息。

```python
async def retrieve(ctx: DelegationContext, query: str, k: int) -> list[Chunk]:
    memberships = await get_memberships(ctx)          # §4，带缓存

    # 5a. 预过滤：把权限编译成向量库的过滤表达式
    allowed_docs = await resolve_allowed_docs(ctx, memberships)   # 见下
    if not allowed_docs:
        return []

    candidates = await hybrid_search(
        query, k=k * OVERFETCH,
        filter=doc_filter(allowed_docs),               # 下推到向量库与 BM25
    )

    # 5b. 二次实时校验（FR-06-03）—— 预过滤依赖的快照可能已过期
    result = []
    for c in candidates:
        if evaluate(ctx, build_chain(*chain_inputs(c.doc_id)), acls_for(c.doc_id), memberships):
            result.append(c)
        if len(result) >= k:
            break
    return result
```

**`resolve_allowed_docs` 的两种实现，按规模选择：**

| 规模 | 策略 |
| --- | --- |
| 文档数 ≤ 10 万 | 物化每个 principal 的可见文档集（`allow_set` 缓存），直接下推 ID 列表 |
| 文档数 > 10 万 | 下推 **容器级** 过滤（source/folder），文档级与 deny 在 5b 二次校验中处理；`OVERFETCH` 需相应放大 |

```
R-1  5b 不可省略。预过滤基于快照，权限回收后到快照刷新前存在窗口。
R-2  被 5b 拒绝的候选不计入 k，必须继续取下一个 —— 否则用户会因为
     "有无权限文档"而看到更少结果，从而推断出存在被隐藏的内容。
R-3  OVERFETCH 默认 3；若 5b 拒绝率持续 > 50%，说明预过滤太宽，需告警。
```

---

## 6. Agent 通道的权限交集 / Delegated Access

```python
def effective_scope(
    ctx: DelegationContext,
    registry: AgentRegistry | None,       # 显式注入，不得用模块级全局
    *,
    now: datetime,                        # 显式注入，不得调 datetime.now()
) -> frozenset[str]:
    """架构 §5.9：有效权限 = min(用户 ACL, Agent scope)。"""

    # 步骤 1：先查有效期。必须在 actor 分支之前 —— 见 D-5
    if ctx.expires_at <= now:
        raise AuthorizationError("delegation expired")

    # 步骤 2：人直接提问，无 Agent 收窄
    if ctx.actor is None:
        return FULL_SCOPE

    # 步骤 3：Agent 必须已登记
    if registry is None:
        raise AuthorizationError("agent registry required for delegated context")
    agent = registry.get(ctx.actor.id)
    if agent is None:
        raise AuthorizationError("unregistered agent")

    # 步骤 4：交集，不是并集
    return ctx.scope & agent.granted_scope
```

```
D-1  Agent 不能让用户获得更多权限（显然）。
D-2  Agent 也不应自动继承用户的全部权限 —— 只管销售话术的 Agent
     不该因为调用者是 HR 总监就能读全员薪资。
D-3  assurance_level 与数据分级联动：
     L0 → 仅 audience='customer_safe' 且分级 ≤2 的内容
     L1 → 用户显式授权范围内
     L2/L3 → 用户 ACL ∩ Agent scope
D-4  禁止运行时隐式降级（架构 §5.11）。校验失败即拒绝，
     不得回退到更弱的保证等级 —— 那是一次静默的权限提升。

D-5  有效期检查必须排在 actor 分支之前。写成"先判 actor is None 再判过期"
     会让人类直接提问的过期令牌一路放行到 FULL_SCOPE —— 这是越权。
     对应测试 6c：该用例的 ctx.actor 恰为 None。

D-6  registry 与 now 一律显式传入，禁止模块级全局与 datetime.now()。
     全局注册表让求值结果依赖进程历史，无法测试也无法审计；
     隐式取当前时间则使"过期即拒绝"这条性质不可复现。
```

---

## 7. 测试向量 / Test Vectors

**以下每一条都必须有对应的自动化测试。** 这些是 FR-06 验收「≥50 组越权用例」的核心六类。

> 下表为**用例记法，非可运行代码** —— 实现时转为 `pytest.mark.parametrize` 的参数表。

```text
# 记法：chain 从对象自身到根
# T = 允许，F = 拒绝

CASES = [
# ── 类 1：基础 ──────────────────────────────────────────
("1a 无条目即拒绝",
 chain=[doc], acls={}, memberships=set(), expect=False),

("1b 直接 allow",
 chain=[doc], acls={doc: [allow_user("alice")]}, memberships=set(),
 principal="alice", expect=True),

("1c everyone allow",
 chain=[doc], acls={doc: [allow_everyone()]}, memberships=set(),
 principal="alice", expect=True),

# ── 类 2：拒绝优先（FR-06-28 核心）────────────────────────
("2a 同级 deny 压过 allow",
 chain=[doc], acls={doc: [allow_user("alice"), deny_user("alice")]},
 principal="alice", expect=False),

("2b ★目录级 deny 压过文档级 allow",
 chain=[doc, folder], acls={doc: [allow_user("alice")], folder: [deny_user("alice")]},
 principal="alice", expect=False),

("2c ★用户同属被允许组与被拒绝组",
 chain=[doc], acls={doc: [allow_group("engineering"), deny_group("contractors")]},
 principal="alice", memberships={"engineering", "contractors"}, expect=False),

("2d 源级 deny 压过文档级 allow",
 chain=[doc, folder, source],
 acls={doc: [allow_user("alice")], source: [deny_user("alice")]},
 principal="alice", expect=False),

# ── 类 3：继承与断开（SharePoint 语义）─────────────────────
("3a 继承 allow",
 chain=[doc, folder], acls={folder: [allow_user("alice")]},
 principal="alice", expect=True),

("3b ★断开继承后父级 allow 失效",
 chain=[doc_broken, folder], acls={folder: [allow_user("alice")]},
 principal="alice", expect=False),

("3c ★断开继承后父级 deny 也失效，本级 allow 生效",
 chain=[doc_broken, folder],
 acls={doc_broken: [allow_user("alice")], folder: [deny_user("alice")]},
 principal="alice", expect=True),

("3d ★断开节点自身的条目仍适用（P-2 回归）",
 chain=[folder_broken, source],
 acls={folder_broken: [deny_user("alice")], source: [allow_user("alice")]},
 principal="alice", expect=False),

# ── 类 4：组嵌套 ────────────────────────────────────────
("4a 嵌套组继承 allow",
 chain=[doc], acls={doc: [allow_group("all-staff")]},
 principal="alice", memberships={"engineering", "all-staff"}, expect=True),

("4b ★嵌套组带来的 deny 同样生效",
 chain=[doc], acls={doc: [allow_everyone(), deny_group("suspended")]},
 principal="alice", memberships={"suspended"}, expect=False),

("4c 组环不导致死循环", edges_with_cycle=True, expect_no_hang=True),

("4d 嵌套深度超限抛异常而非静默截断",
 nesting_depth=20, expect_raises=PermissionModelError),

# ── 类 5：租户与委派 ────────────────────────────────────
("5a ★跨租户不可见",
 chain=[doc_tenant_b], principal="alice@tenant-a", expect=False),

("5b Agent scope 交集收窄",
 ctx=delegated(principal="hr-director", agent="sales-copilot",
               agent_scope={"kb.search.sales"}),
 chain=[doc_payroll], acls={doc_payroll: [allow_user("hr-director")]},
 expect=False),                       # 用户有权，但 Agent 无此 scope

("5c L0 无 principal 时 user 条目不匹配",
 ctx=l0_service_account(),
 chain=[doc], acls={doc: [allow_user("alice")]}, expect=False),

("5d L0 可读 everyone 条目",
 ctx=l0_service_account(),
 chain=[doc], acls={doc: [allow_everyone()]}, expect=True),

# ── 类 6：时间窗（FR-06-03 / FR-06-29）────────────────────
("6a ★权限回收后缓存内命中仍被二次校验拒绝",
 revoke_after_prefilter=True, expect=False),

("6b ★离职事件后立即失效，不等下轮 ACL 同步",
 offboard_event=True, expect=False),

("6c 委派令牌过期即拒绝",
 ctx=delegated(expires_at=past()), expect_raises=AuthorizationError),
]
```

**属性测试（补充枚举用例的盲区）**

```python
@given(acl_sets(), principals(), chains())
def test_deny_always_wins(acls, principal, chain):
    """任何存在适用 deny 的配置，结果必为 False。"""
    if has_applicable_deny(acls, principal, chain):
        assert evaluate(ctx_of(principal), chain, acls, memberships_of(principal)) is False

@given(acl_sets(), principals(), chains())
def test_default_deny(acls, principal, chain):
    """无任何适用 allow 时必为 False。"""
    if not has_applicable_allow(acls, principal, chain):
        assert evaluate(ctx_of(principal), chain, acls, memberships_of(principal)) is False

@given(chains())
def test_broken_inheritance_truncates(chain):
    """断开点之上的对象一律不出现在适用集合中。"""
    applicable = applicable_objects(chain)
    idx = first_broken_index(chain)
    if idx is not None:
        assert applicable == [n.object_id for n in chain[: idx + 1]]
```

---

## 8. 审计要求 / Audit

每一次求值为 False 的**拒绝**都必须可解释 —— 合规检查时要能回答"为什么这个人看不到"。

```python
@dataclass(frozen=True)
class Decision:
    allowed:           bool
    reason:            Literal["deny_hit", "no_allow", "scope_excluded",
                               "tenant_mismatch", "assurance_too_low"]
    decisive_acl_id:   str | None        # deny_hit 时指向那条 deny
    applicable_objects: tuple[str, ...]
    evaluated_at:      datetime
```

```
AU-1 越权拦截（deny_hit）写入 audit_log，category='acl'。
AU-2 记录完整委派链：principal / actor / delegation_chain / assurance_level。
     否则"谁问了什么"退化成"某个 agent 问了什么"，等保 8.1.4.3 不认。
AU-3 5b 二次校验拒绝的比例是监控指标（FR-15-05）；
     持续升高说明 ACL 同步滞后。
AU-4 Decision 不返回给终端用户 —— 告知"因为规则 X 被拒"本身是信息泄露。
     用户侧统一提示"无相关内容或无权访问"。
```

---

## 9. 性能 / Performance

| 项 | 目标 | 手段 |
| --- | --- | --- |
| 单次 `evaluate` | ≤ 1ms | 纯内存，无 IO；ACL 与 chain 由调用方批量预取 |
| 组展开 | ≤ 5ms（缓存未命中） | 展开结果缓存 TTL ≤60s，离职事件立即失效 |
| 预过滤解析 | ≤ 50ms | `allow_set` 物化缓存；超 10 万文档改容器级下推 |
| 二次校验 | 与候选数线性 | `OVERFETCH=3`，通常 ≤30 次求值 |

```
PF-1 evaluate 必须是纯函数（无 IO、无全局状态），便于属性测试与并行。
PF-2 deny 查询走 TD-01 的 ix_acl_deny 部分索引。
PF-3 缓存键必须含 tenant_id 与 memberships 版本号，避免跨租户或过期命中。
```

---

## 10. 交叉引用 / Cross-references

| 本文档 | 上游 |
| --- | --- |
| §3 求值算法 | FR-06-28 |
| §3 P-1 目录级 deny | FR-01-20、TD-02 §4 A-3 |
| §5 召回阶段过滤 | FR-04-07 |
| §5 二次校验 | FR-06-03 |
| §6 权限交集 | 架构 §5.9 |
| §6 D-4 禁止降级 | 架构 §5.11、FR-06-21 |
| §7 测试向量 | FR-06 验收 |
| §8 审计 | FR-06-20、FR-09、FR-15-05 |
| ACL 表结构 | TD-01 §4 |
| 连接器如何产出 ACL | TD-02 §4 |
