from __future__ import annotations

import ast
from pathlib import Path

from src.builtin_tool_catalog import (
    EMAIL_ADAPTER_TOOL_IDS,
    OPERATOR_PRIORITY_DEFERRED_IDS,
    build_builtin_descriptor_catalog,
    resolve_operator_priority_disabled,
)
from src.tool_policy import build_effective_tool_policy


ROOT = Path(__file__).resolve().parents[1]


def test_operator_priority_family_is_exact_and_catalog_defaults_are_safe():
    assert OPERATOR_PRIORITY_DEFERRED_IDS == EMAIL_ADAPTER_TOOL_IDS | {
        "manage_assistant",
        "manage_calendar",
        "manage_contact",
        "manage_presets",
        "resolve_contact",
    }
    assert len(OPERATOR_PRIORITY_DEFERRED_IDS) == 14

    catalog = build_builtin_descriptor_catalog()
    for tool_id in OPERATOR_PRIORITY_DEFERRED_IDS:
        descriptor = catalog.resolve(tool_id)
        assert descriptor.lifecycle.value == "deferred"
        assert descriptor.default_enabled is False
        assert descriptor.default_visibility.value == "hidden"


def test_missing_setting_uses_defaults_without_persisting_or_mutating_input():
    configured = ["web_search"]
    disabled, defaults_applied = resolve_operator_priority_disabled(
        configured,
        setting_present=False,
    )
    assert defaults_applied is True
    assert disabled == {"web_search"} | OPERATOR_PRIORITY_DEFERRED_IDS
    assert configured == ["web_search"]


def test_explicit_legacy_setting_remains_authoritative_until_tax9():
    configured = ["web_search"]
    disabled, defaults_applied = resolve_operator_priority_disabled(
        configured,
        setting_present=True,
    )
    assert defaults_applied is False
    assert disabled == {"web_search"}
    assert configured == ["web_search"]

    explicitly_enabled, defaults_applied = resolve_operator_priority_disabled(
        [],
        setting_present=True,
    )
    assert defaults_applied is False
    assert explicitly_enabled == frozenset()


def test_turn_policy_hides_priority_tools_when_settings_are_missing():
    policy = build_effective_tool_policy(settings=None)
    assert OPERATOR_PRIORITY_DEFERRED_IDS <= policy.disabled_tools
    assert OPERATOR_PRIORITY_DEFERRED_IDS <= policy.hidden_tools
    for tool_id in OPERATOR_PRIORITY_DEFERRED_IDS:
        assert "operator priority" in policy.reason_for(tool_id)


def test_explicit_settings_can_retain_reviewed_assistant_and_preset_configuration():
    settings = {"disabled_tools": []}
    policy = build_effective_tool_policy(settings=settings)
    assert policy.blocks("manage_assistant") is False
    assert policy.blocks("manage_presets") is False
    assert settings == {"disabled_tools": []}


def test_normal_prompt_builder_hides_priority_families(monkeypatch):
    import src.agent_loop_system_prompt as prompt_module

    calls: list[tuple[set[str], set[str]]] = []

    def _capture(tool_names, disabled, compact=False):
        calls.append((set(tool_names), set(disabled)))
        visible = sorted(set(tool_names) - set(disabled))
        return " ".join(visible)

    monkeypatch.setattr(prompt_module, "_assemble_prompt", _capture)
    monkeypatch.setattr(prompt_module, "get_setting", lambda *_args, **_kwargs: True)

    relevant = set(OPERATOR_PRIORITY_DEFERRED_IDS) | {"read_file"}
    rendered, _skill_index = prompt_module._build_base_prompt(
        set(),
        None,
        False,
        relevant_tools=relevant,
        compact=True,
        suppress_local_context=True,
    )
    assert calls
    assert OPERATOR_PRIORITY_DEFERRED_IDS <= calls[0][1]
    assert "read_file" in rendered
    for tool_id in OPERATOR_PRIORITY_DEFERRED_IDS:
        assert tool_id not in rendered


def test_admin_prompt_path_can_still_receive_an_explicit_reviewed_configuration(monkeypatch):
    import src.agent_loop_system_prompt as prompt_module

    calls: list[set[str]] = []

    def _capture(tool_names, disabled, compact=False):
        calls.append(set(disabled))
        return "ok"

    monkeypatch.setattr(prompt_module, "_assemble_prompt", _capture)
    monkeypatch.setattr(prompt_module, "get_setting", lambda *_args, **_kwargs: True)
    prompt_module._build_base_prompt(
        set(),
        None,
        True,
        relevant_tools={"manage_presets"},
        compact=True,
        suppress_local_context=True,
    )
    assert calls
    assert "manage_presets" not in calls[0]


def test_read_only_tool_routes_distinguish_missing_from_explicit_settings():
    source = (ROOT / "routes" / "model_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_operator_priority_disabled"
    ]
    assert len(calls) == 2
    for call in calls:
        keyword = next(item for item in call.keywords if item.arg == "setting_present")
        assert isinstance(keyword.value, ast.Compare)
        assert isinstance(keyword.value.ops[0], ast.In)

