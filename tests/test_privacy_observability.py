import json

from src.privacy_observability import privacy_runtime_health


def test_privacy_runtime_health_reports_dsgvo_policy_decisions(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")

    report = privacy_runtime_health(settings={"dsgvo_mode": True})
    meta = report["meta"]
    probes = meta["probes"]

    assert report["name"] == "privacy_runtime"
    assert report["status"] == "ok"
    assert meta["dsgvo_mode"] is True
    assert meta["effective_security_mode"] == "secure"
    assert meta["required_provider_scope"] == "local_only"
    assert meta["local_only_required"] is True

    assert probes["data"]["sensitive_source"]["allowed"] is True
    assert probes["data"]["sensitive_source"]["local_only_required"] is True
    assert probes["data"]["unknown_classification"]["decision"] == "require_review"
    assert probes["data"]["unknown_classification"]["block_reason"] == (
        "classification_unknown_requires_review"
    )

    assert probes["model"]["local_provider"]["allowed"] is True
    assert probes["model"]["external_provider"]["decision"] == "require_local_model"
    assert probes["model"]["external_provider"]["block_reason"] == (
        "external_provider_in_secure_chat"
    )

    assert probes["tool"]["safe_local_tool"]["allowed"] is True
    assert probes["tool"]["external_tool"]["decision"] == "block"
    assert probes["tool"]["external_tool"]["next_action"] == "use_safe_local_tool"

    assert probes["channel"]["local_private"]["allowed"] is True
    assert probes["channel"]["telegram_sensitive"]["decision"] == "unsupported"
    assert probes["channel"]["telegram_sensitive"]["block_reason"] == (
        "secure_telegram_flow_not_supported"
    )


def test_privacy_runtime_health_never_leaks_settings_values(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    sentinel_values = {
        "api_key": "sk-leak-sentinel",
        "search_url": "https://user:pw-leak@example.invalid?q=secret-query",
        "telegram_chat_id": "1234567890",
        "private_path": r"C:\\Users\\Example\\Private\\Invoice.pdf",
    }

    report = privacy_runtime_health(settings={"dsgvo_mode": False, **sentinel_values})
    blob = json.dumps(report, sort_keys=True)

    for bad in sentinel_values.values():
        assert bad not in blob
    assert "secret-query" not in blob
    assert "pw-leak" not in blob
    assert "Invoice.pdf" not in blob
