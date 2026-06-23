import json
from pathlib import Path

from scripts.mvp_roadmap_runner import (
    DEFAULT_STATE_PATH,
    overall_progress,
    render_report,
    select_next_step,
    validate_state,
)


def _load_state():
    return json.loads(DEFAULT_STATE_PATH.read_text(encoding="utf-8"))


def test_runner_state_has_exactly_ten_mvp_roadmaps():
    state = _load_state()

    validate_state(state)

    assert [item["number"] for item in state["roadmaps"]] == list(range(1, 11))
    assert state["push_remote"] == "fuzzy"
    assert "origin" in state["forbidden_remotes"]


def test_runner_progress_matches_master_average():
    state = _load_state()

    assert overall_progress(state) == 100


def test_runner_returns_queue_exhausted_when_all_roadmaps_are_complete():
    state = _load_state()

    step = select_next_step(state)

    assert step["roadmap"] is None
    assert step["slice"] is None
    assert step["runnable"] is False


def test_runner_report_uses_required_product_progress_format():
    state = _load_state()

    report = render_report(state)

    assert "MVP-Gesamtfortschritt: 100%" in report
    assert "Version-1.0-Gate: UI live? nein" in report
    assert "Aktiver Runner-Schritt: none" in report
    assert "Ergebnis: queue_exhausted" in report
    assert "Recommended next human decision:" in report
    assert "| 1 | Runtime Closure Gates | 100 | - |" in report
    assert "| 3 | Private Data / Nextcloud Memory Ingestion | 100 | - |" in report
    assert "| 9 | Image Tools Worker Final Smoke | 100 | - |" in report
    assert "| 10 | GameDev Mount Write Smoke | 100 | - |" in report


def test_runner_state_file_is_json_object():
    assert isinstance(json.loads(Path(DEFAULT_STATE_PATH).read_text(encoding="utf-8")), dict)
