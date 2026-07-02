"""Compact visual diff summaries for agent observation."""

from __future__ import annotations

from typing import Any, Mapping

from src.agent_visual_diff_policy import decide_visual_diff


def build_visual_diff_summary(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    pixel_delta_ratio: Any,
    dom_changed_nodes: Any = 0,
    console_error_delta: Any = 0,
    expected_change: bool = True,
) -> dict[str, Any]:
    decision = decide_visual_diff(
        pixel_delta_ratio=pixel_delta_ratio,
        dom_changed_nodes=dom_changed_nodes,
        console_error_delta=console_error_delta,
        expected_change=expected_change,
    ).to_dict()
    return {
        "schema": "odysseus.visual_diff_summary.v1",
        "before_artifact_ref": _artifact(before),
        "after_artifact_ref": _artifact(after),
        "pixel_delta_ratio": decision["pixel_delta_ratio"],
        "dom_changed_nodes": decision["dom_changed_nodes"],
        "console_error_delta": decision["console_error_delta"],
        "verdict": decision["verdict"],
        "reason": decision["reason"],
        "raw_content_visible": False,
    }


def _artifact(payload: Mapping[str, Any]) -> str:
    value = str((payload or {}).get("artifact_ref") or "")
    if not value or value.startswith("/") or ".." in value.split("/") or "\\" in value:
        raise ValueError("artifact ref is unsafe")
    return value
