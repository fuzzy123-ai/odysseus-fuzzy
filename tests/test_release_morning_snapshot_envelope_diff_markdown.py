import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_envelope_diff import diff_release_morning_snapshot_envelopes
from src.release_morning_snapshot_envelope_diff_markdown import (
    render_release_morning_snapshot_envelope_diff_markdown,
)


def test_envelope_diff_markdown_renders_unchanged():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    diff = diff_release_morning_snapshot_envelopes(envelope, deepcopy(envelope))

    markdown = render_release_morning_snapshot_envelope_diff_markdown(diff)

    assert markdown.startswith("# Release Morning Snapshot Envelope Diff")
    assert "Status: **UNCHANGED**" in markdown
    assert "Digest changed: `false`" in markdown
    assert "No release morning snapshot envelope changes detected." in markdown


def test_envelope_diff_markdown_renders_payload_changes():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    diff = diff_release_morning_snapshot_envelopes(before, after)

    markdown = render_release_morning_snapshot_envelope_diff_markdown(diff)

    assert "Status: **CHANGED**" in markdown
    assert "Digest changed: `true`" in markdown
    assert "## Payload Diff" in markdown
    assert "# Release Morning Payload Diff" in markdown
    assert "- `bad-plugin`" in markdown


def test_envelope_diff_markdown_renders_invalid_errors():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload_json"] = "{not json"
    diff = diff_release_morning_snapshot_envelopes(before, after)

    markdown = render_release_morning_snapshot_envelope_diff_markdown(diff)

    assert "Status: **INVALID**" in markdown
    assert "- `after:payload_json:invalid_json`" in markdown
