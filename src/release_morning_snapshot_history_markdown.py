"""Markdown rendering for release morning snapshot history."""
from __future__ import annotations

from src.release_morning_snapshot_envelope_diff_markdown import (
    render_release_morning_snapshot_envelope_diff_markdown,
)
from src.release_morning_snapshot_history import ReleaseMorningSnapshotHistory


def render_release_morning_snapshot_history_markdown(history: ReleaseMorningSnapshotHistory) -> str:
    summary = history.to_dict()
    lines = [
        "# Release Morning Snapshot History",
        "",
        f"Status: **{_status(history)}**",
        f"- Snapshot count: `{summary['count']}`",
        f"- Latest digest: `{summary['latest_digest'] or 'none'}`",
        f"- Previous digest: `{summary['previous_digest'] or 'none'}`",
    ]

    diff = history.latest_diff()
    if diff is None:
        lines.extend(["", "No comparable previous snapshot is available."])
        return "\n".join(lines)

    lines.extend(["", "## Latest Diff", "", render_release_morning_snapshot_envelope_diff_markdown(diff)])
    return "\n".join(lines)


def _status(history: ReleaseMorningSnapshotHistory) -> str:
    diff = history.latest_diff()
    if history.latest is None:
        return "EMPTY"
    if diff is None:
        return "SINGLE"
    if not diff.ok:
        return "INVALID"
    return "CHANGED" if diff.changed else "UNCHANGED"
