from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_contract import validate_release_morning_payload_contract


def test_current_release_morning_payload_satisfies_contract():
    report = validate_release_morning_payload_contract(build_current_release_morning_payload().to_dict())

    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()


def test_payload_contract_blocks_missing_summary():
    report = validate_release_morning_payload_contract({"brief_markdown": "# Odysseus Release Morning Brief\n"})

    assert not report.ok
    assert report.errors == ("summary:missing_or_invalid",)


def test_payload_contract_blocks_missing_required_summary_fields():
    report = validate_release_morning_payload_contract(
        {
            "summary": {"status": "blocked"},
            "brief_markdown": "# Odysseus Release Morning Brief\n",
        }
    )

    assert not report.ok
    assert "summary:external_release_go:missing" in report.errors
    assert "summary:local_plugin_audit_ok:missing" in report.errors
    assert "summary:next_action_ids:missing" in report.errors


def test_payload_contract_blocks_invalid_summary_types():
    payload = build_current_release_morning_payload().to_dict()
    payload["summary"]["external_release_go"] = "false"
    payload["summary"]["local_plugin_failing_ids"] = "demo"
    payload["summary"]["active_owners"] = "Alice"

    report = validate_release_morning_payload_contract(payload)

    assert not report.ok
    assert "summary:external_release_go:invalid_type" in report.errors
    assert "summary:local_plugin_failing_ids:invalid_type" in report.errors
    assert "summary:active_owners:invalid_type" in report.errors


def test_payload_contract_warns_on_unexpected_heading():
    payload = build_current_release_morning_payload().to_dict()
    payload["brief_markdown"] = "# Different Heading\n"

    report = validate_release_morning_payload_contract(payload)

    assert report.ok
    assert report.warnings == ("brief_markdown:unexpected_heading",)


def test_payload_contract_to_dict_is_stable():
    payload = validate_release_morning_payload_contract(build_current_release_morning_payload().to_dict()).to_dict()

    assert payload == {
        "ok": True,
        "errors": (),
        "warnings": (),
    }
