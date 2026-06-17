import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history import build_release_morning_snapshot_history
from src.release_morning_snapshot_history_contract import validate_release_morning_snapshot_history_contract


def test_snapshot_history_contract_accepts_empty_history():
    history = build_release_morning_snapshot_history(()).to_dict()

    report = validate_release_morning_snapshot_history_contract(history)

    assert report.ok
    assert report.errors == ()


def test_snapshot_history_contract_accepts_single_history():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    history = build_release_morning_snapshot_history((envelope,)).to_dict()

    report = validate_release_morning_snapshot_history_contract(history)

    assert report.ok
    assert report.errors == ()


def test_snapshot_history_contract_accepts_two_snapshot_history():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    history = build_release_morning_snapshot_history((before, after)).to_dict()

    report = validate_release_morning_snapshot_history_contract(history)

    assert report.ok
    assert report.errors == ()


def test_snapshot_history_contract_blocks_invalid_digest():
    history = build_release_morning_snapshot_history(()).to_dict()
    history["latest_digest"] = "bad"

    report = validate_release_morning_snapshot_history_contract(history)

    assert not report.ok
    assert "latest_digest:invalid" in report.errors
    assert "empty_history:unexpected_fields" in report.errors


def test_snapshot_history_contract_requires_diff_for_comparable_history():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    history = build_release_morning_snapshot_history((before, after)).to_dict()
    history["latest_diff"] = None

    report = validate_release_morning_snapshot_history_contract(history)

    assert not report.ok
    assert "history:latest_diff_missing" in report.errors


def test_snapshot_history_contract_to_dict_is_stable():
    report = validate_release_morning_snapshot_history_contract(
        build_release_morning_snapshot_history(()).to_dict()
    )

    assert report.to_dict() == {
        "ok": True,
        "errors": (),
        "warnings": (),
    }
