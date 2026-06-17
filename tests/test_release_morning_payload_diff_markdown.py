from copy import deepcopy

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_diff import diff_release_morning_payloads
from src.release_morning_payload_diff_markdown import render_release_morning_payload_diff_markdown


def test_payload_diff_markdown_renders_unchanged():
    payload = build_current_release_morning_payload().to_dict()
    diff = diff_release_morning_payloads(payload, deepcopy(payload))

    markdown = render_release_morning_payload_diff_markdown(diff)

    assert markdown.startswith("# Release Morning Payload Diff")
    assert "Status: **UNCHANGED**" in markdown
    assert "No release morning payload changes detected." in markdown


def test_payload_diff_markdown_renders_changes():
    before = build_current_release_morning_payload().to_dict()
    after = deepcopy(before)
    after["summary"]["plugin_gate_ok"] = False
    after["summary"]["next_action_ids"] = tuple(before["summary"]["next_action_ids"]) + ("REL-plugin-release-gate-fix",)
    diff = diff_release_morning_payloads(before, after)

    markdown = render_release_morning_payload_diff_markdown(diff)

    assert "Status: **CHANGED**" in markdown
    assert "Changed summary fields:" in markdown
    assert "- `plugin_gate_ok`" in markdown
    assert "Added next actions:" in markdown
    assert "- `REL-plugin-release-gate-fix`" in markdown


def test_payload_diff_markdown_renders_invalid_payload_errors():
    before = build_current_release_morning_payload().to_dict()
    diff = diff_release_morning_payloads(before, {"brief_markdown": "# Odysseus Release Morning Brief\n"})

    markdown = render_release_morning_payload_diff_markdown(diff)

    assert "Status: **INVALID**" in markdown
    assert "- `after:summary:missing_or_invalid`" in markdown
