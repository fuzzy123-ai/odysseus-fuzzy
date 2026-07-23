import pytest

from src.tool_catalog import (
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventV1,
    ToolUsageSurface,
    pseudonymize_reference,
)


HMAC_KEY = b"p" * 32
EVENT_ID = "tue_" + "1" * 32
INVOCATION_ID = "tui_" + "2" * 32
OCCURRED_AT = "2026-07-17T12:00:00.000Z"


def _descriptor() -> ToolDescriptorV2:
    return ToolDescriptorV2.create(
        tool_id="send_email",
        display_name="Send Email",
        description="Send a confirmed message through the configured mail adapter.",
        family=ToolFamily.PLANNING_COMMUNICATION,
        source=ToolSource.BUILTIN,
        lifecycle=ToolLifecycle.CONTEXTUAL,
        availability=ToolAvailability.AVAILABLE,
        default_enabled=False,
        default_visibility=ToolVisibility.HIDDEN,
        risk_level=ToolRiskLevel.DANGEROUS,
        permission=ToolPermission.OWNER,
        effect_class=ToolEffectClass.EXTERNAL_WRITE,
        requires_confirmation=True,
        schema_ref="function:send_email",
        handler_ref="mcp:send_email",
        prompt_ref="index:send_email",
        introduced_in="0.24.0",
    )


def _event(**overrides):
    values = {
        "descriptor": _descriptor(),
        "event_kind": "started",
        "surface": ToolUsageSurface.AGENT,
        "agent_mode": ToolUsageAgentMode.AGENT,
        "event_id": EVENT_ID,
        "invocation_id": INVOCATION_ID,
        "occurred_at": OCCURRED_AT,
    }
    values.update(overrides)
    return ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY).build(**values)


def test_serialized_field_allowlist_has_no_generic_or_raw_content_slots():
    event = _event()
    payload = event.to_dict()

    assert set(payload) == ToolUsageEventV1.SERIALIZED_FIELDS
    assert payload["raw_content_visible"] is False
    forbidden = {
        "args",
        "arguments",
        "command",
        "content",
        "error_message",
        "exception",
        "headers",
        "metadata",
        "output",
        "payload",
        "prompt",
        "result",
        "token",
        "url",
    }
    assert not (set(payload) & forbidden)


def test_raw_identity_secret_path_and_message_values_never_serialize():
    private_values = {
        "owner_identity": "user@example.test",
        "session_identity": "private-session-id",
        "run_identity": r"C:\Users\alice\private-run",
        "correlation_identity": "api_key=do-not-store",
    }
    event = _event(**private_values)
    serialized = event.to_json()

    assert all(value not in serialized for value in private_values.values())
    assert serialized.count("h1_") == 4


def test_free_metadata_exception_messages_and_raw_results_are_not_builder_inputs():
    with pytest.raises(TypeError):
        _event(metadata={"anything": "raw"})
    with pytest.raises(TypeError):
        _event(error_message="provider leaked a private message")
    with pytest.raises(TypeError):
        _event(result={"body": "private"})
    with pytest.raises(TypeError):
        _event(command="rm private-file")


def test_mapping_rejects_raw_fields_and_true_visibility_marker():
    payload = _event().to_dict()
    with pytest.raises(ToolUsageEventError, match="non-allowlisted"):
        ToolUsageEventV1.from_mapping(dict(payload, owner_id="raw-owner"))
    with pytest.raises(ToolUsageEventError, match="must be false"):
        ToolUsageEventV1.from_mapping(dict(payload, raw_content_visible=True))


def test_hmac_key_is_not_represented_and_short_keys_fail_closed():
    builder = ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY)

    assert HMAC_KEY.decode("ascii") not in repr(builder)
    with pytest.raises(ToolUsageEventError, match="at least 32 bytes"):
        ToolUsageEventBuilder(app_version="0.25.0", hmac_key=b"short")
    with pytest.raises(ToolUsageEventError, match="at least 32 bytes"):
        pseudonymize_reference("owner", "value", key=b"short")


def test_unsafe_ids_versions_and_callable_references_fail_closed():
    with pytest.raises(ToolUsageEventError, match="opaque tue"):
        _event(event_id="user@example.test")
    with pytest.raises(ToolUsageEventError, match="machine-readable"):
        ToolUsageEventBuilder(app_version=r"C:\private\build", hmac_key=HMAC_KEY)
    with pytest.raises(ToolUsageEventError, match="callable"):
        _event(owner_identity=lambda: "raw")


def test_builder_requires_tax_descriptor_instead_of_accepting_free_tool_metadata():
    with pytest.raises(ToolUsageEventError, match="TAX ToolDescriptorV2"):
        ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY).build(
            descriptor={"tool_id": "send_email"},
            event_kind="started",
            surface="agent",
            agent_mode="agent",
            event_id=EVENT_ID,
            invocation_id=INVOCATION_ID,
            occurred_at=OCCURRED_AT,
        )
