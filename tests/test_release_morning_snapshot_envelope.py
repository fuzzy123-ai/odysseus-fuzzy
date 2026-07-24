import json

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_contract import validate_release_morning_payload_contract
from src.release_morning_payload_digest import release_morning_payload_digest
from src.release_morning_snapshot_envelope import (
    build_current_release_morning_snapshot_envelope,
    build_release_morning_snapshot_envelope,
)


def test_release_morning_snapshot_envelope_contains_digest_payload_and_json():
    payload = build_current_release_morning_payload()
    envelope = build_release_morning_snapshot_envelope(payload)

    assert envelope.digest == release_morning_payload_digest(payload)
    assert envelope.payload == payload
    assert json.loads(envelope.payload_json) == envelope.to_dict()["payload"]


def test_current_release_morning_snapshot_envelope_to_dict_is_contract_valid():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()

    assert len(envelope["digest"]) == 64
    assert validate_release_morning_payload_contract(envelope["payload"]).ok
    assert json.loads(envelope["payload_json"]) == envelope["payload"]


def test_release_morning_snapshot_envelope_digest_matches_json_payload():
    envelope = build_current_release_morning_snapshot_envelope()

    assert release_morning_payload_digest(envelope.payload) == envelope.digest
    assert json.loads(envelope.payload_json)["summary"]["status"] == "blocked"
