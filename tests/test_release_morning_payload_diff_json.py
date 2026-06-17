import json
from copy import deepcopy

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_diff import diff_release_morning_payloads
from src.release_morning_payload_diff_json import render_release_morning_payload_diff_json


def test_release_morning_payload_diff_json_renders_unchanged():
    payload = build_current_release_morning_payload().to_dict()
    diff = diff_release_morning_payloads(payload, deepcopy(payload))

    rendered = render_release_morning_payload_diff_json(diff)
    decoded = json.loads(rendered)

    assert rendered.startswith("{\n")
    assert decoded["ok"] is True
    assert decoded["changed"] is False


def test_release_morning_payload_diff_json_renders_changes():
    before = build_current_release_morning_payload().to_dict()
    after = deepcopy(before)
    after["summary"]["local_plugin_failing_ids"] = ("bad-plugin",)
    diff = diff_release_morning_payloads(before, after)

    decoded = json.loads(render_release_morning_payload_diff_json(diff))

    assert decoded["changed"] is True
    assert decoded["added_local_plugin_failures"] == ["bad-plugin"]


def test_release_morning_payload_diff_json_renders_invalid_diff():
    before = build_current_release_morning_payload().to_dict()
    diff = diff_release_morning_payloads(before, {"brief_markdown": "# Odysseus Release Morning Brief\n"})

    decoded = json.loads(render_release_morning_payload_diff_json(diff))

    assert decoded["ok"] is False
    assert decoded["errors"] == ["after:summary:missing_or_invalid"]
