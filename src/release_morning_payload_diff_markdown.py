"""Markdown rendering for release morning payload diffs."""
from __future__ import annotations

from src.release_morning_payload_diff import ReleaseMorningPayloadDiff


def render_release_morning_payload_diff_markdown(diff: ReleaseMorningPayloadDiff) -> str:
    lines = [
        "# Release Morning Payload Diff",
        "",
        f"Status: **{_status(diff)}**",
    ]
    if not diff.ok:
        lines.extend(["", "Errors:"])
        lines.extend(f"- `{error}`" for error in diff.errors)
        return "\n".join(lines)

    if not diff.changed:
        lines.extend(["", "No release morning payload changes detected."])
        return "\n".join(lines)

    _append_section(lines, "Changed summary fields", diff.changed_summary_fields)
    _append_section(lines, "Added next actions", diff.added_next_actions)
    _append_section(lines, "Removed next actions", diff.removed_next_actions)
    _append_section(lines, "Added local plugin failures", diff.added_local_plugin_failures)
    _append_section(lines, "Resolved local plugin failures", diff.resolved_local_plugin_failures)
    _append_section(lines, "Added missing artifacts", diff.added_missing_artifacts)
    _append_section(lines, "Resolved missing artifacts", diff.resolved_missing_artifacts)
    return "\n".join(lines)


def _status(diff: ReleaseMorningPayloadDiff) -> str:
    if not diff.ok:
        return "INVALID"
    return "CHANGED" if diff.changed else "UNCHANGED"


def _append_section(lines: list[str], title: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    lines.extend(["", f"{title}:"])
    lines.extend(f"- `{value}`" for value in values)
