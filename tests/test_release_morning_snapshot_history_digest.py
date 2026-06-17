import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history import build_release_morning_snapshot_history
from src.release_morning_snapshot_history_digest import release_morning_snapshot_history_digest
from src.release_morning_snapshot_history_json import render_release_morning_snapshot_history_json


def test_snapshot_history_digest_is_stable_for_same_history():
    history = build_release_morning_snapshot_history(())

    assert release_morning_snapshot_history_digest(history) == release_morning_snapshot_history_digest(history)
    assert len(release_morning_snapshot_history_digest(history)) == 64


def test_snapshot_history_digest_matches_rendered_json():
    history = build_release_morning_snapshot_history(())

    assert release_morning_snapshot_history_digest(history) == hashlib.sha256(
        render_release_morning_snapshot_history_json(history).encode("utf-8")
    ).hexdigest()


def test_snapshot_history_digest_changes_when_history_changes():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()

    empty_history = build_release_morning_snapshot_history(())
    changed_history = build_release_morning_snapshot_history((before, after))

    assert release_morning_snapshot_history_digest(empty_history) != release_morning_snapshot_history_digest(
        changed_history
    )
