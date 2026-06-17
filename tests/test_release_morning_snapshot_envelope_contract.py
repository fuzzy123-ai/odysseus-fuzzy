import json

from src.release_morning_snapshot_envelope import build_current_release_morning_snapshot_envelope
from src.release_morning_snapshot_envelope_contract import validate_release_morning_snapshot_envelope_contract


def test_current_release_morning_snapshot_envelope_satisfies_contract():
    report = validate_release_morning_snapshot_envelope_contract(
        build_current_release_morning_snapshot_envelope().to_dict()
    )

    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()


def test_snapshot_envelope_contract_blocks_invalid_digest():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    envelope["digest"] = "not-a-digest"

    report = validate_release_morning_snapshot_envelope_contract(envelope)

    assert not report.ok
    assert "digest:missing_or_invalid" in report.errors


def test_snapshot_envelope_contract_blocks_payload_json_mismatch():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    decoded = json.loads(envelope["payload_json"])
    decoded["summary"]["status"] = "changed"
    envelope["payload_json"] = json.dumps(decoded, indent=2, sort_keys=True)

    report = validate_release_morning_snapshot_envelope_contract(envelope)

    assert not report.ok
    assert "payload_json:payload_mismatch" in report.errors
    assert "digest:payload_json_mismatch" in report.errors


def test_snapshot_envelope_contract_blocks_invalid_payload_json():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    envelope["payload_json"] = "{not json"

    report = validate_release_morning_snapshot_envelope_contract(envelope)

    assert not report.ok
    assert "payload_json:invalid_json" in report.errors
    assert "digest:payload_json_mismatch" in report.errors


def test_snapshot_envelope_contract_reports_payload_contract_errors():
    envelope = build_current_release_morning_snapshot_envelope().to_dict()
    del envelope["payload"]["summary"]["status"]

    report = validate_release_morning_snapshot_envelope_contract(envelope)

    assert not report.ok
    assert "payload:summary:status:missing" in report.errors
    assert "payload_json:payload_mismatch" in report.errors


def test_snapshot_envelope_contract_to_dict_is_stable():
    payload = validate_release_morning_snapshot_envelope_contract(
        build_current_release_morning_snapshot_envelope().to_dict()
    ).to_dict()

    assert payload == {
        "ok": True,
        "errors": (),
        "warnings": (),
    }
