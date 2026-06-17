"""Static local plugin audit helpers.

The audit scans plugin folders and single-file plugins without importing them.
It is meant for release gates, plugin dashboards, and roadmap evidence where
executing third-party plugin code would be the wrong kind of confidence.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.plugin_capability_boundary import validate_plugin_capability_boundary
from src.plugin_manifest_policy import PluginPolicyReport, validate_local_manifest


@dataclass(frozen=True)
class LocalPluginAudit:
    plugin_id: str
    path: str
    entrypoint: str | None
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalPluginAuditSummary:
    ok: bool
    plugin_count: int
    loaded_count: int
    audits: tuple[LocalPluginAudit, ...]

    @property
    def failing_ids(self) -> tuple[str, ...]:
        return tuple(audit.plugin_id for audit in self.audits if not audit.ok)


def audit_plugins_directory(directory: str | os.PathLike[str]) -> LocalPluginAuditSummary:
    root = Path(directory)
    audits: list[LocalPluginAudit] = []
    if not root.exists():
        return LocalPluginAuditSummary(False, 0, 0, ())

    for entry in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if entry.name.startswith(".") or entry.name in {"__pycache__"}:
            continue
        if entry.is_dir():
            audits.append(audit_plugin_path(entry.name, entry))
        elif entry.is_file() and entry.name.endswith("_plugin.py"):
            audits.append(audit_plugin_path(entry.stem.removesuffix("_plugin"), entry))

    return LocalPluginAuditSummary(
        ok=all(audit.ok for audit in audits),
        plugin_count=len(audits),
        loaded_count=sum(1 for audit in audits if audit.entrypoint),
        audits=tuple(audits),
    )


def audit_plugin_path(plugin_id: str, path: str | os.PathLike[str]) -> LocalPluginAudit:
    plugin_path = Path(path)
    entrypoint = _entrypoint_for(plugin_path)
    if entrypoint is None:
        return LocalPluginAudit(
            plugin_id=plugin_id,
            path=str(plugin_path),
            entrypoint=None,
            ok=False,
            errors=("missing_entrypoint",),
        )

    manifest, parse_errors = _read_manifest(entrypoint)
    if parse_errors:
        return LocalPluginAudit(
            plugin_id=plugin_id,
            path=str(plugin_path),
            entrypoint=str(entrypoint),
            ok=False,
            errors=parse_errors,
        )

    report: PluginPolicyReport = validate_local_manifest(manifest)
    boundary_report = validate_plugin_capability_boundary(manifest)
    return LocalPluginAudit(
        plugin_id=plugin_id,
        path=str(plugin_path),
        entrypoint=str(entrypoint),
        ok=report.ok and boundary_report.ok,
        errors=report.error_codes + boundary_report.error_codes,
        warnings=report.warning_codes + boundary_report.warning_codes,
        manifest=report.normalized,
    )


def _entrypoint_for(path: Path) -> Path | None:
    if path.is_file():
        return path
    plugin_py = path / "plugin.py"
    if plugin_py.is_file():
        return plugin_py
    candidates = sorted(path.glob("*_plugin.py"), key=lambda candidate: candidate.name.lower())
    return candidates[0] if candidates else None


def _read_manifest(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}, ("syntax_error",)
    except OSError:
        return {}, ("entrypoint_unreadable",)

    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PLUGIN":
                try:
                    value = ast.literal_eval(node.value)
                except (SyntaxError, ValueError):
                    return {}, ("manifest_not_literal",)
                if not isinstance(value, dict):
                    return {}, ("manifest_not_object",)
                return value, ()
    return {}, ("missing_manifest",)
