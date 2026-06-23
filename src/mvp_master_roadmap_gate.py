"""Read-only Version 1.0 gate derived from the MVP MasterRoadmap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable


_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([^|]*)\|")
_DEFAULT_PATH = Path("docs/plans/mvp-master-roadmap.md")


class MvpMasterRoadmapGateError(ValueError):
    """Raised when the MVP MasterRoadmap progress table is missing or invalid."""


@dataclass(frozen=True, slots=True)
class MvpRoadmapProgress:
    index: int
    roadmap: str
    percent: int
    why_not_100: str

    @property
    def complete(self) -> bool:
        return self.percent == 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "roadmap": self.roadmap,
            "percent": self.percent,
            "why_not_100": self.why_not_100,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class MvpVersionGate:
    gate_id: str
    overall_percent: int
    ui_live: bool
    version_1_ready: bool
    roadmaps: tuple[MvpRoadmapProgress, ...]
    blocking_reasons: tuple[str, ...]
    next_human_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "overall_percent": self.overall_percent,
            "ui_live": self.ui_live,
            "version_1_ready": self.version_1_ready,
            "roadmaps": tuple(item.to_dict() for item in self.roadmaps),
            "blocking_reasons": self.blocking_reasons,
            "next_human_decision": self.next_human_decision,
        }


def build_mvp_version_gate(
    roadmaps: Iterable[MvpRoadmapProgress],
    *,
    ui_live: bool,
) -> MvpVersionGate:
    items = tuple(sorted(roadmaps, key=lambda item: item.index))
    _validate_roadmaps(items)
    overall = round(sum(item.percent for item in items) / len(items))
    blockers = tuple(
        f"mvp_roadmap:{item.index}:{item.percent}"
        for item in items
        if not item.complete
    )
    if not ui_live:
        blockers += ("mvp_ui:not_live",)
    ready = not blockers
    return MvpVersionGate(
        gate_id="mvp_version_1_gate",
        overall_percent=overall,
        ui_live=bool(ui_live),
        version_1_ready=ready,
        roadmaps=items,
        blocking_reasons=blockers,
        next_human_decision=(
            "Version 1.0 is ready to claim."
            if ready
            else "Finish all ten MVP roadmaps to 100% and ship the new UI before claiming Version 1.0."
        ),
    )


def build_current_mvp_version_gate(
    path: str | Path = _DEFAULT_PATH,
    *,
    ui_live: bool = False,
) -> MvpVersionGate:
    target = Path(path)
    return build_mvp_version_gate(_parse_progress_table(target.read_text(encoding="utf-8")), ui_live=ui_live)


def _parse_progress_table(markdown: str) -> tuple[MvpRoadmapProgress, ...]:
    in_progress_section = False
    rows: list[MvpRoadmapProgress] = []
    for line in markdown.splitlines():
        if line.strip() == "## Aktueller Fortschritt":
            in_progress_section = True
            continue
        if in_progress_section and line.startswith("## "):
            break
        if not in_progress_section:
            continue
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        index = int(match.group(1))
        if not 1 <= index <= 10:
            continue
        percent = int(match.group(3))
        if not 0 <= percent <= 100:
            raise MvpMasterRoadmapGateError("roadmap percent must be between 0 and 100")
        rows.append(
            MvpRoadmapProgress(
                index=index,
                roadmap=" ".join(match.group(2).split()),
                percent=percent,
                why_not_100=" ".join(match.group(4).strip().split()) or "-",
            )
        )
    return tuple(rows)


def _validate_roadmaps(items: tuple[MvpRoadmapProgress, ...]) -> None:
    indexes = tuple(item.index for item in items)
    if indexes != tuple(range(1, 11)):
        raise MvpMasterRoadmapGateError("MVP Version 1.0 gate requires exactly roadmaps 1-10")
    for item in items:
        if not isinstance(item.percent, int) or not 0 <= item.percent <= 100:
            raise MvpMasterRoadmapGateError("roadmap percent must be between 0 and 100")
