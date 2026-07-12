"""Offline policy checks for Odysseus plugin manifests and registries.

This module intentionally does not import or execute plugin code. It validates
plain dictionaries so roadmap/plugin foundations can be audited without touching
the runtime plugin manager or downloading third-party archives.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


PLUGIN_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9_.-]+)?$")
CAPABILITY_RE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}$")
SHA256_RE = re.compile(r"[0-9a-f]{64}$")
PERMISSION_TIERS = frozenset(
    {
        "read_only",
        "owner_scoped_write",
        "admin",
        "host_adjacent",
        "networked",
        "live_action",
    }
)
LEGACY_PERMISSIONS = frozenset({"user"})
ALLOWED_PERMISSIONS = PERMISSION_TIERS | LEGACY_PERMISSIONS
ALLOWED_LIFECYCLES = frozenset(
    {
        "discovered",
        "audited",
        "loadable",
        "loaded",
        "degraded",
        "disabled",
        "quarantined",
        "uninstallable",
    }
)


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    message: str
    field: str = ""
    severity: str = "error"


@dataclass(frozen=True)
class PluginPolicyReport:
    ok: bool
    issues: tuple[PolicyIssue, ...] = ()
    warnings: tuple[PolicyIssue, ...] = ()
    normalized: dict[str, Any] = field(default_factory=dict)

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.warnings)


def validate_local_manifest(manifest: Mapping[str, Any]) -> PluginPolicyReport:
    """Validate a plugin.py ``PLUGIN`` dict without importing plugin code."""
    issues: list[PolicyIssue] = []
    warnings: list[PolicyIssue] = []
    normalized: dict[str, Any] = {}

    name = _required_string(manifest, "name", issues)
    if name:
        normalized["name"] = name

    version = _optional_string(manifest, "version")
    if version:
        normalized["version"] = version
        if not VERSION_RE.fullmatch(version):
            issues.append(_issue("invalid_version", "version must be semver-like", "version"))
    else:
        warnings.append(_warning("missing_version", "version is recommended for upgrade visibility", "version"))

    permission = _optional_string(manifest, "permission") or "admin"
    normalized["permission"] = permission
    if permission not in ALLOWED_PERMISSIONS:
        allowed = ", ".join(sorted(ALLOWED_PERMISSIONS))
        issues.append(_issue("invalid_permission", f"permission must be one of: {allowed}", "permission"))

    manifest_version = _optional_schema_version(manifest, "manifest_version", issues)
    if manifest_version:
        normalized["manifest_version"] = manifest_version
    schema_version = _optional_schema_version(manifest, "schema_version", issues)
    if schema_version:
        normalized["schema_version"] = schema_version

    capabilities = _optional_capabilities(manifest, issues)
    if capabilities is not None:
        normalized["capabilities"] = capabilities

    compatibility = _optional_compatibility(manifest, issues)
    if compatibility is not None:
        normalized["compatibility"] = compatibility

    lifecycle = _optional_lifecycle(manifest, issues)
    if lifecycle:
        normalized["lifecycle"] = lifecycle

    requires = manifest.get("requires", [])
    if requires is None:
        requires = []
    if not isinstance(requires, Sequence) or isinstance(requires, (str, bytes)):
        issues.append(_issue("invalid_requires", "requires must be a list of strings", "requires"))
    else:
        normalized["requires"] = tuple(str(item).strip() for item in requires if str(item).strip())

    ui = manifest.get("ui")
    if ui is not None:
        if not isinstance(ui, Mapping):
            issues.append(_issue("invalid_ui", "ui must be an object", "ui"))
        else:
            open_path = _optional_string(ui, "open")
            if open_path is not None:
                if not _is_safe_ui_path(open_path):
                    issues.append(_issue("unsafe_ui_open", "ui.open must be a safe absolute app path", "ui.open"))
                else:
                    normalized["ui.open"] = open_path

    category = _optional_string(manifest, "category")
    if category:
        normalized["category"] = category
    description = _optional_string(manifest, "description")
    if description:
        normalized["description"] = description

    return PluginPolicyReport(not issues, tuple(issues), tuple(warnings), normalized)


def validate_registry_document(document: Any) -> PluginPolicyReport:
    """Validate a registry JSON payload before browse/install code trusts it."""
    issues: list[PolicyIssue] = []
    warnings: list[PolicyIssue] = []
    normalized: dict[str, Any] = {}

    entries: Any
    if isinstance(document, Mapping):
        version = document.get("version")
        if version not in (None, 1):
            warnings.append(_warning("unknown_registry_version", "registry version is not recognized", "version"))
        entries = document.get("plugins")
    else:
        entries = document

    if not isinstance(entries, list):
        issues.append(_issue("invalid_registry_shape", "registry must be a list or object with plugins list", "plugins"))
        return PluginPolicyReport(False, tuple(issues), tuple(warnings), normalized)

    seen: set[str] = set()
    plugin_ids: list[str] = []
    for index, entry in enumerate(entries):
        field_prefix = f"plugins[{index}]"
        if not isinstance(entry, Mapping):
            issues.append(_issue("invalid_entry", "plugin entry must be an object", field_prefix))
            continue
        entry_report = validate_registry_entry(entry, field_prefix=field_prefix)
        issues.extend(entry_report.issues)
        warnings.extend(entry_report.warnings)
        plugin_id = entry_report.normalized.get("id")
        if plugin_id:
            if plugin_id in seen:
                issues.append(_issue("duplicate_plugin_id", f"duplicate plugin id: {plugin_id}", f"{field_prefix}.id"))
            else:
                seen.add(plugin_id)
                plugin_ids.append(plugin_id)

    normalized["plugin_ids"] = tuple(sorted(plugin_ids))
    normalized["count"] = len(plugin_ids)
    return PluginPolicyReport(not issues, tuple(issues), tuple(warnings), normalized)


def validate_registry_entry(entry: Mapping[str, Any], *, field_prefix: str = "plugin") -> PluginPolicyReport:
    issues: list[PolicyIssue] = []
    warnings: list[PolicyIssue] = []
    normalized: dict[str, Any] = {}

    plugin_id = _required_string(entry, "id", issues, field_prefix)
    if plugin_id:
        normalized["id"] = plugin_id
        if not PLUGIN_ID_RE.fullmatch(plugin_id):
            issues.append(_issue("invalid_plugin_id", "plugin id is not filesystem-safe", f"{field_prefix}.id"))

    for key in ("name", "category", "description", "download", "sha256"):
        value = _required_string(entry, key, issues, field_prefix)
        if value:
            normalized[key] = value

    version = _required_string(entry, "version", issues, field_prefix)
    if version:
        normalized["version"] = version
        if not VERSION_RE.fullmatch(version):
            issues.append(_issue("invalid_version", "version must be semver-like", f"{field_prefix}.version"))

    download = normalized.get("download")
    if download and not _is_https_url(download):
        issues.append(_issue("download_not_https", "download must be an https URL", f"{field_prefix}.download"))

    homepage = _optional_string(entry, "homepage")
    if homepage:
        normalized["homepage"] = homepage
        if not _is_https_url(homepage):
            warnings.append(_warning("homepage_not_https", "homepage should be an https URL", f"{field_prefix}.homepage"))

    sha256 = normalized.get("sha256")
    if sha256 and not SHA256_RE.fullmatch(sha256):
        issues.append(_issue("invalid_sha256", "sha256 must be 64 lowercase hex characters", f"{field_prefix}.sha256"))

    return PluginPolicyReport(not issues, tuple(issues), tuple(warnings), normalized)


def summarize_registry(document: Any) -> dict[str, Any]:
    """Return a compact audit summary for UI/tests/runbooks."""
    report = validate_registry_document(document)
    return {
        "ok": report.ok,
        "count": report.normalized.get("count", 0),
        "plugin_ids": report.normalized.get("plugin_ids", ()),
        "errors": report.error_codes,
        "warnings": report.warning_codes,
    }


def _required_string(
    data: Mapping[str, Any],
    key: str,
    issues: list[PolicyIssue],
    prefix: str = "",
) -> str | None:
    value = data.get(key)
    field = f"{prefix}.{key}" if prefix else key
    if not isinstance(value, str) or not value.strip():
        issues.append(_issue("missing_required_field", f"{key} is required", field))
        return None
    return value.strip()


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _optional_schema_version(data: Mapping[str, Any], key: str, issues: list[PolicyIssue]) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        issues.append(_issue(f"invalid_{key}", f"{key} must be semver-like", key))
        return None
    if isinstance(value, int):
        version = str(value)
    elif isinstance(value, str):
        version = value.strip()
    else:
        issues.append(_issue(f"invalid_{key}", f"{key} must be semver-like", key))
        return None
    if not version or not VERSION_RE.fullmatch(version):
        issues.append(_issue(f"invalid_{key}", f"{key} must be semver-like", key))
        return None
    return version


def _optional_capabilities(data: Mapping[str, Any], issues: list[PolicyIssue]) -> tuple[str, ...] | None:
    value = data.get("capabilities")
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        issues.append(_issue("invalid_capabilities", "capabilities must be a list of safe slugs", "capabilities"))
        return ()

    capabilities: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            issues.append(
                _issue(
                    "invalid_capabilities",
                    "capabilities must contain only safe slugs",
                    f"capabilities[{index}]",
                )
            )
            continue
        capability = item.strip().lower()
        if not capability or not CAPABILITY_RE.fullmatch(capability):
            issues.append(_issue("invalid_capabilities", "capability must be a safe slug", f"capabilities[{index}]"))
            continue
        capabilities.add(capability)
    return tuple(sorted(capabilities))


def _optional_compatibility(data: Mapping[str, Any], issues: list[PolicyIssue]) -> dict[str, str] | None:
    value = data.get("compatibility")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        issues.append(_issue("invalid_compatibility", "compatibility must be an object", "compatibility"))
        return {}

    compatibility: dict[str, str] = {}
    for key in ("min_odysseus", "max_odysseus"):
        raw_version = value.get(key)
        if raw_version is None:
            continue
        if not isinstance(raw_version, str):
            issues.append(
                _issue("invalid_compatibility", f"compatibility.{key} must be semver-like", f"compatibility.{key}")
            )
            continue
        version = raw_version.strip()
        if not version or not VERSION_RE.fullmatch(version):
            issues.append(
                _issue("invalid_compatibility", f"compatibility.{key} must be semver-like", f"compatibility.{key}")
            )
            continue
        compatibility[key] = version
    return compatibility


def _optional_lifecycle(data: Mapping[str, Any], issues: list[PolicyIssue]) -> str | None:
    value = data.get("lifecycle")
    if value is None:
        return None
    if not isinstance(value, str):
        issues.append(_issue("invalid_lifecycle", "lifecycle is not recognized", "lifecycle"))
        return None
    lifecycle = value.strip()
    if not lifecycle:
        return None
    if lifecycle not in ALLOWED_LIFECYCLES:
        issues.append(_issue("invalid_lifecycle", "lifecycle is not recognized", "lifecycle"))
        return None
    return lifecycle


def _is_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _is_safe_ui_path(value: str) -> bool:
    lowered = value.strip().lower()
    return value.startswith("/") and not value.startswith("//") and not lowered.startswith("javascript:")


def _issue(code: str, message: str, field: str) -> PolicyIssue:
    return PolicyIssue(code=code, message=message, field=field, severity="error")


def _warning(code: str, message: str, field: str) -> PolicyIssue:
    return PolicyIssue(code=code, message=message, field=field, severity="warning")
