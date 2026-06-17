import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history_bundle import build_release_morning_snapshot_history_bundle


def test_snapshot_history_bundle_handles_empty_history():
    bundle = build_release_morning_snapshot_history_bundle(())

    assert bundle.ok
    assert bundle.contract_report.ok
    assert bundle.history.to_dict()["count"] == 0
    assert "Status: **EMPTY**" in bundle.markdown
    assert json.loads(bundle.json_payload)["count"] == 0


def test_snapshot_history_bundle_handles_latest_diff():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()

    bundle = build_release_morning_snapshot_history_bundle((before, after))

    assert bundle.ok
    assert bundle.history.latest == after
    assert "Status: **CHANGED**" in bundle.markdown
    assert json.loads(bundle.json_payload)["latest_diff"]["changed"] is True


def test_snapshot_history_bundle_to_dict_is_stable():
    bundle = build_release_morning_snapshot_history_bundle(())

    payload = bundle.to_dict()

    assert payload["ok"] is True
    assert payload["history"]["count"] == 0
    assert payload["contract_report"] == {
        "ok": True,
        "errors": (),
        "warnings": (),
    }
    assert payload["markdown"].startswith("# Release Morning Snapshot History")
    assert json.loads(payload["json_payload"])["count"] == 0
