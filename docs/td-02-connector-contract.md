# TD-02 连接器接口契约 / Connector Interface Contract

| 项 | 内容 |
| --- | --- |
| 编号 | TD-02 |
| 版本 | v1.1 |
| 上游 | PRD v1.8 FR-01-19（六项契约）· FR-01-20（拒绝规则）· FR-01-11/12（删除传播、ACL 同步）· 决策 A-12 |
| 目标读者 | **编码 agent** |
| 语言 | Python 3.12+ typing（与 pyproject.toml 的 requires-python 一致；其他语言按同构映射） |

> **本契约由最复杂的连接器定义，不由最简单的定义。** 接口必须能容纳 SharePoint 的断开继承与 NTFS 的显式 Deny；本地文件夹只是其退化情形。反过来做，接口会被带偏，后续每加一个源都要改接口与全部已有实现。

---

## 1. 六项能力 / The Six Capabilities

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

class Connector(ABC):
    """每个知识源一个实现。所有方法必须幂等且可重入。"""

    # ── 能力声明（先于一切调用）───────────────────────────
    @abstractmethod
    def capabilities(self) -> "Capabilities": ...

    # ── ① 全量拉取 ────────────────────────────────────
    @abstractmethod
    async def full_scan(self, scope: "Scope") -> AsyncIterator["DocumentRef"]: ...

    # ── ② 增量识别 ────────────────────────────────────
    @abstractmethod
    async def incremental_scan(
        self, scope: "Scope", cursor: "Cursor | None"
    ) -> AsyncIterator["ChangeEvent"]: ...

    # ── ③ 删除检测 ────────────────────────────────────
    @abstractmethod
    async def detect_deletions(
        self, scope: "Scope", known_ids: set[str]
    ) -> AsyncIterator["DeletionEvent"]: ...

    # ── ④ ACL 读取与映射 ──────────────────────────────
    @abstractmethod
    async def fetch_acl(self, ref: "DocumentRef") -> "AclSet": ...

    # ── ⑤ 内容与元数据抽取 ────────────────────────────
    @abstractmethod
    async def fetch_content(self, ref: "DocumentRef") -> "RawDocument": ...

    # ── ⑥ 断点续传 ────────────────────────────────────
    @abstractmethod
    def checkpoint(self) -> "Cursor": ...
    @abstractmethod
    def resume(self, cursor: "Cursor") -> None: ...
```

**`checkpoint` / `resume` 的语义（v1.0 未定义，契约测试却在调用它）**

```
S-1  checkpoint() 返回"此刻之后尚未处理"的位置；不改变连接器状态，可随时调用。
S-2  resume(cursor) 只设置内部位置，【不触发任何拉取】。
     调用后下一次 incremental_scan(scope, cursor) 从该位置继续。
S-3  resume 必须幂等：连续两次 resume(同一 cursor) 与一次等效。
S-4  cursor.source_id 与本连接器实例的 source_id 不一致 → 抛 PermanentError。
     跨源复用游标是调度器 bug，不得静默忽略。
S-5  cursor.version 高于连接器支持的版本 → 抛 PermanentError；低于则必须
     能读（向后兼容），读不了也抛 PermanentError。
     **任何一种都不得静默降级为全量** —— 静默全量会在大源上造成突发的
     限速击穿与成本尖峰，并掩盖真正的问题。
S-6  capabilities().incremental == NONE 时两者的行为按 U-2 处理。
```

**调用了未声明的能力会怎样（v1.0 未定义）**

```
U-1  能力声明是契约。核心【不应】调用 capabilities() 未声明的能力。
U-2  但核心仍可能因配置漂移或版本不匹配调到。此时连接器必须抛
     PermanentError 且消息里点名该能力，**不得返回空值**。
     理由：fetch_acl 返回空 AclSet 与"该对象确实没有任何条目"不可区分，
     而后者在默认拒绝语义下表现为"文档对所有人不可见" —— 一次静默的
     可用性故障，排查成本极高。显式失败才能被 FR-15 的告警抓到。
U-3  不新增 UnsupportedError。PermanentError 的语义（该对象/该操作无法
     处理，记录并跳过，不阻塞同步）已覆盖，再加一类只会分散处理路径。
```

**不变量 / Invariants（每条都应有对应的契约测试）**

```
INV-C1  full_scan 与 incremental_scan 产出的 DocumentRef.external_id 在同一 source 内唯一且稳定
INV-C2  连续两次 full_scan 之间源系统无变化时，产出的 external_id 集合必须相同
INV-C3  incremental_scan(cursor=None) 等价于 full_scan 的变更表示
INV-C4  同一 cursor 重放两次，产生的净效果相同（幂等）
INV-C5  fetch_acl 对同一 ref 的两次调用结果一致（除非源端权限确实变了）
INV-C6  任何方法抛出 TransientError 后重试，不得产生重复文档或丢失变更
```

---

## 2. 能力声明 / Capability Declaration

**不是所有源都能做到六项。** 接口必须能表达"做不到"，否则要么接口被削弱到最低公分母，要么核心对做不到的源发出不可能的调用。

```python
class DeletionDetection(str, Enum):
    EVENT      = "event"       # 源系统推送删除事件（最优）
    TOMBSTONE  = "tombstone"   # 源系统保留墓碑记录
    ENUMERATE  = "enumerate"   # 需列举源端全量 ID 与本地对比（贵）
    NONE       = "none"        # 做不到 —— 核心须定期全量重建兜底

class IncrementalMode(str, Enum):
    CURSOR      = "cursor"       # 源提供游标/变更令牌（最优）
    TIMESTAMP   = "timestamp"    # 按修改时间过滤
    VERSION     = "version"      # 按版本号
    NONE        = "none"         # 只能全量

@dataclass(frozen=True)
class Capabilities:
    incremental:        IncrementalMode
    deletion_detection: DeletionDetection
    supports_acl:       bool
    supports_acl_deny:  bool          # ★ 源能否表达显式拒绝（NTFS 能，多数 wiki 不能）
    supports_inheritance_break: bool  # ★ SharePoint 断开继承
    supports_webhook:   bool
    supports_field_level_acl: bool    # 数据库类源
    max_page_size:      int
    rate_limit_qps:     float | None
    binary_formats:     frozenset[str]
```

**核心据此决定策略（这是能力声明存在的意义）：**

| 声明 | 核心的应对 |
| --- | --- |
| `deletion_detection == NONE` | 强制启用周期性全量对比；在管理后台标注"该源删除传播有延迟"，并写入同步看板 |
| `incremental == NONE` | 忽略 `sync_interval_sec` 的高频设置，按全量成本重排调度 |
| `supports_acl == False` | **拒绝接入**，除非管理员显式配置目录级手工授权并接受风险提示（FR-06 的合规论证依赖 ACL 继承） |
| `supports_acl_deny == False` | 允许接入；求值时该源不产生 deny 条目，不影响 FR-06-28 的正确性 |
| `rate_limit_qps` 有值 | 同步调度器按此限速，避免耗尽客户的源系统 API 配额 |

---

## 3. 核心数据结构 / Core Types

```python
@dataclass(frozen=True)
class Scope:
    """接入范围，对应 sources.scope_rules。"""
    include: tuple[str, ...] = ()      # 空 = 全部
    exclude: tuple[str, ...] = ()
    since:   datetime | None = None

@dataclass(frozen=True)
class Cursor:
    """不透明游标。核心只负责持久化与回传，不解释其内容。"""
    source_id: str
    token:     str
    issued_at: datetime
    version:   int = 1

@dataclass(frozen=True)
class DocumentRef:
    external_id:  str                  # 源系统内稳定标识 —— R-8，重命名后不得变
    path:         str | None           # 人类可读路径，仅用于展示与日志
    version:      str
    updated_at:   datetime | None
    size_bytes:   int | None
    container_id: str | None           # 所属空间/目录，用于继承求值

ChangeKind = Literal["created", "updated", "moved", "acl_changed"]

@dataclass(frozen=True)
class ChangeEvent:
    kind: ChangeKind
    ref:  DocumentRef

@dataclass(frozen=True)
class DeletionEvent:
    external_id: str
    detected_by: DeletionDetection
    detected_at: datetime

@dataclass(frozen=True)
class RawDocument:
    ref:        DocumentRef
    content:    bytes
    mime_type:  str
    metadata:   dict[str, Any] = field(default_factory=dict)
    # 建议的 metadata 键：author, department, doc_type, effective_until,
    #                     source_created_at, language
```

---

## 4. ACL 映射格式 / ACL Mapping

**这是本契约中最容易做错的部分。** 只支持"允许名单"的映射装不下 NTFS 显式 Deny 与 SharePoint 断开继承 —— 后果不是功能缺失，是**静默越权**。

```python
Effect        = Literal["allow", "deny"]
PrincipalType = Literal["user", "group", "role", "everyone"]
ScopeLevel    = Literal["source", "folder", "document", "field"]

@dataclass(frozen=True)
class AclEntry:
    principal_type: PrincipalType
    principal_id:   str                 # everyone 时为 "*"
    effect:         Effect              # ★ 不可省略，无默认值
    permission:     str = "read"
    scope_level:    ScopeLevel = "document"
    field_name:     str | None = None   # scope_level == "field" 时必填

@dataclass(frozen=True)
class AclSet:
    entries:            tuple[AclEntry, ...]
    inherited_from:     str | None       # 上级对象 external_id；None = 本级直接设置
    inheritance_broken: bool             # ★ True 时上级条目全部不适用
    fetched_at:         datetime
```

**映射规则 / Mapping rules**

```
A-1  effect 无默认值。连接器必须显式给出 allow 或 deny。
     源系统没有 deny 概念时，只产出 allow —— 但不得让核心去"推断"。
A-2  inheritance_broken == True 时，核心在求值中丢弃全部继承来的条目。
     SharePoint 的"停止继承权限"必须映射到这个布尔。
A-3  NTFS/SMB 的 Deny ACE 必须映射为 effect="deny"，不得转换为
     "从 allow 列表中移除该 principal" —— 二者语义不同：
     deny 会压过用户经其他组获得的 allow，移除不会。
A-4  scope_level 反映权限设置的层级，不是被保护对象的层级。
     目录级 deny 的 scope_level = "folder"，即使它作用于其下的文档。
A-5  连接器不做求值，只做映射。谁赢谁输由 TD-03 的求值算法决定。
```

**A-3 的反例（会被写错的地方）** —— 以下为**示意片段，非可运行代码**：

```python
# ❌ 错误：把 deny 实现为从 allow 名单里剔除
allowed = source_allowed_users - source_denied_users
return AclSet(entries=tuple(AclEntry("user", u, "allow") for u in allowed), ...)
# 用户 U 不在 source_denied_users 但属于被 deny 的组 G 时，此写法漏掉了拒绝

# ✅ 正确：两类条目都产出，交给求值算法
return AclSet(entries=(
    *(AclEntry("user",  u, "allow") for u in source_allowed_users),
    *(AclEntry("group", g, "allow") for g in source_allowed_groups),
    *(AclEntry("user",  u, "deny")  for u in source_denied_users),
    *(AclEntry("group", g, "deny")  for g in source_denied_groups),
), ...)
```

---

## 5. 错误分类与重试 / Error Taxonomy

```python
class ConnectorError(Exception):
    """所有连接器异常的基类。"""

class TransientError(ConnectorError):
    """可重试：网络抖动、5xx、限流。核心按指数退避重试。"""
    retry_after_sec: float | None = None

class AuthError(ConnectorError):
    """凭证失效或权限不足。不重试，告警，暂停该 source。"""

class NotFoundError(ConnectorError):
    """对象已不存在。视作删除事件，不是失败。"""

class PermanentError(ConnectorError):
    """该对象无法处理（格式损坏、超限）。记入解析质量报告，跳过，不阻塞同步。"""

class ScopeViolationError(ConnectorError):
    """连接器返回了 Scope 之外的对象。核心必须丢弃并告警 —— 这是配置或实现缺陷。"""
```

**重试策略（核心侧，连接器不自行重试）**

| 异常 | 重试 | 退避 | 上限 | 失败后 |
| --- | --- | --- | --- | --- |
| `TransientError` | ✅ | 指数，初始 1s | 5 次 | 记入 `sync_runs.docs_failed`，本轮继续 |
| `AuthError` | ❌ | — | — | `sources.status = 'error'`，告警 |
| `NotFoundError` | ❌ | — | — | 转 `DeletionEvent` |
| `PermanentError` | ❌ | — | — | 记入质量报告（FR-02-12），跳过 |
| `ScopeViolationError` | ❌ | — | — | 丢弃对象 + 安全告警 |

> **连接器不得自行 sleep 重试。** 重试是调度器的职责 —— 连接器内部重试会让限速、超时预算与断点续传全部失效。

---

## 6. 同步编排的约束 / Orchestration Constraints

这几条不属于连接器实现，但决定了连接器如何被调用，写在这里以免遗漏。

```
O-1  抑制清单前置：任何文档进入索引前，必须查 suppression_list。
     命中即跳过，并递增 sync_runs.docs_suppressed。          -- FR-12-17
O-2  索引全量重建前，必须先加载抑制清单。                      -- FR-03-08
O-3  ACL 与内容在同一事务内落库。禁止"先入内容后补权限"的时间窗。
O-4  Scope 由核心校验，不信任连接器的自我约束（见 ScopeViolationError）。
O-5  单个 source 的同步串行；不同 source 可并行，并发度受 rate_limit_qps 约束。
O-6  cursor 仅在整批成功后推进；部分失败则保持旧 cursor，下轮重放。
```

---

## 7. 契约测试套件 / Contract Test Suite

**每个连接器实现必须通过以下测试才允许合入。** 测试针对连接器的 mock 源或沙箱实例执行。

```python
# tests/contract/test_connector_contract.py
# 参数化到每个连接器实现

async def test_full_scan_stable(conn, sandbox):
    """INV-C2：源无变化时两次全量结果一致。"""
    a = {r.external_id async for r in conn.full_scan(Scope())}
    b = {r.external_id async for r in conn.full_scan(Scope())}
    assert a == b and len(a) > 0

async def test_incremental_equivalent_to_full(conn, sandbox):
    """INV-C3。"""
    full = {r.external_id async for r in conn.full_scan(Scope())}
    inc  = {e.ref.external_id async for e in conn.incremental_scan(Scope(), None)}
    assert inc == full

async def test_cursor_replay_idempotent(conn, sandbox):
    """INV-C4：同一 cursor 重放两次净效果相同。"""
    c0 = conn.checkpoint()
    sandbox.update_document("doc-1")
    first  = [e async for e in conn.incremental_scan(Scope(), c0)]
    conn.resume(c0)
    second = [e async for e in conn.incremental_scan(Scope(), c0)]
    assert {e.ref.external_id for e in first} == {e.ref.external_id for e in second}

async def test_external_id_stable_across_rename(conn, sandbox):
    """R-8：重命名后 external_id 不变。做不到则图谱与增量全部失效。"""
    before = await sandbox.create_document(name="a.docx")
    sandbox.rename(before, "b.docx")
    after = [r async for r in conn.full_scan(Scope()) if r.path and "b.docx" in r.path][0]
    assert after.external_id == before.external_id

async def test_deletion_detected(conn, sandbox):
    """③ 删除检测：按声明的方式都要能发现。"""
    d = await sandbox.create_document()
    known = {r.external_id async for r in conn.full_scan(Scope())}
    sandbox.delete(d)
    if conn.capabilities().deletion_detection != DeletionDetection.NONE:
        dels = {e.external_id async for e in conn.detect_deletions(Scope(), known)}
        assert d.external_id in dels

async def test_acl_effect_is_explicit(conn, sandbox):
    """A-1：不得有缺省 effect。"""
    ref = await sandbox.create_document()
    acl = await conn.fetch_acl(ref)
    assert all(e.effect in ("allow", "deny") for e in acl.entries)

async def test_acl_deny_preserved(conn, sandbox):
    """A-3：源端显式拒绝必须映射为 deny，不得实现为从 allow 中剔除。"""
    if not conn.capabilities().supports_acl_deny:
        pytest.skip("源不支持显式拒绝")
    ref = await sandbox.create_document()
    sandbox.grant(ref, group="engineering", effect="allow")
    sandbox.grant(ref, user="alice",        effect="deny")   # alice ∈ engineering
    acl = await conn.fetch_acl(ref)
    assert any(e.effect == "deny" and e.principal_id == "alice" for e in acl.entries)
    assert any(e.effect == "allow" and e.principal_id == "engineering" for e in acl.entries)

async def test_inheritance_break_reported(conn, sandbox):
    """A-2：断开继承必须置位。"""
    if not conn.capabilities().supports_inheritance_break:
        pytest.skip("源不支持断开继承")
    ref = await sandbox.create_document_with_broken_inheritance()
    acl = await conn.fetch_acl(ref)
    assert acl.inheritance_broken is True

async def test_scope_respected(conn, sandbox):
    """O-4 的连接器侧：不得返回 Scope 之外的对象。"""
    inside  = await sandbox.create_document(container="allowed")
    outside = await sandbox.create_document(container="forbidden")
    got = {r.external_id async for r in conn.full_scan(Scope(include=("allowed",)))}
    assert inside.external_id in got and outside.external_id not in got

async def test_not_found_is_deletion_not_failure(conn, sandbox):
    """NotFoundError 语义。"""
    ref = await sandbox.create_document()
    sandbox.delete(ref)
    with pytest.raises(NotFoundError):
        await conn.fetch_content(ref)

async def test_no_internal_retry(conn, sandbox, monkeypatch):
    """连接器不得自行重试 —— 重试是调度器职责。"""
    calls = sandbox.fail_next(times=1, error="503")
    with pytest.raises(TransientError):
        _ = [r async for r in conn.full_scan(Scope())]
    assert calls.count == 1
```

---

## 8. 首个连接器的选择（决策 A-12）

| | Confluence / SharePoint | 本地文件夹 |
| --- | --- | --- |
| 能覆盖的契约项 | 六项全覆盖 + deny + 断开继承 | ①②⑤⑥，③靠枚举，④仅 POSIX/NTFS |
| 接口定型质量 | **高** —— 契约测试可全部跑通 | 低 —— 5 项测试会被 skip |
| 首个可演示版本 | 慢 1–2 周 | 快 |

**建议**：首个实现选 SharePoint（同时具备断开继承与显式 Deny，是契约的最强约束）。若因 demo 排期先做本地文件夹，则：

```
D-1  记为技术债，写入迭代计划
D-2  第二个连接器必须是 Confluence 或 SharePoint，并在该轮重构接口
D-3  不得拖到第三、四个连接器 —— 届时重构成本已随实现数量线性增长
```

---

## 9. 交叉引用 / Cross-references

| 本文档 | 上游需求 |
| --- | --- |
| §1 六项能力 | FR-01-19 |
| §2 能力声明 | FR-01-19（声明式描述自身能力） |
| §4 ACL 映射 | FR-01-20、FR-01-12 |
| §5 错误分类 | FR-01-15（失败重试与告警） |
| §6 编排约束 O-1/O-2 | FR-12-17、FR-03-08 |
| §7 契约测试 | FR-01-19 验收 |
| 求值算法 | **不在本文档** —— 见 TD-03 |
