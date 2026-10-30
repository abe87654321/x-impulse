"""检索结果缓存（基础版）—— FR-04-14a。

缓存的是「问题哈希 → 已召回 chunk_id 列表」，**不按用户权限分区**。
命中后仍必须做实时权限二次校验（FR-06-03）。因此缓存里绝不放
"已过滤结果"，只放召回候选。

删除链路要求按 chunk_id 定点失效 —— 这是 FR-04-14b / 删除位置 4 的硬前置。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence


def make_cache_key(*, tenant_id: str, query: str, metadata_signature: str) -> str:
    """问题哈希。含租户与元数据过滤指纹，**不含权限信息**。"""
    raw = f"{tenant_id}\x00{query}\x00{metadata_signature}".encode()
    return hashlib.sha256(raw).hexdigest()


class InMemoryQueryCache:
    """进程内实现；生产由共享存储替换，接口保持不变。"""

    def __init__(self) -> None:
        self._forward: dict[str, tuple[str, ...]] = {}
        self._reverse: dict[str, set[str]] = {}

    def get(self, key: str) -> tuple[str, ...] | None:
        return self._forward.get(key)

    def put(self, key: str, chunk_ids: Sequence[str]) -> None:
        self._drop(key)
        ids = tuple(chunk_ids)
        self._forward[key] = ids
        for chunk_id in set(ids):
            self._reverse.setdefault(chunk_id, set()).add(key)

    def invalidate_chunk(self, chunk_id: str) -> None:
        """按 chunk_id 定点失效：所有引用它的缓存条目都移除该 id。"""
        for key in self._reverse.pop(chunk_id, set()):
            remaining = tuple(i for i in self._forward.get(key, ()) if i != chunk_id)
            if remaining:
                self._forward[key] = remaining
            else:
                self._forward.pop(key, None)

    def clear(self) -> None:
        self._forward.clear()
        self._reverse.clear()

    def _drop(self, key: str) -> None:
        for chunk_id in self._forward.pop(key, ()):
            keys = self._reverse.get(chunk_id)
            if keys is not None:
                keys.discard(key)
                if not keys:
                    self._reverse.pop(chunk_id, None)


__all__ = ["InMemoryQueryCache", "make_cache_key"]
