"""Policy decisions for visual and DOM comparison evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VisualDiffDecision:
    verdict: str
    reason: str
    pixel_delta_ratio: float
    dom_changed_nodes: int
    console_error_delta: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.visual_diff_decision.v1",
            "verdict": self.verdict,
            "reason": self.reason,
            "pixel_delta_ratio": self.pixel_delta_ratio,
            "dom_changed_nodes": self.dom_changed_nodes,
            "console_error_delta": self.console_error_delta,
        }


def decide_visual_diff(
    *,
    pixel_delta_ratio: Any,
    dom_changed_nodes: Any = 0,
    console_error_delta: Any = 0,
    expected_change: bool = True,
    max_unexpected_pixel_delta: float = 0.02,
) -> VisualDiffDecision:
    pixels = max(0.0, min(1.0, float(pixel_delta_ratio or 0.0)))
    dom = max(0, int(dom_changed_nodes or 0))
    console = int(console_error_delta or 0)
    if console > 0:
        return VisualDiffDecision("failed", "new_console_errors", pixels, dom, console)
    if expected_change and pixels == 0 and dom == 0:
        return VisualDiffDecision("warning", "no_observable_change", pixels, dom, console)
    if not expected_change and pixels > max_unexpected_pixel_delta:
        return VisualDiffDecision("warning", "unexpected_visual_change", pixels, dom, console)
    return VisualDiffDecision("passed", "evidence_within_policy", pixels, dom, console)
