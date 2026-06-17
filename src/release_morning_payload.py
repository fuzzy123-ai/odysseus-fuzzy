"""Stable payload for release morning dashboards and handoffs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.local_release_readiness_bundle import LocalReleaseReadinessBundle, build_local_release_readiness_bundle
from src.release_morning_brief import render_release_morning_brief
from src.release_morning_summary import ReleaseMorningSummary, build_release_morning_summary


@dataclass(frozen=True)
class ReleaseMorningPayload:
    summary: ReleaseMorningSummary
    brief_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "brief_markdown": self.brief_markdown,
        }


def build_release_morning_payload(bundle: LocalReleaseReadinessBundle) -> ReleaseMorningPayload:
    return ReleaseMorningPayload(
        summary=build_release_morning_summary(bundle),
        brief_markdown=render_release_morning_brief(bundle),
    )


def build_current_release_morning_payload() -> ReleaseMorningPayload:
    return build_release_morning_payload(build_local_release_readiness_bundle())
