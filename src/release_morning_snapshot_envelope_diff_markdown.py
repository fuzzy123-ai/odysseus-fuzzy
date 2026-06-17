"""Markdown rendering for release morning snapshot envelope diffs."""
from __future__ import annotations

from src.release_morning_payload_diff_markdown import render_release_morning_payload_diff_markdown
from src.release_morning_snapshot_envelope_diff import ReleaseMorningSnapshotEnvelopeDiff


def render_release_morning_snapshot_envelope_diff_markdown(diff: ReleaseMorningSnapshotEnvelopeDiff) -> str:
    lines = [
        "# Release Morning Snapshot Envelope Diff",
        "",
        f"Status: **{_status(diff)}**",
        f"- Digest changed: `{str(diff.digest_changed).lower()}`",
    ]
    if not diff.ok:
        lines.extend(["", "Errors:"])
        lines.extend(f"- `{error}`" for error in diff.errors)
        return "\n".join(lines)

    if not diff.changed:
        lines.extend(["", "No release morning snapshot envelope changes detected."])
        return "\n".join(lines)

    if diff.payload_diff:
        lines.extend(["", "## Payload Diff", "", render_release_morning_payload_diff_markdown(diff.payload_diff)])
    return "\n".join(lines)


def _status(diff: ReleaseMorningSnapshotEnvelopeDiff) -> str:
    if not diff.ok:
        return "INVALID"
    return "CHANGED" if diff.changed else "UNCHANGED"
