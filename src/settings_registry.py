"""Machine-readable settings and feature registry.

This module is intentionally passive: it describes existing settings, feature
flags, scopes, and agent access policy without changing how values are read or
written. The service layer can use this contract in a later slice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

from src.settings import DEFAULT_FEATURES, DEFAULT_SETTINGS, _PER_USER_KEYS


SettingSource = Literal["setting", "feature"]
ValueType = Literal["bool", "int", "float", "str", "enum", "list", "object"]
Scope = Literal["global", "user", "both"]
AgentAccess = Literal["read", "write", "confirm", "secret_handoff", "human_only"]


@dataclass(frozen=True)
class SettingRegistryEntry:
    key: str
    source: SettingSource
    value_type: ValueType
    default: Any
    scope: Scope
    agent_access: AgentAccess
    secret: bool = False
    structured_schema: dict[str, Any] | None = None
    aliases: tuple[str, ...] = ()
    category: str = "general"
    requires_restart: bool = False
    owner_policy: str = "admin_global"
    enum_values: tuple[str, ...] = ()
    description: str = ""

    @property
    def registry_key(self) -> str:
        return f"{self.source}:{self.key}"

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["registry_key"] = self.registry_key
        return data


SETTING_ALIASES: dict[str, str] = {
    "agent timeout": "agent_stream_timeout_seconds",
    "agent tool calls": "agent_max_tool_calls",
    "background model": "task_model",
    "chat model": "default_model",
    "default endpoint": "default_endpoint_id",
    "default model": "default_model",
    "hard max": "agent_input_token_hard_max",
    "image gen": "image_gen_enabled",
    "image generation": "image_gen_enabled",
    "image model": "image_model",
    "image quality": "image_quality",
    "input budget": "agent_input_token_budget",
    "input budget cap": "agent_input_token_hard_max",
    "ntfy topic": "reminder_ntfy_topic",
    "reminder channel": "reminder_channel",
    "reminder telegram dry run": "reminder_telegram_dry_run",
    "reminders": "reminder_channel",
    "telegram reminder dry run": "reminder_telegram_dry_run",
    "research max tokens": "research_max_tokens",
    "research model": "research_model",
    "result count": "search_result_count",
    "search engine": "search_provider",
    "search provider": "search_provider",
    "search results": "search_result_count",
    "speech speed": "tts_speed",
    "speech to text": "stt_enabled",
    "stt": "stt_enabled",
    "task model": "task_model",
    "teacher": "teacher_enabled",
    "teacher model": "teacher_model",
    "text to speech": "tts_enabled",
    "token budget": "agent_input_token_budget",
    "token budget cap": "agent_input_token_hard_max",
    "transcription": "stt_enabled",
    "tts": "tts_enabled",
    "tts provider": "tts_provider",
    "tts voice": "tts_voice",
    "utility model": "utility_model",
    "vision": "vision_enabled",
    "vision model": "vision_model",
    "voice": "tts_voice",
    "voice speed": "tts_speed",
    "webhook integration": "reminder_webhook_integration_id",
    "webhook payload": "reminder_webhook_payload_template",
    "webhook template": "reminder_webhook_payload_template",
}


_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "image_quality": ("low", "medium", "high"),
    "reminder_channel": ("browser", "email", "ntfy", "webhook", "telegram"),
    "search_safesearch": ("strict", "moderate", "off"),
}


_SECRET_KEYS = {
    "brave_api_key",
    "google_pse_key",
    "google_pse_cx",
    "tavily_api_key",
    "serper_api_key",
}


_CONFIRM_KEYS = {
    "app_public_url",
    "reminder_email_to",
    "reminder_webhook_integration_id",
    "reminder_webhook_payload_template",
    "search_url",
    "tool_path_extra_roots",
}


_STRUCTURED_SCHEMAS: dict[str, dict[str, Any]] = {
    "default_model_fallbacks": {
        "type": "list",
        "items": {"type": "object", "required": ["endpoint_id"], "properties": {"endpoint_id": "str", "model": "str"}},
        "patch_ops": ("append", "remove", "replace", "clear"),
    },
    "keybinds": {
        "type": "object",
        "additional_properties": "str",
        "patch_ops": ("set", "remove", "replace"),
    },
    "memory.answer_fallback_models": {
        "type": "list",
        "items": "str",
        "patch_ops": ("append", "remove", "replace", "clear"),
    },
    "search_fallback_chain": {
        "type": "list",
        "items": "str",
        "patch_ops": ("append", "remove", "replace", "clear"),
    },
    "tool_path_extra_roots": {
        "type": "list",
        "items": "absolute_path",
        "patch_ops": ("append", "remove", "replace", "clear"),
        "requires_confirmation": True,
    },
    "utility_model_fallbacks": {
        "type": "list",
        "items": {"type": "object", "required": ["endpoint_id"], "properties": {"endpoint_id": "str", "model": "str"}},
        "patch_ops": ("append", "remove", "replace", "clear"),
    },
    "vision_model_fallbacks": {
        "type": "list",
        "items": {"type": "object", "required": ["endpoint_id"], "properties": {"endpoint_id": "str", "model": "str"}},
        "patch_ops": ("append", "remove", "replace", "clear"),
    },
}


_CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("agent_", "agent_runtime"),
    ("context_", "agent_runtime"),
    ("session_", "session_runtime"),
    ("default_", "models"),
    ("utility_", "models"),
    ("research_", "research"),
    ("task_", "models"),
    ("vision_", "vision"),
    ("image_", "image"),
    ("memory.", "memory"),
    ("maintenance_", "memory"),
    ("tts_", "speech"),
    ("stt_", "speech"),
    ("search_", "search"),
    ("reminder_", "reminders"),
    ("skill_", "skills"),
)


_EXPLICIT_CATEGORIES: dict[str, str] = {
    "agent_email_confirm": "email_safety",
    "app_public_url": "network",
    "brave_api_key": "search",
    "dsgvo_mode": "privacy",
    "google_pse_cx": "search",
    "google_pse_key": "search",
    "keybinds": "ui_preferences",
    "serper_api_key": "search",
    "tavily_api_key": "search",
    "teacher_enabled": "teacher",
    "teacher_model": "teacher",
    "tool_path_extra_roots": "tool_security",
    "urgent_email_prompt": "email_safety",
}


def _value_type(key: str, default: Any) -> ValueType:
    if key in _ENUM_VALUES:
        return "enum"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, list):
        return "list"
    if isinstance(default, dict):
        return "object"
    return "str"


def _category_for(key: str) -> str:
    if key in _EXPLICIT_CATEGORIES:
        return _EXPLICIT_CATEGORIES[key]
    for prefix, category in _CATEGORY_PREFIXES:
        if key.startswith(prefix):
            return category
    return "general"


def _scope_for_setting(key: str) -> Scope:
    return "both" if key in _PER_USER_KEYS else "global"


def _owner_policy_for(scope: Scope, access: AgentAccess) -> str:
    if access == "human_only":
        return "human_only"
    if access == "secret_handoff":
        return "secure_secret_handoff"
    if access == "confirm":
        return "admin_confirmed"
    if scope == "both":
        return "user_scope_default_admin_global_explicit"
    if scope == "user":
        return "user_only"
    return "admin_global"


def _agent_access_for_setting(key: str) -> AgentAccess:
    if key in _SECRET_KEYS:
        return "secret_handoff"
    if key in _CONFIRM_KEYS:
        return "confirm"
    return "write"


def _aliases_for(key: str) -> tuple[str, ...]:
    return tuple(sorted(alias for alias, target in SETTING_ALIASES.items() if target == key))


def _setting_entry(key: str, default: Any) -> SettingRegistryEntry:
    scope = _scope_for_setting(key)
    access = _agent_access_for_setting(key)
    return SettingRegistryEntry(
        key=key,
        source="setting",
        value_type=_value_type(key, default),
        default=default,
        scope=scope,
        agent_access=access,
        secret=key in _SECRET_KEYS,
        structured_schema=_STRUCTURED_SCHEMAS.get(key),
        aliases=_aliases_for(key),
        category=_category_for(key),
        requires_restart=False,
        owner_policy=_owner_policy_for(scope, access),
        enum_values=_ENUM_VALUES.get(key, ()),
    )


def _feature_entry(key: str, default: Any) -> SettingRegistryEntry:
    access: AgentAccess = "confirm"
    scope: Scope = "global"
    return SettingRegistryEntry(
        key=key,
        source="feature",
        value_type=_value_type(key, default),
        default=default,
        scope=scope,
        agent_access=access,
        secret=False,
        category="feature_flags",
        owner_policy=_owner_policy_for(scope, access),
        description="Global feature visibility flag.",
    )


def iter_registry_entries(source: SettingSource | None = None) -> Iterable[SettingRegistryEntry]:
    for key, default in DEFAULT_SETTINGS.items():
        if source in (None, "setting"):
            yield _setting_entry(key, default)
    for key, default in DEFAULT_FEATURES.items():
        if source in (None, "feature"):
            yield _feature_entry(key, default)


def build_settings_registry(source: SettingSource | None = None) -> dict[str, SettingRegistryEntry]:
    return {entry.registry_key: entry for entry in iter_registry_entries(source)}


def get_registry_entry(key: str, source: SettingSource = "setting") -> SettingRegistryEntry:
    registry_key = f"{source}:{key}"
    registry = build_settings_registry(source)
    try:
        return registry[registry_key]
    except KeyError as exc:
        raise KeyError(f"unknown {source} key: {key}") from exc


def resolve_setting_alias(key_or_alias: str) -> str:
    normalized = (key_or_alias or "").strip().lower()
    if normalized in DEFAULT_SETTINGS:
        return normalized
    return SETTING_ALIASES.get(normalized, (key_or_alias or "").strip())


def public_registry(source: SettingSource | None = None) -> list[dict[str, Any]]:
    return [entry.to_public_dict() for entry in iter_registry_entries(source)]
