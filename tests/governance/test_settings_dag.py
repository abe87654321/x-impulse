"""设置项 DAG：拓扑求值、推荐默认、依赖阻塞与校验。"""

from __future__ import annotations

import pytest

from kb.governance.settings_dag import (
    SettingsDAG,
    SettingsError,
    SettingsGraphError,
    SettingSpec,
)


def _dag() -> SettingsDAG:
    return SettingsDAG(
        (
            SettingSpec("b", "B", "int", recommended=2, depends_on=("a",)),
            SettingSpec("a", "A", "choice", options=("x", "y"), recommended="x"),
            SettingSpec(
                "c",
                "C",
                "choice",
                options=("on", "off"),
                recommended="on",
                depends_on=("b",),
                applies_when=lambda values: values.get("b") != 1,
            ),
        )
    )


def test_resolves_in_dependency_order_with_recommendations():
    resolved = _dag().resolve({})

    assert resolved.ready is True
    assert resolved.order == ("a", "b", "c")
    assert resolved.values == {"a": "x", "b": 2, "c": "on"}
    assert resolved.sources == {
        "a": "recommended",
        "b": "recommended",
        "c": "recommended",
    }


def test_admin_override_marks_source():
    resolved = _dag().resolve({"a": "y", "b": 3})

    assert resolved.values["a"] == "y"
    assert resolved.sources["a"] == "admin"
    assert resolved.values["c"] == "on"


def test_not_applicable_when_parent_disables_it():
    resolved = _dag().resolve({"b": 1})

    assert resolved.sources["c"] == "not_applicable"
    assert "c" not in resolved.values


def test_invalid_choice_and_type_are_errors():
    resolved = _dag().resolve({"a": "z", "b": "three"})

    assert resolved.ready is False
    assert any("a:" in error for error in resolved.errors)
    assert any("b:" in error for error in resolved.errors)


def test_invalid_parent_blocks_dependent():
    resolved = _dag().resolve({"a": "z"})

    assert any("b:" in error and "依赖" in error for error in resolved.errors)


def test_unknown_setting_is_rejected():
    with pytest.raises(SettingsError):
        _dag().resolve({"nope": 1})


def test_require_ready_raises_on_errors():
    resolved = _dag().resolve({"a": "z"})

    with pytest.raises(SettingsError):
        resolved.require_ready()


def test_cycle_is_rejected():
    with pytest.raises(SettingsGraphError):
        SettingsDAG(
            (
                SettingSpec("a", "A", "choice", options=("x",), recommended="x", depends_on=("b",)),
                SettingSpec("b", "B", "choice", options=("x",), recommended="x", depends_on=("a",)),
            )
        )


def test_missing_dependency_is_rejected():
    with pytest.raises(SettingsGraphError):
        SettingsDAG(
            (SettingSpec("a", "A", "choice", options=("x",), recommended="x", depends_on=("b",)),)
        )


def test_describe_exposes_recommendations_for_admin_ui():
    described = {item["key"]: item for item in _dag().describe()}

    assert described["a"]["recommended"] == "x"
    assert described["b"]["depends_on"] == ("a",)
