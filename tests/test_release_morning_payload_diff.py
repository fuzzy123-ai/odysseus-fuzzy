from copy import deepcopy

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_diff import diff_release_morning_payloads


def test_payload_diff_reports_no_changes_for_same_payload():
    payload = build_current_release_morning_payload().to_dict()

    diff = diff_release_morning_payloads(payload, deepcopy(payload))

    assert diff.ok
    assert not diff.changed
    assert diff.to_dict()["changed"] is False


def test_payload_diff_reports_summary_and_action_changes():
    before = build_current_release_morning_payload().to_dict()
    after = deepcopy(before)
    after["summary"]["plugin_gate_ok"] = False
    after["summary"]["next_action_ids"] = tuple(before["summary"]["next_action_ids"]) + ("REL-plugin-release-gate-fix",)

    diff = diff_release_morning_payloads(before, after)

    assert diff.ok
    assert diff.changed
    assert diff.changed_summary_fields == ("plugin_gate_ok",)
    assert diff.added_next_actions == ("REL-plugin-release-gate-fix",)


def test_payload_diff_reports_removed_actions_and_resolved_artifacts():
    before = build_current_release_morning_payload().to_dict()
    after = deepcopy(before)
    before["summary"]["next_action_ids"] = ("REL-provider-proof-evidence", "REL-old")
    after["summary"]["next_action_ids"] = ("REL-provider-proof-evidence",)
    before["summary"]["missing_required_artifacts"] = ("missing-a.md", "missing-b.md")
    after["summary"]["missing_required_artifacts"] = ("missing-b.md",)

    diff = diff_release_morning_payloads(before, after)

    assert diff.ok
    assert diff.removed_next_actions == ("REL-old",)
    assert diff.resolved_missing_artifacts == ("missing-a.md",)


def test_payload_diff_reports_added_missing_artifacts():
    before = build_current_release_morning_payload().to_dict()
    after = deepcopy(before)
    after["summary"]["missing_required_artifacts"] = ("missing-a.md",)

    diff = diff_release_morning_payloads(before, after)

    assert diff.ok
    assert diff.added_missing_artifacts == ("missing-a.md",)


def test_payload_diff_blocks_invalid_payloads():
    before = build_current_release_morning_payload().to_dict()
    after = {"brief_markdown": "# Odysseus Release Morning Brief\n"}

    diff = diff_release_morning_payloads(before, after)

    assert not diff.ok
    assert diff.errors == ("after:summary:missing_or_invalid",)
