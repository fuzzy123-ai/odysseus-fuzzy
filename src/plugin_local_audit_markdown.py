"""Markdown rendering for static local plugin audits."""
from __future__ import annotations

from src.plugin_local_audit import LocalPluginAuditSummary


def render_local_plugin_audit_markdown(summary: LocalPluginAuditSummary) -> str:
    status = "PASS" if summary.ok else "BLOCKED"
    lines = [
        "# Local Plugin Audit",
        "",
        f"Status: **{status}**",
        "",
        f"- Plugins discovered: `{summary.plugin_count}`",
        f"- Entrypoints found: `{summary.loaded_count}`",
        "",
        "| Plugin | State | Entrypoint | Errors | Warnings |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not summary.audits:
        lines.append("| `none` | blocked | `none` | `no_plugins_found` | `none` |")
        return "\n".join(lines)
    for audit in summary.audits:
        state = "pass" if audit.ok else "blocked"
        entrypoint = audit.entrypoint or "none"
        lines.append(
            f"| `{audit.plugin_id}` | {state} | `{entrypoint}` | {_fmt(audit.errors)} | {_fmt(audit.warnings)} |"
        )
    return "\n".join(lines)


def _fmt(values: tuple[str, ...]) -> str:
    if not values:
        return "`none`"
    return ", ".join(f"`{value}`" for value in values)
