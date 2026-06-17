from src.plugin_capability_boundary import validate_plugin_capability_boundary


def test_ui_plugin_cannot_request_host_capabilities():
    report = validate_plugin_capability_boundary(
        {
            "id": "health-ui",
            "kind": "ui",
            "capabilities": ["host_metrics"],
        }
    )

    assert not report.ok
    assert report.error_codes == ("ui_plugin_requests_host_capability",)


def test_core_plugin_cannot_request_direct_host_access():
    report = validate_plugin_capability_boundary(
        {
            "id": "bad-core",
            "kind": "core",
            "capabilities": ["docker_socket", "notes"],
        }
    )

    assert not report.ok
    assert report.error_codes == ("core_plugin_requests_forbidden_host_access",)


def test_host_agent_without_local_api_is_warning_not_blocker():
    report = validate_plugin_capability_boundary(
        {
            "id": "health-agent",
            "kind": "host-agent",
            "capabilities": ["host_metrics"],
        }
    )

    assert report.ok
    assert report.warning_codes == ("host_agent_without_local_api",)


def test_host_agent_token_storage_requires_redaction():
    report = validate_plugin_capability_boundary(
        {
            "id": "health-agent",
            "kind": "host-agent",
            "capabilities": ["local_api", "telegram_alerts", "token_storage"],
        }
    )

    assert not report.ok
    assert report.error_codes == ("token_storage_without_redaction",)


def test_unknown_kind_is_warning_only():
    report = validate_plugin_capability_boundary(
        {
            "id": "future-plugin",
            "kind": "remote-worker",
            "capabilities": ["local_api"],
        }
    )

    assert report.ok
    assert report.warning_codes == ("unknown_plugin_kind",)


def test_boundary_report_to_dict_is_stable():
    payload = validate_plugin_capability_boundary(
        {
            "id": "health-agent",
            "kind": "host-agent",
            "capabilities": ["secret_redaction", "token_storage", "local_api"],
        }
    ).to_dict()

    assert payload == {
        "ok": True,
        "plugin_id": "health-agent",
        "plugin_kind": "host-agent",
        "capabilities": ("local_api", "secret_redaction", "token_storage"),
        "errors": (),
        "warnings": (),
    }


def test_invalid_capabilities_are_blocked():
    report = validate_plugin_capability_boundary(
        {
            "id": "broken",
            "kind": "core",
            "capabilities": "host_metrics",
        }
    )

    assert not report.ok
    assert report.error_codes == ("invalid_capabilities",)
