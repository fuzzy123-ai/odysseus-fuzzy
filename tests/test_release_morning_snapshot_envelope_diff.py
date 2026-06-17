import json
import hashlib
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_envelope_diff import diff_release_morning_snapshot_envelopes


def test_envelope_diff_reports_unchanged_when_digest_matches():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()

    diff = diff_release_morning_snapshot_envelopes(envelope, deepcopy(envelope))

    assert diff.ok
    assert not diff.changed
    assert diff.to_dict()["payload_diff"] is None


def test_envelope_diff_reports_digest_and_payload_changes():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()

    diff = diff_release_morning_snapshot_envelopes(before, after)

    assert diff.ok
    assert diff.changed
    assert diff.digest_changed
    assert diff.payload_diff is not None
    assert diff.payload_diff.added_local_plugin_failures == ("bad-plugin",)


def test_envelope_diff_blocks_invalid_envelope():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload_json"] = "{not json"

    diff = diff_release_morning_snapshot_envelopes(before, after)

    assert not diff.ok
    assert "after:payload_json:invalid_json" in diff.errors


def test_envelope_diff_to_dict_is_stable():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    payload = diff_release_morning_snapshot_envelopes(envelope, deepcopy(envelope)).to_dict()

    assert payload == {
        "ok": True,
        "changed": False,
        "digest_changed": False,
        "payload_diff": None,
        "errors": (),
    }
