"""Markdown rendering for read-only plugin release gates."""
from __future__ import annotations

from src.plugin_release_gate import PluginReleaseGate


def render_plugin_release_gate_markdown(gate: PluginReleaseGate) -> str:
    status = "PASS" if gate.ok else "BLOCKED"
    lines = [
        "# Plugin Release Gate",
        "",
        f"Status: **{status}**",
        "",
        "| Check | State | Count |",
        "| --- | --- | ---: |",
        f"| Registry | {_state(gate.registry_ok)} | {gate.registry_plugin_count} |",
        f"| Local plugins | {_state(gate.local_plugins_ok)} | {gate.local_plugin_count} |",
    ]
    if gate.errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- `{error}`" for error in gate.errors)
    if gate.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- `{warning}`" for warning in gate.warnings)
    return "\n".join(lines)


def _state(ok: bool) -> str:
    return "pass" if ok else "blocked"
