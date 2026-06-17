"""Markdown rendering for release follow-up slices."""
from __future__ import annotations

from src.release_slice_router import ReleaseFollowupSlice


def render_release_followup_markdown(slices: tuple[ReleaseFollowupSlice, ...]) -> str:
    if not slices:
        return "# Release Followups\n\nNo follow-up slices are currently required."

    lines = [
        "# Release Followups",
        "",
        "| Slice | Owner | Parallel | Scope | Exit |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in slices:
        lines.append(
            "| "
            + " | ".join(
                (
                    _code(item.slice_id),
                    item.owner,
                    "yes" if item.parallel_safe else "no",
                    _scope(item.scope),
                    item.exit_criteria,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _scope(values: tuple[str, ...]) -> str:
    if not values:
        return "`none`"
    return "<br>".join(_code(value) for value in values)


def _code(value: str) -> str:
    return f"`{value}`"
