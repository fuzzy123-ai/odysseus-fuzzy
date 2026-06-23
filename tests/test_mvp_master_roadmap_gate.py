from pathlib import Path

import pytest

from src.mvp_master_roadmap_gate import (
    MvpMasterRoadmapGateError,
    MvpRoadmapProgress,
    build_current_mvp_version_gate,
    build_mvp_version_gate,
)


def test_current_mvp_gate_blocks_version_1_until_all_roadmaps_and_ui_are_done():
    gate = build_current_mvp_version_gate("docs/plans/mvp-master-roadmap.md", ui_live=False)

    assert gate.gate_id == "mvp_version_1_gate"
    assert gate.overall_percent == 81
    assert gate.version_1_ready is False
    assert gate.ui_live is False
    assert len(gate.roadmaps) == 10
    assert "mvp_roadmap:1:82" in gate.blocking_reasons
    assert "mvp_ui:not_live" in gate.blocking_reasons
    assert "Version 1.0" in gate.next_human_decision


def test_mvp_gate_allows_version_1_only_when_everything_is_complete():
    gate = build_mvp_version_gate(
        (
            MvpRoadmapProgress(index=index, roadmap=f"Roadmap {index}", percent=100, why_not_100="-")
            for index in range(1, 11)
        ),
        ui_live=True,
    )

    assert gate.overall_percent == 100
    assert gate.version_1_ready is True
    assert gate.blocking_reasons == ()


def test_mvp_gate_rejects_missing_roadmaps():
    with pytest.raises(MvpMasterRoadmapGateError, match="exactly roadmaps 1-10"):
        build_mvp_version_gate(
            (MvpRoadmapProgress(index=1, roadmap="One", percent=100, why_not_100="-"),),
            ui_live=True,
        )


def test_mvp_gate_parses_fixture_progress_table(tmp_path: Path):
    path = tmp_path / "roadmap.md"
    rows = "\n".join(f"| {index} | Roadmap {index} | 100 | - |" for index in range(1, 11))
    path.write_text(f"## Aktueller Fortschritt\n\n{rows}\n\nGesamtfortschritt MVP-Roadmaps: 100%\n", encoding="utf-8")

    gate = build_current_mvp_version_gate(path, ui_live=False)

    assert gate.overall_percent == 100
    assert gate.version_1_ready is False
    assert gate.blocking_reasons == ("mvp_ui:not_live",)
