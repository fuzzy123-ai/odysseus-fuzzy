"""Canonical settings service for UI and agent settings flows.

The service is the first active layer on top of the passive registry. It keeps
the existing storage files intact while centralizing validation, scope
resolution, feature writes, user overrides, and policy-shaped results.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import src.settings as settings_store
from src.settings_registry import (
    SettingRegistryEntry,
    SettingSource,
    get_registry_entry,
    iter_registry_entries,
    resolve_setting_alias,
)


_INT_RANGES: dict[str, tuple[int, int]] = {
    "agent_max_rounds": (1, 200),
    "agent_max_tool_calls": (0, 1000),
    "research_extraction_concurrency": (1, 20),
    "search_result_count": (1, 50),
    "skill_max_injected": (0, 20),
}

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}


@dataclass
class SettingsServiceError(ValueError):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": self.code,
            "error": self.message,
            "details": self.details or {},
        }


def _load_user_prefs(owner: str | None) -> dict[str, Any]:
    from routes.prefs_routes import _load_for_user

    return dict(_load_for_user(owner) or {})


def _save_user_prefs(owner: str | None, prefs: dict[str, Any]) -> None:
    from routes.prefs_routes import _save_for_user

    _save_for_user(owner, prefs)


def _entry_for(key: str, store: SettingSource = "setting") -> SettingRegistryEntry:
    resolved = resolve_setting_alias(key) if store == "setting" else (key or "").strip()
    try:
        return get_registry_entry(resolved, source=store)
    except KeyError as exc:
        raise SettingsServiceError("unknown_key", f"Unknown {store} key '{key}'.", {"key": key}) from exc


def _resolve_scope(entry: SettingRegistryEntry, owner: str | None, scope: str = "auto", *, for_write: bool = False) -> str:
    requested = (scope or "auto").strip().lower()
    if requested not in {"auto", "user", "global"}:
        raise SettingsServiceError("invalid_scope", f"Invalid scope '{scope}'.", {"scope": scope})
    if entry.source == "feature":
        if requested == "user":
            raise SettingsServiceError("invalid_scope", "Feature flags are global-only.", {"key": entry.key})
        return "global"
    if requested == "global":
        if entry.scope not in {"global", "both"}:
            raise SettingsServiceError("invalid_scope", f"{entry.key} is not global-scoped.", {"key": entry.key})
        return "global"
    if requested == "user":
        if entry.scope not in {"user", "both"}:
            raise SettingsServiceError("invalid_scope", f"{entry.key} does not support user scope.", {"key": entry.key})
        if for_write and not owner:
            raise SettingsServiceError("owner_required", f"{entry.key} needs an owner for user scope.", {"key": entry.key})
        return "user"
    if entry.scope == "both" and owner:
        return "user"
    return "global"


def _coerce_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    raise SettingsServiceError("invalid_value", f"{key} must be a boolean.", {"key": key, "value": value})


def _coerce_value(entry: SettingRegistryEntry, value: Any) -> Any:
    key = entry.key
    if entry.value_type == "bool":
        return _coerce_bool(value, key)
    if entry.value_type == "int":
        try:
            coerced = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsServiceError("invalid_value", f"{key} must be an integer.", {"key": key}) from exc
        if key in _INT_RANGES:
            lo, hi = _INT_RANGES[key]
            coerced = max(lo, min(coerced, hi))
        return coerced
    if entry.value_type == "float":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise SettingsServiceError("invalid_value", f"{key} must be a number.", {"key": key}) from exc
    if entry.value_type == "enum":
        normalized = str(value).strip().lower()
        if normalized not in entry.enum_values:
            allowed = ", ".join(entry.enum_values)
            raise SettingsServiceError("invalid_value", f"{key} must be one of: {allowed}.", {"key": key})
        return normalized
    if entry.value_type == "list":
        if not isinstance(value, list):
            raise SettingsServiceError("invalid_value", f"{key} must be a list.", {"key": key})
        _validate_structured_value(entry, value)
        return deepcopy(value)
    if entry.value_type == "object":
        if not isinstance(value, dict):
            raise SettingsServiceError("invalid_value", f"{key} must be an object.", {"key": key})
        _validate_structured_value(entry, value)
        return deepcopy(value)
    return str(value) if value is not None else ""


def _validate_structured_value(entry: SettingRegistryEntry, value: Any) -> None:
    schema = entry.structured_schema or {}
    if not schema:
        return
    item_schema = schema.get("items")
    if schema.get("type") == "list":
        if not isinstance(value, list):
            raise SettingsServiceError("invalid_value", f"{entry.key} must be a list.", {"key": entry.key})
        if item_schema == "str":
            if not all(isinstance(item, str) for item in value):
                raise SettingsServiceError("invalid_value", f"{entry.key} entries must be strings.", {"key": entry.key})
        elif item_schema == "absolute_path":
            for item in value:
                if not isinstance(item, str) or not Path(item).is_absolute():
                    raise SettingsServiceError(
                        "invalid_value",
                        f"{entry.key} entries must be absolute paths.",
                        {"key": entry.key},
                    )
        elif isinstance(item_schema, dict):
            required = set(item_schema.get("required") or ())
            for item in value:
                if not isinstance(item, dict):
                    raise SettingsServiceError("invalid_value", f"{entry.key} entries must be objects.", {"key": entry.key})
                missing = [name for name in required if not isinstance(item.get(name), str) or not item.get(name)]
                if missing:
                    raise SettingsServiceError(
                        "invalid_value",
                        f"{entry.key} entry missing required fields: {', '.join(missing)}.",
                        {"key": entry.key, "missing": missing},
                    )
    elif schema.get("type") == "object":
        if not isinstance(value, dict):
            raise SettingsServiceError("invalid_value", f"{entry.key} must be an object.", {"key": entry.key})
        if schema.get("additional_properties") == "str" and not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise SettingsServiceError("invalid_value", f"{entry.key} entries must be string:string.", {"key": entry.key})
        required = set(schema.get("required") or ())
        missing = sorted(required - set(value))
        if missing:
            raise SettingsServiceError(
                "invalid_value",
                f"{entry.key} is missing required fields: {', '.join(missing)}.",
                {"key": entry.key, "missing": missing},
            )
        properties = schema.get("properties") or {}
        if schema.get("additional_properties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise SettingsServiceError(
                    "invalid_value",
                    f"{entry.key} has unsupported fields: {', '.join(unexpected)}.",
                    {"key": entry.key, "unexpected": unexpected},
                )
        for property_name, property_schema in properties.items():
            if property_name not in value or not isinstance(property_schema, dict):
                continue
            property_value = value[property_name]
            if property_schema.get("type") == "object":
                if not isinstance(property_value, dict):
                    raise SettingsServiceError(
                        "invalid_value",
                        f"{entry.key}.{property_name} must be an object.",
                        {"key": entry.key, "path": property_name},
                    )
                if property_schema.get("additional_properties") == "positive_int":
                    valid = all(
                        isinstance(nested_key, str)
                        and bool(nested_key.strip())
                        and isinstance(nested_value, int)
                        and not isinstance(nested_value, bool)
                        and nested_value > 0
                        for nested_key, nested_value in property_value.items()
                    )
                    if not valid:
                        raise SettingsServiceError(
                            "invalid_value",
                            f"{entry.key}.{property_name} entries must map non-empty names to positive integers.",
                            {"key": entry.key, "path": property_name},
                        )


def _policy_result(entry: SettingRegistryEntry, *, actor: str, confirmed: bool) -> dict[str, Any] | None:
    actor_key = (actor or "agent").strip().lower()
    if actor_key in {"ui", "system"}:
        return None
    if entry.agent_access == "human_only":
        return {
            "ok": False,
            "status": "human_only",
            "key": entry.key,
            "source": entry.source,
            "value_visible": False,
            "reason": "This setting is human-only.",
        }
    if entry.agent_access == "secret_handoff":
        return {
            "ok": False,
            "status": "secret_handoff_required",
            "key": entry.key,
            "source": entry.source,
            "value_visible": False,
            "reason": "Secrets must be collected through a secure handoff, not chat text.",
        }
    if entry.agent_access == "confirm" and not confirmed:
        return {
            "ok": False,
            "status": "confirmation_required",
            "key": entry.key,
            "source": entry.source,
            "requires_confirmation": True,
            "value_visible": False,
            "reason": "This setting requires explicit confirmation before an agent may change it.",
        }
    return None


def _read_global(entry: SettingRegistryEntry) -> Any:
    if entry.source == "feature":
        return deepcopy(settings_store.load_features().get(entry.key, entry.default))
    return deepcopy(settings_store.load_settings().get(entry.key, entry.default))


def _write_global(entry: SettingRegistryEntry, value: Any) -> None:
    if entry.source == "feature":
        features = settings_store.load_features()
        features[entry.key] = value
        settings_store.save_features(features)
        return
    settings = settings_store.load_settings()
    settings[entry.key] = value
    settings_store.save_settings(settings)


def _read_effective(entry: SettingRegistryEntry, owner: str | None, scope: str) -> tuple[Any, str]:
    resolved_scope = _resolve_scope(entry, owner, scope, for_write=False)
    if resolved_scope == "user" and owner:
        prefs = _load_user_prefs(owner)
        if entry.key in prefs and prefs[entry.key] not in (None, ""):
            return deepcopy(prefs[entry.key]), "user"
    return _read_global(entry), "global"


def _write_user(entry: SettingRegistryEntry, owner: str, value: Any) -> None:
    prefs = _load_user_prefs(owner)
    prefs[entry.key] = value
    _save_user_prefs(owner, prefs)


def _reset_user(entry: SettingRegistryEntry, owner: str) -> Any:
    prefs = _load_user_prefs(owner)
    previous = deepcopy(prefs.get(entry.key, entry.default))
    prefs.pop(entry.key, None)
    _save_user_prefs(owner, prefs)
    return previous


def _visible_value(entry: SettingRegistryEntry, value: Any, *, include_secrets: bool = False) -> Any:
    if entry.secret and not include_secrets:
        return None
    return deepcopy(value)


def _base_result(entry: SettingRegistryEntry, *, scope: str, owner: str | None = None) -> dict[str, Any]:
    return {
        "key": entry.key,
        "source": entry.source,
        "scope": scope,
        "owner_present": bool(owner),
        "entry": entry.to_public_dict(),
    }


def list_settings(
    *,
    owner: str | None = None,
    scope: str = "auto",
    store: SettingSource | None = None,
    include_secrets: bool = False,
) -> dict[str, Any]:
    items = []
    for entry in iter_registry_entries(store):
        try:
            value, effective_scope = _read_effective(entry, owner, scope)
        except SettingsServiceError:
            value, effective_scope = _read_global(entry), "global"
        item = entry.to_public_dict()
        item.update({
            "effective_scope": effective_scope,
            "value": _visible_value(entry, value, include_secrets=include_secrets),
            "value_visible": bool(include_secrets or not entry.secret),
        })
        items.append(item)
    return {"ok": True, "status": "listed", "count": len(items), "settings": items}


def get_setting(
    key: str,
    *,
    owner: str | None = None,
    scope: str = "auto",
    store: SettingSource = "setting",
    include_secret: bool = False,
) -> dict[str, Any]:
    entry = _entry_for(key, store)
    requested_scope = _resolve_scope(entry, owner, scope, for_write=False)
    value, effective_scope = _read_effective(entry, owner, scope)
    result = _base_result(entry, scope=requested_scope, owner=owner)
    result.update({
        "ok": True,
        "status": "read",
        "effective_scope": effective_scope,
        "value": _visible_value(entry, value, include_secrets=include_secret),
        "value_visible": bool(include_secret or not entry.secret),
    })
    return result


def set_setting(
    key: str,
    value: Any,
    *,
    owner: str | None = None,
    scope: str = "auto",
    store: SettingSource = "setting",
    actor: str = "agent",
    confirmed: bool = False,
) -> dict[str, Any]:
    entry = _entry_for(key, store)
    resolved_scope = _resolve_scope(entry, owner, scope, for_write=True)
    policy = _policy_result(entry, actor=actor, confirmed=confirmed)
    if policy is not None:
        return policy
    coerced = _coerce_value(entry, value)
    previous, previous_scope = _read_effective(entry, owner, resolved_scope)
    if resolved_scope == "user":
        _write_user(entry, str(owner), coerced)
    else:
        _write_global(entry, coerced)
    result = _base_result(entry, scope=resolved_scope, owner=owner)
    result.update({
        "ok": True,
        "status": "updated",
        "effective_scope": resolved_scope,
        "previous": _visible_value(entry, previous),
        "previous_scope": previous_scope,
        "value": _visible_value(entry, coerced),
        "value_visible": not entry.secret,
    })
    return result


def _patch_list(current: list[Any], patch: dict[str, Any]) -> list[Any]:
    op = str(patch.get("op") or "").strip().lower()
    if op == "append":
        return [*current, patch.get("value")]
    if op == "remove":
        target = patch.get("value")
        return [item for item in current if item != target]
    if op == "replace":
        value = patch.get("value")
        if not isinstance(value, list):
            raise SettingsServiceError("invalid_patch", "replace needs a list value.")
        return deepcopy(value)
    if op == "clear":
        return []
    raise SettingsServiceError("invalid_patch", f"Unsupported list patch op '{op}'.")


def _patch_object(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    op = str(patch.get("op") or "").strip().lower()
    if op == "replace":
        value = patch.get("value")
        if not isinstance(value, dict):
            raise SettingsServiceError("invalid_patch", "replace needs an object value.")
        return deepcopy(value)
    key = str(patch.get("path") or patch.get("key") or "").strip()
    if not key:
        raise SettingsServiceError("invalid_patch", "object patch needs path or key.")
    updated = deepcopy(current)
    if op == "set":
        updated[key] = patch.get("value")
        return updated
    if op == "remove":
        updated.pop(key, None)
        return updated
    raise SettingsServiceError("invalid_patch", f"Unsupported object patch op '{op}'.")


def patch_setting(
    key: str,
    patch: dict[str, Any],
    *,
    owner: str | None = None,
    scope: str = "auto",
    actor: str = "agent",
    confirmed: bool = False,
) -> dict[str, Any]:
    entry = _entry_for(key, "setting")
    resolved_scope = _resolve_scope(entry, owner, scope, for_write=True)
    policy = _policy_result(entry, actor=actor, confirmed=confirmed)
    if policy is not None:
        return policy
    if entry.value_type not in {"list", "object"}:
        raise SettingsServiceError("invalid_patch", f"{entry.key} is not patchable.", {"key": entry.key})
    current, _effective_scope = _read_effective(entry, owner, resolved_scope)
    if entry.value_type == "list":
        updated = _patch_list(list(current or []), patch)
    else:
        updated = _patch_object(dict(current or {}), patch)
    return set_setting(
        entry.key,
        updated,
        owner=owner,
        scope=resolved_scope,
        store="setting",
        actor=actor,
        confirmed=confirmed,
    )


def reset_setting(
    key: str,
    *,
    owner: str | None = None,
    scope: str = "auto",
    store: SettingSource = "setting",
    actor: str = "agent",
    confirmed: bool = False,
) -> dict[str, Any]:
    entry = _entry_for(key, store)
    resolved_scope = _resolve_scope(entry, owner, scope, for_write=True)
    policy = _policy_result(entry, actor=actor, confirmed=confirmed)
    if policy is not None:
        return policy
    if resolved_scope == "user":
        previous = _reset_user(entry, str(owner))
        value, effective_scope = _read_effective(entry, owner, "auto")
    else:
        previous = _read_global(entry)
        _write_global(entry, deepcopy(entry.default))
        value, effective_scope = deepcopy(entry.default), "global"
    result = _base_result(entry, scope=resolved_scope, owner=owner)
    result.update({
        "ok": True,
        "status": "reset",
        "effective_scope": effective_scope,
        "previous": _visible_value(entry, previous),
        "value": _visible_value(entry, value),
        "value_visible": not entry.secret,
    })
    return result


def explain_setting(
    key: str,
    *,
    owner: str | None = None,
    scope: str = "auto",
    store: SettingSource = "setting",
) -> dict[str, Any]:
    entry = _entry_for(key, store)
    try:
        current = get_setting(key, owner=owner, scope=scope, store=store)
    except SettingsServiceError:
        current = {}
    result = _base_result(entry, scope=_resolve_scope(entry, owner, scope), owner=owner)
    result.update({
        "ok": True,
        "status": "explained",
        "agent_access": entry.agent_access,
        "requires_confirmation": entry.agent_access == "confirm",
        "secret_handoff_required": entry.agent_access == "secret_handoff",
        "structured_schema": entry.structured_schema,
        "enum_values": entry.enum_values,
        "value": current.get("value"),
        "value_visible": bool(current.get("value_visible", not entry.secret)),
    })
    return result
