"""Quarantined contrib package (Scope-Diät).

This package isolates legacy, unverified, or heavy niche features that are
quarantined under the MCP-First architecture:
- local_model_serving_cookbook
- image_tools_background_removal_worker
- experimental_tts_stt_protocols
- frontpage_v2_window_manager
- harbor_one_web_ui_h1r14

Under ODYSSEUS_MCP_FIRST_MODE=1, contrib features are disabled by default
to maintain a lean, robust platform surface.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

CONTRIB_QUARANTINE_SCHEMA = "odysseus.contrib.quarantine_manifest.v1"

QUARANTINED_FEATURES: list[dict[str, Any]] = [
    {
        "id": "local_model_serving_cookbook",
        "name": "Local Model Serving Cookbook",
        "description": "Experimental local model serving cookbook and unverified local runtime recipes",
        "quarantined_since": "2026-10-02",
        "replacement": "Centralized API endpoints / Ollama adapter",
    },
    {
        "id": "image_tools_background_removal_worker",
        "name": "Image Tools Background Removal Worker",
        "description": "Background removal worker requiring heavy native computer vision libraries",
        "quarantined_since": "2026-10-02",
        "replacement": "Headless image tool delegation",
    },
    {
        "id": "experimental_tts_stt_protocols",
        "name": "Experimental TTS/STT Protocols",
        "description": "Experimental TTS/STT protocols outside the standard faster-whisper core",
        "quarantined_since": "2026-10-02",
        "replacement": "Standardized faster-whisper pipeline",
    },
    {
        "id": "frontpage_v2_window_manager",
        "name": "Frontpage v2 Multi-Window Manager",
        "description": "Legacy frontpage v2 multi-window manager desktop emulation UI",
        "quarantined_since": "2026-10-02",
        "replacement": "Deferred/frozen UI (H1R-14)",
    },
    {
        "id": "harbor_one_web_ui_h1r14",
        "name": "Harbour One Web UI (H1R-14)",
        "description": "Complex browser frontend web UI implementation under H1R-14",
        "quarantined_since": "2026-10-02",
        "replacement": "MCP-First Primary Interface (mcp-first-2026-10-02)",
    },
]

QUARANTINED_FEATURE_IDS: tuple[str, ...] = tuple(f["id"] for f in QUARANTINED_FEATURES)


def is_contrib_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Check whether quarantined contrib features should be loaded or enabled.

    Rules:
    1. If ODYSSEUS_ENABLE_CONTRIB is explicitly set to truthy, return True.
    2. If ODYSSEUS_ENABLE_CONTRIB is explicitly set to falsy, return False.
    3. If ODYSSEUS_MCP_FIRST_MODE is active ('1', 'true', 'yes', 'on'), default to False.
    4. Otherwise, default to True for legacy application backwards compatibility.
    """
    if env is None:
        env = os.environ

    contrib_raw = env.get("ODYSSEUS_ENABLE_CONTRIB")
    if contrib_raw is not None:
        val = contrib_raw.strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False

    mcp_first_raw = env.get("ODYSSEUS_MCP_FIRST_MODE", "").strip().lower()
    if mcp_first_raw in ("1", "true", "yes", "on"):
        return False

    return True


def get_quarantine_manifest(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the quarantine status and manifest for contrib features."""
    if env is None:
        env = os.environ

    mcp_first_raw = env.get("ODYSSEUS_MCP_FIRST_MODE", "").strip().lower()
    mcp_first_mode = mcp_first_raw in ("1", "true", "yes", "on")
    enabled = is_contrib_enabled(env)

    return {
        "schema": CONTRIB_QUARANTINE_SCHEMA,
        "manifest_version": "1.0.0",
        "mcp_first_mode_enforced": mcp_first_mode,
        "contrib_enabled": enabled,
        "quarantined_feature_count": len(QUARANTINED_FEATURES),
        "quarantined_features": [dict(f) for f in QUARANTINED_FEATURES],
        "quarantined_feature_ids": list(QUARANTINED_FEATURE_IDS),
    }


__all__ = [
    "CONTRIB_QUARANTINE_SCHEMA",
    "QUARANTINED_FEATURES",
    "QUARANTINED_FEATURE_IDS",
    "is_contrib_enabled",
    "get_quarantine_manifest",
]
