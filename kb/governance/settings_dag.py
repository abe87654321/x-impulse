"""带依赖的设置项 DAG —— 管理面配置的求值与校验。

用途：管理员只改自己关心的项；未设置的项使用推荐默认值。求值按依赖
拓扑序进行，前置项非法时后继项标记为阻塞，而不是带着坏配置继续启动。

这是通用容器，不绑定具体业务；备份策略见 `kb.compliance.backup_policy`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SettingKind = Literal["choice", "int", "string"]


class SettingsGraphError(Exception):
    """设置项依赖图不合法（重复键、依赖缺失、成环）。"""


class SettingsError(Exception):
    """设置求值未通过。"""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    title: str
    kind: SettingKind
    options: tuple[object, ...] = ()
    recommended: object | None = None
    depends_on: tuple[str, ...] = ()
    applies_when: Callable[[Mapping[str, object]], bool] | None = None
    check: Callable[[object, Mapping[str, object]], Sequence[str]] | None = None


@dataclass(frozen=True)
class ResolvedSettings:
    values: Mapping[str, object]
    sources: Mapping[str, str]
    errors: tuple[str, ...]
    order: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def require_ready(self) -> None:
        if self.errors:
            raise SettingsError("；".join(self.errors))


class SettingsDAG:
    def __init__(self, specs: Sequence[SettingSpec]) -> None:
        self._specs = {spec.key: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise SettingsGraphError("设置项键重复")
        for spec in specs:
            for dependency in spec.depends_on:
                if dependency not in self._specs:
                    raise SettingsGraphError(f"{spec.key} 依赖不存在的设置项 {dependency}")
        self._order = _topological_order(self._specs)

    @property
    def order(self) -> tuple[str, ...]:
        return self._order

    def describe(self) -> tuple[dict[str, object], ...]:
        """供管理面渲染；包含推荐值与依赖，不包含任何密钥或敏感值。"""
        return tuple(
            {
                "key": spec.key,
                "title": spec.title,
                "kind": spec.kind,
                "options": spec.options,
                "recommended": spec.recommended,
                "depends_on": spec.depends_on,
            }
            for spec in self._specs.values()
        )

    def resolve(self, overrides: Mapping[str, object]) -> ResolvedSettings:
        unknown = set(overrides) - set(self._specs)
        if unknown:
            raise SettingsError(f"未知设置项：{', '.join(sorted(unknown))}")

        values: dict[str, object] = {}
        sources: dict[str, str] = {}
        errors: list[str] = []
        failed: set[str] = set()

        for key in self._order:
            spec = self._specs[key]
            if spec.applies_when is not None:
                try:
                    applies = spec.applies_when(values)
                except KeyError:
                    errors.append(f"{key}: 依赖项尚未求值")
                    failed.add(key)
                    continue
                if not applies:
                    sources[key] = "not_applicable"
                    continue

            for dependency in spec.depends_on:
                if dependency in failed:
                    errors.append(f"{key}: 依赖的设置项未通过（{dependency}）")
                    failed.add(key)
                    break
            else:
                raw = overrides.get(key, spec.recommended)
                if raw is None:
                    errors.append(f"{key}: 缺少必填设置")
                    failed.add(key)
                    continue
                problem = _validate(spec, raw, values)
                if problem is not None:
                    errors.append(f"{key}: {problem}")
                    failed.add(key)
                    continue
                values[key] = raw
                sources[key] = "admin" if key in overrides else "recommended"

        return ResolvedSettings(
            values=values,
            sources=sources,
            errors=tuple(errors),
            order=self._order,
        )


def _validate(spec: SettingSpec, raw: object, values: Mapping[str, object]) -> str | None:
    if spec.kind == "choice":
        if raw not in spec.options:
            return f"取值 {raw!r} 不在允许范围 {spec.options}"
    elif spec.kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            return "必须是整数"
        if raw <= 0:
            return "必须 > 0"
    elif spec.kind == "string":
        if not isinstance(raw, str):
            return "必须是字符串"
        if not raw.strip():
            return "不能为空"
    if spec.check is not None:
        problems = list(spec.check(raw, values))
        if problems:
            return "；".join(problems)
    return None


def _topological_order(specs: Mapping[str, SettingSpec]) -> tuple[str, ...]:
    indegree = {key: 0 for key in specs}
    outgoing: dict[str, list[str]] = {key: [] for key in specs}
    for key, spec in specs.items():
        for dependency in spec.depends_on:
            indegree[key] += 1
            outgoing[dependency].append(key)

    queue = sorted(key for key, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        key = queue.pop(0)
        order.append(key)
        for dependent in sorted(outgoing[key]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if len(order) != len(specs):
        raise SettingsGraphError("设置项依赖图存在环")
    return tuple(order)


__all__ = [
    "ResolvedSettings",
    "SettingKind",
    "SettingSpec",
    "SettingsDAG",
    "SettingsError",
    "SettingsGraphError",
]
