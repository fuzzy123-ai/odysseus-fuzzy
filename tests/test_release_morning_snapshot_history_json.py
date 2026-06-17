import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history import build_release_morning_snapshot_history
from src.release_morning_snapshot_history_json import render_release_morning_snapshot_history_json


def test_snapshot_history_json_renders_empty_history():
    history = build_release_morning_snapshot_history(())

    rendered = render_release_morning_snapshot_history_json(history)

    assert json.loads(rendered) == {
        "count": 0,
        "latest_digest": None,
        "latest_diff": None,
        "previous_digest": None,
    }


def test_snapshot_history_json_renders_latest_diff():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    history = build_release_morning_snapshot_history((before, after))

    rendered = render_release_morning_snapshot_history_json(history)
    payload = json.loads(rendered)

    assert payload["count"] == 2
    assert payload["latest_digest"] == after["digest"]
    assert payload["previous_digest"] == before["digest"]
    assert payload["latest_diff"]["changed"] is True
    assert payload["latest_diff"]["payload_diff"]["added_local_plugin_failures"] == ["bad-plugin"]


def test_snapshot_history_json_is_stable():
    history = build_release_morning_snapshot_history(())

    assert render_release_morning_snapshot_history_json(history) == (
        "{\n"
        '  "count": 0,\n'
        '  "latest_diff": null,\n'
        '  "latest_digest": null,\n'
        '  "previous_digest": null\n'
        "}"
    )
