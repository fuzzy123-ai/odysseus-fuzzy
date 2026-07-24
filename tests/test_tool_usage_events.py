import json

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
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventKind,
    ToolUsageEventV1,
    ToolUsageModelScope,
    ToolUsagePersistenceReason,
    ToolUsageReferenceState,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    pseudonymize_reference,
    size_bucket,
)


HMAC_KEY = b"k" * 32
EVENT_ID = "tue_" + "a" * 32
TERMINAL_EVENT_ID = "tue_" + "b" * 32
INVOCATION_ID = "tui_" + "c" * 32
OCCURRED_AT = "2026-07-17T10:00:00.000Z"


def _descriptor() -> ToolDescriptorV2:
    return ToolDescriptorV2.create(
        tool_id="read_file",
        analytics_id="read_file",
        display_name="Read File",
        description="Read a workspace file through the bounded adapter.",
        family=ToolFamily.CODE_FILESYSTEM,
        source=ToolSource.BUILTIN,
        lifecycle=ToolLifecycle.ACTIVE,
        availability=ToolAvailability.AVAILABLE,
        default_enabled=True,
        default_visibility=ToolVisibility.VISIBLE,
        risk_level=ToolRiskLevel.SAFE,
        permission=ToolPermission.USER,
        effect_class=ToolEffectClass.READ,
        requires_confirmation=False,
        schema_ref="function:read_file",
        handler_ref="builtin:read_file",
        prompt_ref="index:read_file",
        introduced_in="0.24.0",
    )


def _builder(key=HMAC_KEY) -> ToolUsageEventBuilder:
    return ToolUsageEventBuilder(app_version="0.25.0", hmac_key=key)


def test_started_event_is_content_free_deterministic_and_tax_bound():
    event = _builder().build(
        descriptor=_descriptor(),
        event_kind=ToolUsageEventKind.STARTED,
        surface=ToolUsageSurface.AGENT,
        agent_mode=ToolUsageAgentMode.AGENT,
        model_scope=ToolUsageModelScope.LOCAL,
        argument_size_bytes=120,
        owner_identity="owner-private-value",
        session_identity="session-private-value",
        event_id=EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )

    payload = event.to_dict()

    assert payload["schema_version"] == "odysseus.tool_usage_event.v1"
    assert payload["event_kind"] == "started"
    assert payload["tool_analytics_id"] == "read_file"
    assert payload["tool_family"] == "code_filesystem"
    assert payload["tool_source"] == "builtin"
    assert payload["status"] is None
    assert payload["duration_ms"] is None
    assert payload["argument_size_bucket"] == "xs"
    assert payload["result_size_bucket"] == "none"
    assert payload["result_shape_bucket"] == "none"
    assert payload["reference_state"] == "available"
    assert payload["owner_ref"].startswith("h1_")
    assert payload["session_ref"].startswith("h1_")
    assert payload["raw_content_visible"] is False
    assert event.to_json() == event.to_json()
    assert "owner-private-value" not in event.to_json()
    assert "session-private-value" not in event.to_json()


def test_terminal_event_uses_buckets_and_bounded_status_classes():
    event = _builder().build(
        descriptor=_descriptor(),
        event_kind="terminal",
        surface="chat",
        agent_mode="chat",
        status=ToolUsageStatus.FAILED,
        error_class=ToolUsageErrorClass.TIMEOUT,
        duration_ms=1234,
        retry_ordinal=2,
        argument_size_bytes=257,
        result_size_bytes=5000,
        result_shape=ToolUsageResultShape.MAPPING,
        event_id=TERMINAL_EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )

    assert event.status == ToolUsageStatus.FAILED
    assert event.error_class == ToolUsageErrorClass.TIMEOUT
    assert event.duration_ms == 1234
    assert event.argument_size_bucket == ToolUsageSizeBucket.S
    assert event.result_size_bucket == ToolUsageSizeBucket.L
    assert event.result_shape_bucket == ToolUsageResultShape.MAPPING


def test_status_semantics_fail_closed():
    common = dict(
        descriptor=_descriptor(),
        event_kind="terminal",
        surface="agent",
        agent_mode="agent",
        event_id=TERMINAL_EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )
    with pytest.raises(ToolUsageEventError, match="require a status"):
        _builder().build(**common)
    with pytest.raises(ToolUsageEventError, match="bounded error_class"):
        _builder().build(**common, status="failed")
    with pytest.raises(ToolUsageEventError, match="bounded reason"):
        _builder().build(**common, status="blocked")
    with pytest.raises(ToolUsageEventError, match="cannot contain error"):
        _builder().build(
            **common,
            status="succeeded",
            error_class="unknown",
        )

    blocked = _builder().build(
        **common,
        status="rejected",
        blocked_reason_code=ToolUsageBlockedReason.POLICY,
    )
    assert blocked.blocked_reason_code == ToolUsageBlockedReason.POLICY


def test_hmac_references_are_stable_domain_separated_and_have_no_raw_fallback():
    owner = pseudonymize_reference("owner", "same-value", key=HMAC_KEY)
    owner_again = pseudonymize_reference("owner", "same-value", key=HMAC_KEY)
    session = pseudonymize_reference("session", "same-value", key=HMAC_KEY)

    assert owner == owner_again
    assert owner != session
    assert pseudonymize_reference("owner", "same-value", key=None) is None
    assert len(owner) == 35

    without_key = _builder(key=None).build(
        descriptor=_descriptor(),
        event_kind="started",
        surface="chat",
        agent_mode="chat",
        owner_identity="raw-owner",
        event_id=EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )
    assert without_key.owner_ref is None
    assert without_key.reference_state == ToolUsageReferenceState.UNAVAILABLE
    assert "raw-owner" not in without_key.to_json()


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        ({"incognito": True}, ToolUsagePersistenceReason.INCOGNITO),
        ({"is_nobody": True}, ToolUsagePersistenceReason.NOBODY),
    ],
)
def test_incognito_and_nobody_are_persistence_prohibitions(flags, reason):
    event = _builder().build(
        descriptor=_descriptor(),
        event_kind="started",
        surface="chat",
        agent_mode="chat",
        event_id=EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
        **flags,
    )

    assert event.persistence_allowed is False
    assert event.persistence_reason == reason


def test_allowlisted_mapping_round_trip_is_exact_and_unknown_fields_fail():
    event = _builder().build(
        descriptor=_descriptor(),
        event_kind="terminal",
        surface="api",
        agent_mode="background_system",
        status="succeeded",
        duration_ms=4,
        result_size_bytes=1,
        result_shape="scalar",
        event_id=TERMINAL_EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )
    payload = event.to_dict()

    assert ToolUsageEventV1.from_mapping(payload) == event
    unsafe = dict(payload, metadata={"free": "form"})
    with pytest.raises(ToolUsageEventError, match="non-allowlisted"):
        ToolUsageEventV1.from_mapping(unsafe)


def test_size_buckets_expose_no_raw_sizes():
    assert size_bucket(0) == ToolUsageSizeBucket.NONE
    assert size_bucket(1) == ToolUsageSizeBucket.XS
    assert size_bucket(257) == ToolUsageSizeBucket.S
    assert size_bucket(1025) == ToolUsageSizeBucket.M
    assert size_bucket(4097) == ToolUsageSizeBucket.L
    assert size_bucket(16385) == ToolUsageSizeBucket.XL
    with pytest.raises(ToolUsageEventError):
        size_bucket(-1)


def test_serialized_json_is_canonical_json():
    event = _builder().build(
        descriptor=_descriptor(),
        event_kind="started",
        surface="system",
        agent_mode="background_system",
        event_id=EVENT_ID,
        invocation_id=INVOCATION_ID,
        occurred_at=OCCURRED_AT,
    )

    assert json.loads(event.to_json()) == event.to_dict()
    assert event.to_json() == json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
