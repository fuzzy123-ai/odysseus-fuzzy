from src.local_release_readiness_bundle import build_local_release_readiness_bundle
from src.release_morning_payload import build_current_release_morning_payload, build_release_morning_payload


def test_release_morning_payload_combines_summary_and_markdown():
    payload = build_release_morning_payload(build_local_release_readiness_bundle())

    assert payload.summary.status == "blocked"
    assert payload.summary.plugin_gate_ok is True
    assert payload.brief_markdown.startswith("# Odysseus Release Morning Brief")
    assert "# Plugin Release Gate" in payload.brief_markdown
    assert "# Release Artifact Manifest" in payload.brief_markdown


def test_current_release_morning_payload_to_dict_is_stable():
    payload = build_current_release_morning_payload().to_dict()

    assert payload["summary"]["status"] == "blocked"
    assert payload["summary"]["external_release_go"] is False
    assert payload["summary"]["plugin_gate_ok"] is True
    assert payload["summary"]["artifact_manifest_ok"] is True
    assert payload["brief_markdown"].startswith("# Odysseus Release Morning Brief")
