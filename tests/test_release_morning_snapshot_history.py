import hashlib
import json
from copy import deepcopy

import pytest

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history import build_release_morning_snapshot_history


def test_snapshot_history_handles_empty_history():
    history = build_release_morning_snapshot_history(())

    assert history.latest is None
    assert history.previous is None
    assert history.latest_diff() is None
    assert history.to_dict() == {
        "count": 0,
        "latest_digest": None,
        "previous_digest": None,
        "latest_diff": None,
    }


def test_snapshot_history_handles_single_envelope():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    history = build_release_morning_snapshot_history((envelope,))

    assert history.latest == envelope
    assert history.previous is None
    assert history.latest_diff() is None
    assert history.to_dict()["latest_digest"] == envelope["digest"]


def test_snapshot_history_diffs_latest_two_envelopes():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    history = build_release_morning_snapshot_history((before, after))

    diff = history.latest_diff()

    assert diff is not None
    assert diff.changed
    assert diff.payload_diff is not None
    assert diff.payload_diff.added_local_plugin_failures == ("bad-plugin",)


def test_snapshot_history_rejects_invalid_envelope():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    envelope["digest"] = "bad"

    with pytest.raises(ValueError, match="invalid envelope at index 0"):
        build_release_morning_snapshot_history((envelope,))
