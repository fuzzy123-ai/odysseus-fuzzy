import os
from typing import Dict


DEFAULT_FLAGS = {
    "obsidian_somt_enabled": True,
    "obsidian_freshness_gate_enabled": True,
    "obsidian_raptor_enabled": False,
    "obsidian_hybrid_retrieval_enabled": False,
    "obsidian_memory_tree_ui_enabled": False,
}

ENV_NAMES = {
    "obsidian_somt_enabled": "ODYSSEUS_OBSIDIAN_SOMT_ENABLED",
    "obsidian_freshness_gate_enabled": "ODYSSEUS_OBSIDIAN_FRESHNESS_GATE_ENABLED",
    "obsidian_raptor_enabled": "ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED",
    "obsidian_hybrid_retrieval_enabled": "ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED",
    "obsidian_memory_tree_ui_enabled": "ODYSSEUS_OBSIDIAN_MEMORY_TREE_UI_ENABLED",
}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def is_enabled(flag: str) -> bool:
    if flag not in DEFAULT_FLAGS:
        return False
    return _bool_env(ENV_NAMES[flag], DEFAULT_FLAGS[flag])


def all_flags() -> Dict[str, bool]:
    return {flag: is_enabled(flag) for flag in DEFAULT_FLAGS}


def freshness_filtering_state(flags: Dict[str, bool] | None = None) -> str:
    values = flags or all_flags()
    if not values.get("obsidian_freshness_gate_enabled", False):
        return "disabled"
    if values.get("obsidian_hybrid_retrieval_enabled", False):
        return "active"
    return "audit_only"
