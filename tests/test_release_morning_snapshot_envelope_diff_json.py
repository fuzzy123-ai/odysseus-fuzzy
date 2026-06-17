import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_envelope_diff import diff_release_morning_snapshot_envelopes
from src.release_morning_snapshot_envelope_diff_json import render_release_morning_snapshot_envelope_diff_json


def test_envelope_diff_json_renders_unchanged():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    diff = diff_release_morning_snapshot_envelopes(envelope, deepcopy(envelope))

    decoded = json.loads(render_release_morning_snapshot_envelope_diff_json(diff))

    assert decoded["ok"] is True
    assert decoded["changed"] is False
    assert decoded["payload_diff"] is None


def test_envelope_diff_json_renders_payload_changes():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    diff = diff_release_morning_snapshot_envelopes(before, after)

    decoded = json.loads(render_release_morning_snapshot_envelope_diff_json(diff))

    assert decoded["changed"] is True
    assert decoded["digest_changed"] is True
    assert decoded["payload_diff"]["added_local_plugin_failures"] == ["bad-plugin"]


def test_envelope_diff_json_renders_invalid_diff():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload_json"] = "{not json"
    diff = diff_release_morning_snapshot_envelopes(before, after)

    decoded = json.loads(render_release_morning_snapshot_envelope_diff_json(diff))

    assert decoded["ok"] is False
    assert "after:payload_json:invalid_json" in decoded["errors"]
