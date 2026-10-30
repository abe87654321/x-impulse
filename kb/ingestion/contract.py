"""连接器契约 —— 六项能力、能力声明、核心类型与错误分类。

对应 docs/td-02-connector-contract.md §1~§5。所有实现必须满足本模块的
接口与不变量；契约测试见 tests/contract/。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

# ── 能力声明 ────────────────────────────────────────────────


class DeletionDetection(StrEnum):
    EVENT = "event"
    TOMBSTONE = "tombstone"
    ENUMERATE = "enumerate"
    NONE = "none"


class IncrementalMode(StrEnum):
    CURSOR = "cursor"
    TIMESTAMP = "timestamp"
    VERSION = "version"
    NONE = "none"


@dataclass(frozen=True)
class Capabilities:
    incremental: IncrementalMode
    deletion_detection: DeletionDetection
    supports_acl: bool
    supports_acl_deny: bool
    supports_inheritance_break: bool
    supports_webhook: bool
    supports_field_level_acl: bool
    max_page_size: int
    rate_limit_qps: float | None
    binary_formats: frozenset[str]


# ── 核心数据结构 ────────────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """接入范围，对应 sources.scope_rules。空 include = 全部。"""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    since: datetime | None = None


@dataclass(frozen=True)
class Cursor:
    """不透明游标。核心只负责持久化与回传，不解释其内容。"""

    source_id: str
    token: str
    issued_at: datetime
    version: int = 1


@dataclass(frozen=True)
class DocumentRef:
    external_id: str
    path: str | None
    version: str
    updated_at: datetime | None
    size_bytes: int | None
    container_id: str | None


ChangeKind = Literal["created", "updated", "moved", "acl_changed"]


@dataclass(frozen=True)
class ChangeEvent:
    kind: ChangeKind
    ref: DocumentRef


@dataclass(frozen=True)
class DeletionEvent:
    external_id: str
    detected_by: DeletionDetection
    detected_at: datetime


@dataclass(frozen=True)
class RawDocument:
    ref: DocumentRef
    content: bytes
    mime_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── ACL 映射 ────────────────────────────────────────────────

Effect = Literal["allow", "deny"]
PrincipalType = Literal["user", "group", "role", "everyone"]
ScopeLevel = Literal["source", "folder", "document", "field"]


@dataclass(frozen=True)
class AclEntry:
    principal_type: PrincipalType
    principal_id: str
    effect: Effect
    permission: str = "read"
    scope_level: ScopeLevel = "document"
    field_name: str | None = None


@dataclass(frozen=True)
class AclSet:
    entries: tuple[AclEntry, ...]
    inherited_from: str | None
    inheritance_broken: bool
    fetched_at: datetime


# ── 错误分类 ────────────────────────────────────────────────


class ConnectorError(Exception):
    """所有连接器异常的基类。"""


class TransientError(ConnectorError):
    """可重试：网络抖动、5xx、限流。核心按指数退避重试。"""

    def __init__(self, message: str = "", retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class AuthError(ConnectorError):
    """凭证失效或权限不足。不重试，告警，暂停该 source。"""


class NotFoundError(ConnectorError):
    """对象已不存在。视作删除事件，不是失败。"""


class PermanentError(ConnectorError):
    """该对象无法处理（格式损坏、超限）。记录并跳过，不阻塞同步。"""


class ScopeViolationError(ConnectorError):
    """连接器返回了 Scope 之外的对象。核心必须丢弃并告警。"""


# ── 接口 ────────────────────────────────────────────────────


class Connector(ABC):
    """每个知识源一个实现。所有方法必须幂等且可重入。"""

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """能力声明先于一切调用。做不到的能力必须在声明里体现。"""

    @abstractmethod
    def full_scan(self, scope: Scope) -> AsyncIterator[DocumentRef]:
        """① 全量拉取。"""

    @abstractmethod
    def incremental_scan(self, scope: Scope, cursor: Cursor | None) -> AsyncIterator[ChangeEvent]:
        """② 增量识别。cursor=None 等价于 full_scan 的变更表示。"""

    @abstractmethod
    def detect_deletions(self, scope: Scope, known_ids: set[str]) -> AsyncIterator[DeletionEvent]:
        """③ 删除检测。"""

    @abstractmethod
    async def fetch_acl(self, ref: DocumentRef) -> AclSet:
        """④ ACL 读取与映射。连接器只映射，不求值。"""

    @abstractmethod
    async def fetch_content(self, ref: DocumentRef) -> RawDocument:
        """⑤ 内容与元数据抽取。"""

    @abstractmethod
    def checkpoint(self) -> Cursor:
        """⑥ 返回"此刻之后尚未处理"的位置，不改变连接器状态。"""

    @abstractmethod
    def resume(self, cursor: Cursor) -> None:
        """⑥ 只设置内部位置，不触发拉取；必须幂等。"""
