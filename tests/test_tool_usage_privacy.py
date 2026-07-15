from datetime import datetime, timezone

import pytest

from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBuildResult,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageReferenceKind,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageSuppressionReason,
    ToolUsageSurface,
    pseudonymize_reference,
)


def _event_values(**overrides):
    values = {
        "event_kind": ToolUsageEventKind.STARTED,
        "event_id": "evt_000000000101",
        "invocation_id": "inv_000000000101",
        "occurred_at": datetime(2026, 2, 3, tzinfo=timezone.utc),
        "tool_analytics_id": "safe-tool",
        "tool_family": ToolFamily.EXPERIMENTAL,
        "tool_source": ToolSource.LEGACY,
        "surface": ToolUsageSurface.SYSTEM,
        "argument_size_bucket": ToolUsageSizeBucket.NONE,
        "result_size_bucket": ToolUsageSizeBucket.NONE,
        "result_shape_bucket": ToolUsageResultShape.NONE,
        "model_scope": ToolUsageModelScope.UNKNOWN,
        "agent_mode": ToolUsageAgentMode.SYSTEM,
        "app_version": "0.25.0-test",
    }
    values.update(overrides)
    return values


def test_hmac_references_are_deterministic_namespaced_and_nonreversible():
    key = b"k" * 32
    owner = pseudonymize_reference(
        "synthetic-owner-a",
        hmac_key=key,
        kind=ToolUsageReferenceKind.OWNER,
    )
    owner_again = pseudonymize_reference(
        "synthetic-owner-a",
        hmac_key=key,
        kind=ToolUsageReferenceKind.OWNER,
    )
    session = pseudonymize_reference(
        "synthetic-owner-a",
        hmac_key=key,
        kind=ToolUsageReferenceKind.SESSION,
    )

    assert owner == owner_again
    assert owner.startswith("h1_owner_")
    assert session.startswith("h1_session_")
    assert owner != session
    assert "synthetic-owner-a" not in owner


@pytest.mark.parametrize("value", [None, "synthetic-reference"])
def test_missing_hmac_key_has_no_raw_fallback(value):
    assert (
        pseudonymize_reference(
            value,
            hmac_key=None,
            kind=ToolUsageReferenceKind.CORRELATION,
        )
        is None
    )


def test_hmac_helper_rejects_short_keys_and_unknown_namespaces():
    with pytest.raises(ToolUsageEventError):
        pseudonymize_reference(
            "synthetic-reference",
            hmac_key=b"short",
            kind=ToolUsageReferenceKind.RUN,
        )
    with pytest.raises(ToolUsageEventError):
        pseudonymize_reference(
            "synthetic-reference",
            hmac_key=b"k" * 32,
            kind="user",
        )


def test_builder_accepts_only_namespaced_hmac_references():
    key = b"k" * 32
    owner_ref = pseudonymize_reference(
        "synthetic-owner",
        hmac_key=key,
        kind=ToolUsageReferenceKind.OWNER,
    )
    result = ToolUsageEventBuilder.build(**_event_values(owner_ref=owner_ref))

    assert result.event.owner_ref == owner_ref
    assert "synthetic-owner" not in repr(result.to_safe_dict())

    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**_event_values(owner_ref="synthetic-owner"))
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(
            **_event_values(owner_ref=owner_ref.replace("owner", "session", 1))
        )


def test_incognito_short_circuits_before_event_construction_and_forbids_persistence():
    result = ToolUsageEventBuilder.build(**_event_values(incognito=True))

    assert result.event is None
    assert result.persistence_allowed is False
    assert result.suppression_reason == ToolUsageSuppressionReason.INCOGNITO
    assert result.to_safe_dict() == {
        "event": None,
        "persistence_allowed": False,
        "raw_content_visible": False,
        "suppression_reason": "incognito",
    }


def test_nobody_short_circuits_before_event_construction_and_forbids_persistence():
    result = ToolUsageEventBuilder.build(**_event_values(owner_is_nobody=True))

    assert result.event is None
    assert result.persistence_allowed is False
    assert result.suppression_reason == ToolUsageSuppressionReason.NOBODY


@pytest.mark.parametrize("field", ["incognito", "owner_is_nobody"])
def test_persistence_policy_flags_must_be_real_booleans(field):
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**_event_values(**{field: "false"}))


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "metadata",
        "payload",
        "args",
        "result",
        "prompt",
        "command",
        "path",
        "url",
        "exception_message",
        "owner_id",
        "session_id",
        "chat_id",
        "credential",
    ],
)
def test_builder_fails_closed_on_fields_outside_the_allowlist(forbidden_field):
    values = _event_values()
    values[forbidden_field] = "forbidden-marker"
    with pytest.raises(TypeError):
        ToolUsageEventBuilder.build(**values)


def test_error_class_rejects_free_text_without_echoing_it():
    marker = "unbounded-error-detail"
    with pytest.raises(ToolUsageEventError) as exc_info:
        ToolUsageEventBuilder.build(**_event_values(error_class=marker))

    assert marker not in str(exc_info.value)


def test_safe_serialization_has_only_allowlisted_fields_and_explicit_redaction():
    payload = ToolUsageEventBuilder.build(**_event_values()).event.to_safe_dict()

    assert set(payload) == {
        "agent_mode",
        "app_version",
        "argument_size_bucket",
        "blocked_reason_code",
        "correlation_ref",
        "duration_ms",
        "error_class",
        "event_id",
        "event_kind",
        "invocation_id",
        "model_scope",
        "occurred_at",
        "owner_ref",
        "raw_content_visible",
        "result_shape_bucket",
        "result_size_bucket",
        "retry_ordinal",
        "run_ref",
        "schema_version",
        "session_ref",
        "status",
        "surface",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
    }
    assert payload["raw_content_visible"] is False


def test_build_result_cannot_claim_persistence_without_an_event():
    with pytest.raises(ToolUsageEventError):
        ToolUsageBuildResult(
            event=None,
            persistence_allowed=True,
            suppression_reason=None,
        )
