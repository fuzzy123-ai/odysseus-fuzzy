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

    assert overall_progress(state) == 81


def test_runner_selects_first_open_runnable_slice():
    state = _load_state()

    step = select_next_step(state)

    assert step["roadmap"]["number"] == 9
    assert step["slice"]["id"] == "r9-rembg-worker-smoke"
    assert step["runnable"] is True


def test_runner_report_uses_required_product_progress_format():
    state = _load_state()

    report = render_report(state)

    assert "MVP-Gesamtfortschritt: 81%" in report
    assert "Version-1.0-Gate: UI live? nein" in report
    assert "Aktiver Runner-Schritt: R9 r9-rembg-worker-smoke" in report
    assert "Recommended next human decision:" in report
    assert "| 10 | GameDev Mount Write Smoke | 100 | - |" in report


def test_runner_state_file_is_json_object():
    assert isinstance(json.loads(Path(DEFAULT_STATE_PATH).read_text(encoding="utf-8")), dict)
