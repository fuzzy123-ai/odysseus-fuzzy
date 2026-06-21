import os
from typing import Dict


DEFAULT_FLAGS = {
    "obsidian_somt_enabled": True,
    "obsidian_freshness_gate_enabled": True,
    "obsidian_raptor_enabled": False,
    "obsidian_raptor_rebuild_enabled": False,
    "obsidian_hybrid_retrieval_enabled": False,
    "obsidian_memory_tree_ui_enabled": False,
}

ORCA_FLAG_ALIASES = {
    "orca_somt_enabled": "obsidian_somt_enabled",
    "orca_freshness_gate_enabled": "obsidian_freshness_gate_enabled",
    "orca_raptor_enabled": "obsidian_raptor_enabled",
    "orca_raptor_rebuild_enabled": "obsidian_raptor_rebuild_enabled",
    "orca_hybrid_retrieval_enabled": "obsidian_hybrid_retrieval_enabled",
    "orca_memory_tree_ui_enabled": "obsidian_memory_tree_ui_enabled",
}

ENV_NAMES = {
    "obsidian_somt_enabled": "ODYSSEUS_OBSIDIAN_SOMT_ENABLED",
    "obsidian_freshness_gate_enabled": "ODYSSEUS_OBSIDIAN_FRESHNESS_GATE_ENABLED",
    "obsidian_raptor_enabled": "ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED",
    "obsidian_raptor_rebuild_enabled": "ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED",
    "obsidian_hybrid_retrieval_enabled": "ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED",
    "obsidian_memory_tree_ui_enabled": "ODYSSEUS_OBSIDIAN_MEMORY_TREE_UI_ENABLED",
}

ORCA_ENV_NAMES = {
    "obsidian_somt_enabled": "ODYSSEUS_ORCA_SOMT_ENABLED",
    "obsidian_freshness_gate_enabled": "ODYSSEUS_ORCA_FRESHNESS_GATE_ENABLED",
    "obsidian_raptor_enabled": "ODYSSEUS_ORCA_RAPTOR_ENABLED",
    "obsidian_raptor_rebuild_enabled": "ODYSSEUS_ORCA_RAPTOR_REBUILD_ENABLED",
    "obsidian_hybrid_retrieval_enabled": "ODYSSEUS_ORCA_HYBRID_RETRIEVAL_ENABLED",
    "obsidian_memory_tree_ui_enabled": "ODYSSEUS_ORCA_MEMORY_TREE_UI_ENABLED",
}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _canonical_flag(flag: str) -> str:
    return ORCA_FLAG_ALIASES.get(flag, flag)


def _flag_env(flag: str, default: bool) -> bool:
    orca_env = ORCA_ENV_NAMES.get(flag)
    if orca_env and os.getenv(orca_env) is not None:
        return _bool_env(orca_env, default)
    return _bool_env(ENV_NAMES[flag], default)


def is_enabled(flag: str) -> bool:
    canonical = _canonical_flag(flag)
    if canonical not in DEFAULT_FLAGS:
        return False
    return _flag_env(canonical, DEFAULT_FLAGS[canonical])


def all_flags() -> Dict[str, bool]:
    return {flag: is_enabled(flag) for flag in DEFAULT_FLAGS}


def freshness_filtering_state(flags: Dict[str, bool] | None = None) -> str:
    values = flags or all_flags()
    if not values.get("obsidian_freshness_gate_enabled", False):
        return "disabled"
    if values.get("obsidian_hybrid_retrieval_enabled", False):
        return "active"
    return "audit_only"
