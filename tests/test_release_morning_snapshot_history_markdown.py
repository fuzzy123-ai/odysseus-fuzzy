import hashlib
import json
from copy import deepcopy

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_history import build_release_morning_snapshot_history
from src.release_morning_snapshot_history_markdown import render_release_morning_snapshot_history_markdown


def test_snapshot_history_markdown_renders_empty_history():
    history = build_release_morning_snapshot_history(())

    markdown = render_release_morning_snapshot_history_markdown(history)

    assert markdown.startswith("# Release Morning Snapshot History")
    assert "Status: **EMPTY**" in markdown
    assert "Snapshot count: `0`" in markdown
    assert "No comparable previous snapshot is available." in markdown


def test_snapshot_history_markdown_renders_single_snapshot():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    history = build_release_morning_snapshot_history((envelope,))

    markdown = render_release_morning_snapshot_history_markdown(history)

    assert "Status: **SINGLE**" in markdown
    assert f"Latest digest: `{envelope['digest']}`" in markdown
    assert "Previous digest: `none`" in markdown


def test_snapshot_history_markdown_renders_latest_diff():
    before = build_current_release_morning_snapshot_envelope().to_dict()
    after = deepcopy(before)
    after["payload"]["summary"]["local_plugin_failing_ids"] = ["bad-plugin"]
    after["payload_json"] = json.dumps(after["payload"], indent=2, sort_keys=True)
    after["digest"] = hashlib.sha256(after["payload_json"].encode("utf-8")).hexdigest()
    history = build_release_morning_snapshot_history((before, after))

    markdown = render_release_morning_snapshot_history_markdown(history)

    assert "Status: **CHANGED**" in markdown
    assert "Snapshot count: `2`" in markdown
    assert "## Latest Diff" in markdown
    assert "# Release Morning Snapshot Envelope Diff" in markdown
    assert "- `bad-plugin`" in markdown
