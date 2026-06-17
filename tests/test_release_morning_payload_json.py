import json

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_contract import validate_release_morning_payload_contract
from src.release_morning_payload_json import (
    render_current_release_morning_payload_json,
    render_release_morning_payload_json,
)


def test_release_morning_payload_json_is_deterministic_and_valid():
    text = render_release_morning_payload_json(build_current_release_morning_payload())
    payload = json.loads(text)

    assert text.startswith("{\n")
    assert payload["summary"]["status"] == "blocked"
    assert payload["brief_markdown"].startswith("# Odysseus Release Morning Brief")
    assert validate_release_morning_payload_contract(payload).ok


def test_current_release_morning_payload_json_uses_current_payload():
    payload = json.loads(render_current_release_morning_payload_json())

    assert payload["summary"]["plugin_gate_ok"] is True
    assert payload["summary"]["artifact_manifest_ok"] is True
    assert "REL-provider-proof-evidence" in payload["summary"]["next_action_ids"]
