# src/settings.py
"""Centralized settings and features management.

Single source of truth for reading/writing data/settings.json and data/features.json.
All modules should import from here instead of accessing files directly.
"""

from collections import Counter
from copy import deepcopy
import json
import time
import logging
from typing import Any, Mapping

from src.constants import SETTINGS_FILE, FEATURES_FILE

logger = logging.getLogger(__name__)

# Tiny TTL cache for settings/features. get_setting() is called on hot paths
# (every chat, every preprocess); without this it re-parses the JSON each call.
# Picks up edits within _CACHE_TTL seconds, which is fine for human-edited config.
_CACHE_TTL = 2.0
_settings_cache: tuple[float, dict] | None = None
_features_cache: tuple[float, dict] | None = None

def _invalidate_caches():
    global _settings_cache, _features_cache
    _settings_cache = None
    _features_cache = None

# ── Default values ──

DEFAULT_SETTINGS = {
    # Global privacy switch. When enabled, runtime gates must treat all
    # potentially private context as local-only unless a narrower policy
    # explicitly blocks or requires review.
    "dsgvo_mode": False,
    # Agent email safety: when True, the MCP send_email / reply_to_email
    # tools don't SMTP directly. They stage the composed message into the
    # scheduled_emails table with status='agent_draft' and return a
    # pending_id + the rendered email so the user can review and approve
    # (or cancel) before it actually goes out. Default ON because models
    # have been observed inventing signatures and sending to real
    # recipients without confirmation.
    "agent_email_confirm": True,
    "image_gen_enabled": False,
    "image_model": "",
    "image_quality": "medium",
    "vision_model": "",
    "vision_enabled": True,
    # Ordered fallback chain for the Vision model (image analysis, OCR, tagging).
    "vision_model_fallbacks": [],
    # Public base URL used to build clickable deep-links in outgoing alerts
    # (e.g., urgency alert email). Example: "https://chat.example.com"
    "app_public_url": "",
    "tts_enabled": True,
    "tts_provider": "disabled",
    "tts_model": "tts-1",
    "tts_voice": "alloy",
    "tts_speed": "1",
    "stt_enabled": False,
    "stt_provider": "disabled",
    "stt_model": "base",
    "stt_language": "",
    "search_provider": "searxng",
    # Default fallback chain — when the primary provider fails or
    # rate-limits, we try DuckDuckGo next. Free, no API key required, so
    # safe to ship on by default for every user.
    "search_fallback_chain": ["duckduckgo"],
    "search_url": "",
    "search_result_count": 5,
    # SafeSearch level applied to every provider that exposes one.
    # "strict"   — block adult / explicit results (default; matches what users
    #              expect from a research tool and avoids unrelated NSFW URLs
    #              bleeding in via provider "related" / spam recommendations)
    # "moderate" — provider-default behavior (filter explicit but allow
    #              suggestive content)
    # "off"      — disable filtering entirely (advanced users only)
    #
    # Providers that honor this setting (translated to each provider's native
    # param in src/search/providers.py:_safesearch_for):
    #     SearXNG       safesearch=0/1/2 (JSON API, HTML scrape, news fallback)
    #     Brave Search  safesearch=off/moderate/strict
    #     DuckDuckGo    safesearch=off/moderate/on (library + HTML kp param)
    #     Google PSE    safe=active (omitted for "off"; PSE has no middle tier)
    #     Serper.dev    safe=active (omitted for "off"; proxies Google's `safe`)
    # Providers NOT touched: Tavily (no SafeSearch knob; filters at index time)
    # and any custom backend reached via search_url — they keep whatever the
    # backend itself decides, so operators stay in control of self-hosted /
    # niche search instances.
    "search_safesearch": "strict",
    "brave_api_key": "",
    "google_pse_key": "",
    "google_pse_cx": "",
    "tavily_api_key": "",
    "serper_api_key": "",
    "research_endpoint_id": "",
    "research_model": "",
    "research_search_provider": "",
    "research_max_tokens": 16384,
    "research_extraction_timeout_seconds": 90,
    # Lightweight planning/query LLM calls happen before any search starts.
    # Keep them separately tunable so slow local backends are not capped by
    # the old 30s/60s per-call defaults.
    "research_planning_timeout_seconds": 90,
    "research_query_timeout_seconds": 90,
    "research_extraction_concurrency": 3,
    # Hard wall-clock cap on a single deep-research run. The previous 600s
    # (10 min) default cut off slow local / edge LLMs mid-synthesis; 1800s
    # (30 min) is comfortable for most local setups while still bounding
    # runaway jobs. Set to 0 to disable the cap entirely (unlimited) — only
    # for very long deep-research runs, since a stalled job then runs an
    # unbounded model/API bill. Other values are bounded to [60, 86400].
    # Tune via Settings or by editing data/settings.json.
    "research_run_timeout_seconds": 1800,
    "agent_max_tool_calls": 0,
    "agent_max_rounds": 20,  # per-message agent step cap (clamped 1..200)
    # Soft input-token budget for the agent loop. The DEFAULT value (6000) is the
    # "auto" sentinel: it means "scale the budget to the model's context window"
    # (#1230) — so long-context models aren't capped at 6000. Set ANY OTHER value
    # to enforce an explicit cap (clamped to the window only — hard_max does not
    # apply to explicit budgets, #1230); set 0 to disable soft-trimming. The
    # default is treated as auto because the settings-save path materializes
    # defaults, so a persisted 6000 can't be told apart from a deliberate 6000 —
    # to pin a budget near the default, use a nearby value (e.g. 5999).
    "agent_input_token_budget": 6000,
    # Ceiling on the *auto-derived* input budget; a configurable setting since #1273
    # (the merged #1230 left it a module constant). No effect on an explicit budget
    # — a deliberate value is honoured (#1230). Default matches
    # `src.context_budget.DEFAULT_HARD_MAX`; lower this for
    # cost-paranoid setups, raise it on premium APIs with very large windows you
    # want to actually use (e.g. 900_000 to fill a 1M-context model). See
    # `compute_input_token_budget` in src/context_budget.py.
    "agent_input_token_hard_max": 32_000,
    # Optional prompt-cap overrides. Positive integer values only; resolution
    # precedence is exact normalized model, then provider, then the legacy
    # global cap/auto policy. Invalid entries are ignored defensively at runtime.
    "agent_input_token_budget_overrides": {"providers": {}, "models": {}},
    "context_compact_threshold": 0.65,
    "session_touch_interval_seconds": 60,
    "session_cache_max": 100,
    "session_context_message_limit": 200,
    "agent_stream_timeout_seconds": 300,
    # Extra directory roots that read_file / write_file may access, in
    # addition to the built-in project data/ and system temp dirs. Each
    # entry is an absolute path. Sensitive subpaths (.ssh, .gnupg, shell
    # rc files, SSH key files) are always blocked regardless of roots.
    "tool_path_extra_roots": [],
    "task_endpoint_id": "",
    "task_model": "",
    # Telegram-specific chat model spec. When set, Telegram /new uses this
    # instead of the general default chat model. Supports "model@endpoint".
    "telegram_model_spec": "",
    "default_endpoint_id": "",
    "default_model": "",
    # Ordered fallback chain for the default chat model. Each entry is
    # {"endpoint_id": "...", "model": "..."}. If the primary model fails
    # before producing output (endpoint offline / errors), the chat
    # dispatch retries the next entry in order.
    "default_model_fallbacks": [],
    "utility_endpoint_id": "",
    "utility_model": "",
    # Ordered fallback chain for the Utility model (summarization, naming,
    # tidy actions, etc.).
    "utility_model_fallbacks": [],
    "memory.router_model": "heuristic",
    "memory.answer_model": "default",
    "memory.answer_fallback_models": [],
    "memory.summarize_model": "default",
    "memory.graph_extract_model": "default",
    "memory.global_synthesis_model": "default",
    "memory.embedding_model": "",
    "maintenance_model_ref": "gemma3:4b",
    "maintenance_model_provider": "local_ollama",
    "maintenance_model_fallback_ref": "api-review-model",
    "maintenance_model_token_budget": 1200,
    "maintenance_model_max_input_chars": 6000,
    "maintenance_model_chunk_budget": 4,
    "maintenance_model_source_ref_budget": 4,
    "maintenance_model_latency_budget_ms": 45000,
    "maintenance_model_api_fallback_enabled": False,
    "maintenance_runtime_enabled": False,
    "teacher_model": "",
    "teacher_enabled": False,
    # Skills: minimum self-reported confidence for an auto-written (LLM-authored)
    # DRAFT skill to be injected into the agent prompt. Published skills always
    # qualify. Keeps low-confidence auto-skills out of context until they're
    # vetted/published. 0 disables the gate.
    "skill_autosave_min_confidence": 0.85,
    # Max relevant skills injected into the prompt for one request. The skills
    # library can grow beyond this; cleanup/retirement is an explicit review flow.
    "skill_max_injected": 3,
    # Reminders
    "reminder_channel": "browser",   # "browser" | "email" | "ntfy" | "webhook" | "telegram"
    "reminder_telegram_dry_run": True,
    "reminder_llm_synthesis": False,
    "reminder_llm_persona": "",
    "reminder_ntfy_topic": "Reminders",
    "reminder_email_to": "",
    # Generic outbound webhook channel: pick any saved Integration as the
    # target and supply a JSON payload template. Use {{title}} and {{message}}
    # as placeholders — they are JSON-escaped before substitution, so the
    # rendered string is always valid JSON. Works with Discord, Slack, Teams,
    # ntfy (JSON mode), or any service that accepts a POST with a JSON body.
    "reminder_webhook_integration_id": "",
    "reminder_webhook_payload_template": "",
    # Email triage scanner rules. Running/paused state and schedule live in
    # Tasks via the built-in `check_email_urgency` task.
    "urgent_email_prompt": (
        "Flag as urgent: explicit deadlines, time-sensitive requests, "
        "work-blocking issues, messages from people I report to, or anything "
        "where a delayed reply costs money/trust. Someone waiting outside, "
        "at the door, locked out, or unable to get in is urgent now. "
        "Newsletters, marketing, automated digests, and FYI-only updates are "
        "NOT urgent."
    ),
    # Keyboard shortcuts (action: key combination)
    "keybinds": {
        "search": "ctrl+k",
        "toggle_sidebar": "ctrl+b",
        "new_session": "ctrl+alt+n",
        "star_session": "ctrl+alt+s",
        "delete_session": "ctrl+alt+d",
        "admin_panel": "ctrl+shift+u",
        "cancel": "escape",
    },
}

DEFAULT_FEATURES = {
    "web_search": True,
    "web_fetch": True,
    "deep_research": False,
    "memory": True,
    "document_editor": True,
    "rag": True,
    "sensitive_filter": True,
    "gallery": True,
    "context_provider_preload": True,
    "consolidation_jobs": True,
}


TOOL_SETTINGS_MIGRATION_KEY = "_tool_settings_migration"
TOOL_SETTINGS_MIGRATION_VERSION = 1


class ToolSettingsMigrationError(ValueError):
    """Raised when persisted tool settings cannot be migrated safely."""


def _tool_settings_known_ids() -> frozenset[str]:
    from src.builtin_tool_catalog import CATALOG_TOOL_IDS
    from src.tool_policy import DEFAULT_DEFERRED_RUNTIME_TOOLS

    return frozenset(CATALOG_TOOL_IDS) | DEFAULT_DEFERRED_RUNTIME_TOOLS


def _tool_settings_report(
    settings: Mapping[str, Any],
    *,
    changed: bool,
) -> dict[str, Any]:
    metadata = settings.get(TOOL_SETTINGS_MIGRATION_KEY)
    if not isinstance(metadata, Mapping):
        raise ToolSettingsMigrationError("tool settings migration metadata is missing")
    quarantine = metadata.get("quarantine", ())
    aliases = metadata.get("aliases", ())
    if not isinstance(quarantine, (list, tuple)) or not isinstance(
        aliases, (list, tuple)
    ):
        raise ToolSettingsMigrationError("tool settings migration metadata is malformed")
    reason_counts = Counter(
        str(item.get("reason", "invalid_quarantine_record"))
        for item in quarantine
        if isinstance(item, Mapping)
    )
    disabled_tools = settings.get("disabled_tools", ())
    return {
        "schema_version": TOOL_SETTINGS_MIGRATION_VERSION,
        "changed": changed,
        "rollback_available": isinstance(metadata.get("rollback"), Mapping),
        "disabled_tool_count": (
            len(disabled_tools)
            if isinstance(disabled_tools, (list, tuple, set, frozenset))
            else 0
        ),
        "alias_migration_count": len(aliases),
        "quarantine_count": len(quarantine),
        "quarantine_reason_counts": dict(sorted(reason_counts.items())),
        "raw_values_visible": False,
        "user_data_visible": False,
        "provider_data_visible": False,
    }


def migrate_tool_settings(
    settings: Mapping[str, Any],
    *,
    known_tool_ids: set[str] | frozenset[str] | None = None,
    alias_targets: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the versioned, reversible TAX9 settings migration.

    Unknown syntactically safe identifiers remain in ``disabled_tools`` so a
    dynamic tool cannot become active by accident. They are also retained in
    the private rollback/quarantine metadata. The returned report contains
    counts and reason codes only; raw persisted values are never logged.
    """

    if not isinstance(settings, Mapping):
        raise ToolSettingsMigrationError("settings must be an object")
    migrated = deepcopy(dict(settings))
    existing = migrated.get(TOOL_SETTINGS_MIGRATION_KEY)
    if existing is not None:
        if (
            not isinstance(existing, Mapping)
            or existing.get("schema_version") != TOOL_SETTINGS_MIGRATION_VERSION
        ):
            raise ToolSettingsMigrationError("unsupported tool settings migration version")
        return migrated, _tool_settings_report(migrated, changed=False)

    from src.tool_catalog import (
        ToolCatalogError,
        ToolIdentifierDisposition,
        resolve_tool_identifier_for_migration,
    )

    known = frozenset(
        _tool_settings_known_ids() if known_tool_ids is None else known_tool_ids
    )
    had_disabled_tools = "disabled_tools" in migrated
    original_disabled_tools = deepcopy(migrated.get("disabled_tools"))
    quarantine: list[dict[str, Any]] = []
    aliases: list[dict[str, str]] = []
    seen_aliases: set[tuple[str, str]] = set()

    if not had_disabled_tools:
        from src.tool_policy import DEFAULT_DEFERRED_RUNTIME_TOOLS

        canonical_disabled = set(DEFAULT_DEFERRED_RUNTIME_TOOLS)
    elif not isinstance(original_disabled_tools, (list, tuple, set, frozenset)):
        from src.tool_policy import DEFAULT_DEFERRED_RUNTIME_TOOLS

        canonical_disabled = set(DEFAULT_DEFERRED_RUNTIME_TOOLS)
        quarantine.append(
            {
                "value": deepcopy(original_disabled_tools),
                "reason": "invalid_disabled_tools_container",
            }
        )
    else:
        canonical_disabled: set[str] = set()
        for value in original_disabled_tools:
            if not isinstance(value, str):
                quarantine.append(
                    {
                        "value": deepcopy(value),
                        "reason": "non_string_tool_id",
                    }
                )
                continue
            try:
                resolution = resolve_tool_identifier_for_migration(
                    value,
                    known_tool_ids=known,
                    alias_targets=alias_targets,
                )
            except ToolCatalogError:
                quarantine.append(
                    {
                        "value": value,
                        "reason": "unsafe_tool_id",
                    }
                )
                continue
            if resolution.canonical_id is not None:
                canonical_disabled.add(resolution.canonical_id)
            if resolution.disposition == ToolIdentifierDisposition.ALIAS:
                alias_pair = (
                    resolution.supplied_id,
                    resolution.canonical_id or "",
                )
                if alias_pair not in seen_aliases:
                    seen_aliases.add(alias_pair)
                    aliases.append(
                        {
                            "source": alias_pair[0],
                            "target": alias_pair[1],
                        }
                    )
            elif resolution.disposition == ToolIdentifierDisposition.LEGACY_NON_RUNTIME:
                quarantine.append(
                    {
                        "value": resolution.supplied_id,
                        "reason": resolution.reason_code,
                    }
                )
            elif resolution.disposition == ToolIdentifierDisposition.UNKNOWN:
                # Preserve the operator's deny decision as well as quarantining
                # the ID; dropping it could activate a dynamic provider tool.
                canonical_disabled.add(resolution.supplied_id)
                quarantine.append(
                    {
                        "value": resolution.supplied_id,
                        "reason": resolution.reason_code,
                    }
                )

    quarantine.sort(
        key=lambda item: (
            item["reason"],
            json.dumps(item["value"], ensure_ascii=False, sort_keys=True, default=str),
        )
    )
    aliases.sort(key=lambda item: (item["source"], item["target"]))
    migrated["disabled_tools"] = sorted(canonical_disabled)
    migrated[TOOL_SETTINGS_MIGRATION_KEY] = {
        "schema_version": TOOL_SETTINGS_MIGRATION_VERSION,
        "rollback": {
            "disabled_tools_present": had_disabled_tools,
            "disabled_tools_value": original_disabled_tools,
        },
        "aliases": aliases,
        "quarantine": quarantine,
    }
    return migrated, _tool_settings_report(migrated, changed=migrated != dict(settings))


def rollback_tool_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the pre-TAX9 ``disabled_tools`` shape and remove metadata."""

    if not isinstance(settings, Mapping):
        raise ToolSettingsMigrationError("settings must be an object")
    restored = deepcopy(dict(settings))
    metadata = restored.pop(TOOL_SETTINGS_MIGRATION_KEY, None)
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("schema_version") != TOOL_SETTINGS_MIGRATION_VERSION
    ):
        raise ToolSettingsMigrationError("supported tool settings migration metadata is required")
    rollback = metadata.get("rollback")
    if not isinstance(rollback, Mapping) or not isinstance(
        rollback.get("disabled_tools_present"), bool
    ):
        raise ToolSettingsMigrationError("tool settings rollback metadata is malformed")
    if rollback["disabled_tools_present"]:
        restored["disabled_tools"] = deepcopy(rollback.get("disabled_tools_value"))
    else:
        restored.pop("disabled_tools", None)
    return restored


# ── Settings (data/settings.json) ──

def load_settings() -> dict:
    """Load settings merged with defaults. Always returns a complete dict."""
    global _settings_cache
    now = time.monotonic()
    if _settings_cache and (now - _settings_cache[0]) < _CACHE_TTL:
        return _settings_cache[1]
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("settings must be an object")
        merged = {**DEFAULT_SETTINGS, **saved}
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_SETTINGS)
    _settings_cache = (now, merged)
    return merged


def save_settings(settings: dict):
    """Persist settings to disk (atomic; see core.atomic_io)."""
    from core.atomic_io import atomic_write_json
    atomic_write_json(SETTINGS_FILE, settings, indent=2)
    _invalidate_caches()


def migrate_tool_settings_file(settings_file: Any = None) -> dict[str, Any] | None:
    """Apply the versioned tool-settings migration without logging raw values."""

    target = settings_file or SETTINGS_FILE
    try:
        with open(target, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except FileNotFoundError:
        return None
    if not isinstance(saved, dict):
        raise ValueError("settings must be an object")

    from core.atomic_io import atomic_write_json
    from src.tool_catalog import migrate_tool_settings

    migrated, report = migrate_tool_settings(saved)
    if report.changed:
        atomic_write_json(target, migrated, indent=2)
        if str(target) == str(SETTINGS_FILE):
            _invalidate_caches()
        audit = report.audit_dict()
        logger.info(
            "Tool settings migration applied: version=%d aliases=%d quarantined=%d invalid=%d",
            audit["to_version"],
            audit["alias_rewrite_count"],
            audit["quarantined_count"],
            audit["invalid_value_count"],
        )
    return report.audit_dict()


def rollback_tool_settings_file(settings_file: Any = None) -> dict[str, Any]:
    """Restore migration-owned settings keys from the embedded rollback packet."""

    target = settings_file or SETTINGS_FILE
    with open(target, "r", encoding="utf-8") as handle:
        saved = json.load(handle)
    if not isinstance(saved, dict):
        raise ValueError("settings must be an object")

    from core.atomic_io import atomic_write_json
    from src.tool_catalog import rollback_tool_settings_migration

    restored = rollback_tool_settings_migration(saved)
    atomic_write_json(target, restored, indent=2)
    if str(target) == str(SETTINGS_FILE):
        _invalidate_caches()
    logger.info("Tool settings migration rollback applied")
    return restored


def get_setting(key: str, default: Any = None) -> Any:
    """Read a single setting value."""
    return load_settings().get(key, default)


def is_setting_overridden(key: str) -> bool:
    """True if ``key`` is explicitly present in the saved settings file.

    ``load_settings`` merges DEFAULT_SETTINGS with the saved file, so a value
    equal to its default is indistinguishable from "never set" via get_setting.
    Callers that must distinguish an explicit user choice from a default read
    the raw saved file via this. (Note: a materialized default is also "present",
    so value-sensitive callers should compare against the default — see
    ``context_budget.budget_is_explicit``.)
    """
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return isinstance(saved, dict) and key in saved
    except (FileNotFoundError, json.JSONDecodeError):
        return False


# Per-user settings (user prefs override the global admin default). Used for
# keys that a user is allowed to choose individually — currently the vision
# model + image-generation model. The owner argument is the authed username
# resolved by FastAPI deps; an empty/None owner falls through to the global.
_PER_USER_KEYS = {
    "vision_model", "vision_enabled", "vision_model_fallbacks",
    "image_model", "image_gen_enabled", "image_quality",
    # Default chat endpoint / model — without per-user resolution every new
    # account inherited whatever the most-recent admin picked, which then
    # got injected into the chat composer on first open.
    "default_endpoint_id", "default_model", "default_model_fallbacks",
    "utility_endpoint_id", "utility_model", "utility_model_fallbacks",
    "research_endpoint_id", "research_model",
    "memory.router_model", "memory.answer_model", "memory.answer_fallback_models",
    "memory.summarize_model", "memory.graph_extract_model",
    "memory.global_synthesis_model", "memory.embedding_model",
    "maintenance_model_ref", "maintenance_model_provider", "maintenance_model_fallback_ref",
    "maintenance_model_token_budget", "maintenance_model_max_input_chars",
    "maintenance_model_chunk_budget", "maintenance_model_source_ref_budget",
    "maintenance_model_latency_budget_ms", "maintenance_model_api_fallback_enabled",
}


def get_user_setting(key: str, owner: str = "", default: Any = None) -> Any:
    """Resolve `key` from the caller's per-user prefs first, falling back to
    the global setting. Only the small whitelist in `_PER_USER_KEYS` is
    eligible — for any other key this is equivalent to `get_setting(key)`.

    Falls back gracefully if the prefs module can't be imported (cycle/early
    boot) — admin-global settings keep working.
    """
    if owner and key in _PER_USER_KEYS:
        try:
            from routes.prefs_routes import _load_for_user
            prefs = _load_for_user(owner) or {}
            if key in prefs and prefs[key] not in (None, ""):
                return prefs[key]
        except Exception:
            pass
    return get_setting(key, default)


# ── Features (data/features.json) ──

def load_features() -> dict:
    """Load feature flags merged with defaults."""
    global _features_cache
    now = time.monotonic()
    if _features_cache and (now - _features_cache[0]) < _CACHE_TTL:
        return _features_cache[1]
    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("features must be an object")
        merged = {**DEFAULT_FEATURES, **saved}
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_FEATURES)
    _features_cache = (now, merged)
    return merged


def save_features(features: dict):
    """Persist feature flags to disk (atomic)."""
    from core.atomic_io import atomic_write_json
    atomic_write_json(FEATURES_FILE, features, indent=2)
    _invalidate_caches()
