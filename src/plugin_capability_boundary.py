"""Offline capability-boundary checks for plugin planning and release gates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


HOST_AGENT_CAPABILITIES = frozenset({"host_metrics", "container_runtime", "telegram_alerts"})
UI_ONLY_FORBIDDEN_CAPABILITIES = HOST_AGENT_CAPABILITIES | frozenset({"host_commands", "token_storage"})
FORBIDDEN_CORE_CAPABILITIES = frozenset({"host_commands", "docker_socket", "podman_socket"})
PERMISSION_TIER_ORDER = {
    "read_only": 1,
    "owner_scoped_write": 2,
    "admin": 3,
    "host_adjacent": 4,
    "networked": 5,
    "live_action": 6,
}
LEGACY_PERMISSIONS = frozenset({"user", "admin"})
CAPABILITY_TIER_REQUIREMENTS = {
    "local_api": "read_only",
    "notes_search": "read_only",
    "context_provider": "read_only",
    "memory_write": "owner_scoped_write",
    "owner_scoped_write": "owner_scoped_write",
    "admin_route": "admin",
    "plugin_config": "admin",
    "secret_redaction": "admin",
    "host_metrics": "host_adjacent",
    "container_runtime": "host_adjacent",
    "token_storage": "host_adjacent",
    "network_client": "networked",
    "telegram_alerts": "networked",
    "nextcloud_client": "networked",
    "telegram_send": "live_action",
    "nextcloud_write": "live_action",
    "plugin_install": "live_action",
}


@dataclass(frozen=True)
class PluginCapabilityBoundaryIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class PluginCapabilityBoundaryReport:
    ok: bool
    plugin_id: str
    plugin_kind: str
    permission: str
    required_permission_tier: str
    capabilities: tuple[str, ...]
    issues: tuple[PluginCapabilityBoundaryIssue, ...] = ()
    warnings: tuple[PluginCapabilityBoundaryIssue, ...] = ()

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "plugin_id": self.plugin_id,
            "plugin_kind": self.plugin_kind,
            "permission": self.permission,
            "required_permission_tier": self.required_permission_tier,
            "capabilities": self.capabilities,
            "errors": self.error_codes,
            "warnings": self.warning_codes,
        }


def validate_plugin_capability_boundary(manifest: Mapping[str, Any]) -> PluginCapabilityBoundaryReport:
    plugin_id = _string_value(manifest, "id") or _string_value(manifest, "name") or "unknown"
    plugin_kind = _string_value(manifest, "kind") or "core"
    permission = _string_value(manifest, "permission") or "admin"
    capabilities = _normalized_capabilities(manifest.get("capabilities", ()))
    required_permission_tier = required_permission_tier_for_capabilities(capabilities)
    issues: list[PluginCapabilityBoundaryIssue] = []
    warnings: list[PluginCapabilityBoundaryIssue] = []

    if manifest.get("capabilities") is not None and not _is_string_sequence(manifest.get("capabilities")):
        issues.append(_issue("invalid_capabilities", "capabilities must be a list of strings"))

    if permission not in PERMISSION_TIER_ORDER and permission not in LEGACY_PERMISSIONS:
        issues.append(_issue("unknown_permission_tier", "permission is not a recognized plugin permission tier"))
    elif _explicit_new_permission(manifest) and _permission_rank(permission) < _permission_rank(required_permission_tier):
        issues.append(
            _issue(
                "capability_exceeds_permission_tier",
                f"capabilities require {required_permission_tier} but permission is {permission}",
            )
        )
    elif _explicit_legacy_user_permission(manifest) and required_permission_tier not in {"read_only", "admin"}:
        warnings.append(
            _warning(
                "legacy_permission_needs_tier_review",
                f"legacy permission {permission} should migrate to {required_permission_tier}",
            )
        )

    if plugin_kind == "ui":
        overlap = sorted(set(capabilities) & UI_ONLY_FORBIDDEN_CAPABILITIES)
        if overlap:
            issues.append(_issue("ui_plugin_requests_host_capability", f"UI plugin requests host capabilities: {', '.join(overlap)}"))

    if plugin_kind == "core":
        overlap = sorted(set(capabilities) & FORBIDDEN_CORE_CAPABILITIES)
        if overlap:
            issues.append(_issue("core_plugin_requests_forbidden_host_access", f"Core plugin requests forbidden host access: {', '.join(overlap)}"))

    if plugin_kind == "host-agent":
        if "local_api" not in capabilities:
            warnings.append(_warning("host_agent_without_local_api", "host-agent plugins should expose sanitized local API snapshots"))
        if "token_storage" in capabilities and "secret_redaction" not in capabilities:
            issues.append(_issue("token_storage_without_redaction", "token storage requires secret_redaction capability"))

    if plugin_kind not in {"core", "ui", "host-agent"}:
        warnings.append(_warning("unknown_plugin_kind", "plugin kind is not recognized by the offline boundary model"))

    return PluginCapabilityBoundaryReport(
        ok=not issues,
        plugin_id=plugin_id,
        plugin_kind=plugin_kind,
        permission=permission,
        required_permission_tier=required_permission_tier,
        capabilities=capabilities,
        issues=tuple(issues),
        warnings=tuple(warnings),
    )


def required_permission_tier_for_capabilities(capabilities: Sequence[str]) -> str:
    required = "read_only"
    for capability in capabilities:
        tier = CAPABILITY_TIER_REQUIREMENTS.get(str(capability).strip(), "admin")
        if PERMISSION_TIER_ORDER[tier] > PERMISSION_TIER_ORDER[required]:
            required = tier
    return required


def _normalized_capabilities(value: Any) -> tuple[str, ...]:
    if not _is_string_sequence(value):
        return ()
    return tuple(sorted({item.strip() for item in value if item.strip()}))


def _is_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and all(isinstance(item, str) for item in value)


def _string_value(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _explicit_new_permission(manifest: Mapping[str, Any]) -> bool:
    permission = _string_value(manifest, "permission")
    return bool(permission and permission in PERMISSION_TIER_ORDER)


def _explicit_legacy_user_permission(manifest: Mapping[str, Any]) -> bool:
    return _string_value(manifest, "permission") == "user"


def _permission_rank(permission: str) -> int:
    if permission == "user":
        return PERMISSION_TIER_ORDER["read_only"]
    return PERMISSION_TIER_ORDER.get(permission, PERMISSION_TIER_ORDER["admin"])


def _issue(code: str, message: str) -> PluginCapabilityBoundaryIssue:
    return PluginCapabilityBoundaryIssue(code=code, message=message, severity="error")


def _warning(code: str, message: str) -> PluginCapabilityBoundaryIssue:
    return PluginCapabilityBoundaryIssue(code=code, message=message, severity="warning")
