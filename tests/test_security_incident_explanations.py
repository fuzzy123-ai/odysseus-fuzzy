from __future__ import annotations

import copy
import json

import pytest

from src.runtime_event_envelope import build_runtime_event
from src.security_anomaly_classifier import SecurityAnomalyClassifierError, classify_security_anomalies
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_evidence_sources import auth_outcome_projection, reverse_proxy_projection
from src.security_incident_explanations import (
    SECURITY_INCIDENT_EXPLANATION_SCHEMA,
    SecurityIncidentExplanationError,
    build_security_incident_explanation,
)
from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_response_policy import action_policy_disposition, decide_action


def _ref(kind: str, char: str = "a") -> str:
    return f"{kind}:sha256:{char * 64}"


def _evidence(**overrides):
    values = {
        "outcome": "failed",
        "principal_ref": _ref("principal"),
        "source_familiarity": "unknown",
        "session_created": "no",
        "affected_session_refs": (_ref("session"),),
    }
    values.update(overrides)
    return build_security_evidence_envelope(auth_outcome_projection(**values))


def _incident(*, actions=(), confidence: float = 0.9):
    return build_security_incident(
        incident_id="inc-explanation",
        level=3,
        severity="high",
        confidence=confidence,
        status="open",
        trigger="Repeated bounded authentication failures.",
        affected_surfaces=("auth",),
        correlation_ids=("corr-explanation",),
        evidence_refs=("evidence:sha256:" + "b" * 64,),
        recommended_actions=actions,
        created_at="2026-07-27T10:00:00Z",
        updated_at="2026-07-27T10:05:00Z",
    )


def _bound_incident(envelopes=(), **kwargs):
    incident = _incident(**kwargs)
    incident["evidence_refs"] = tuple(envelope.evidence_ref for envelope in envelopes)
    return incident


def _action(action_type: str, action_id: str):
    return build_recommended_action(
        action_type=action_type,
        action_id=action_id,
        summary="Prepare a bounded redacted action recommendation.",
        risk="Operator review is required before any external effect.",
    )


def test_explanation_is_deterministic_and_has_the_exact_schema():
    evidence = _evidence()
    incident = _bound_incident((evidence,), actions=(_action("read_only_diagnostics", "act-read"),))
    first = build_security_incident_explanation(incident, evidence=(evidence,))
    second = build_security_incident_explanation(incident, evidence=(evidence,))

    expected = {
        "schema", "incident_id", "incident_version", "evidence_refs", "observation_window",
        "classifier_family_and_revision", "observed_signal_summary", "affected_surfaces",
        "confidence_and_level", "policy_decision_and_reason", "recommended_action_ids",
        "why_each_action_is_allowed_gated_blocked_or_denied", "missing_or_conflicting_evidence",
        "operator_next_step", "auth_outcome", "principal_ref", "source_familiarity",
        "session_created", "affected_session_refs", "containment_state", "evidence_freshness",
        "known_unknowns", "raw_content_visible",
    }
    assert first == second and set(first) == expected
    assert first["schema"] == SECURITY_INCIDENT_EXPLANATION_SCHEMA
    assert first["raw_content_visible"] is False
    assert first["incident_version"] == "not_available"
    assert first["containment_state"]["state"] == "not_executed"
    assert "verification_not_available" in first["known_unknowns"]


def test_classifier_candidates_are_deterministic_across_input_order():
    events = tuple(
        build_runtime_event(
            surface="auth", component="login", event_type="auth_failure", status="failed", severity="warn",
            owner_scope="security_test", correlation_id=f"corr-{index}", event_id=f"evt-{index}",
            ts=f"2026-07-27T10:00:0{index}Z", metadata={"attempt": index},
        )
        for index in range(3)
    )

    assert classify_security_anomalies(events) == classify_security_anomalies(reversed(events))


def test_missing_and_conflicting_evidence_is_truthful_and_explicit():
    missing = build_security_incident_explanation(_incident())
    first, second = _evidence(outcome="failed"), _evidence(outcome="success", principal_ref=_ref("principal", "c"))
    conflict = build_security_incident_explanation(
        _bound_incident((first, second)), evidence=(first, second)
    )

    assert "brokered_evidence_not_available" in missing["missing_or_conflicting_evidence"]["missing"]
    assert missing["auth_outcome"] == "not_applicable"
    assert conflict["auth_outcome"] == "unknown"
    assert "conflicting_auth_outcome" in conflict["missing_or_conflicting_evidence"]["conflicting"]


def test_auth_and_non_auth_fields_are_bounded_and_not_applicable_when_absent():
    auth_evidence = _evidence()
    auth = build_security_incident_explanation(_bound_incident((auth_evidence,)), evidence=(auth_evidence,))
    non_auth_evidence = build_security_evidence_envelope(reverse_proxy_projection(surface="api", status="warn", request_count=2, error_count=1))
    non_auth = build_security_incident_explanation(
        _bound_incident((non_auth_evidence,)), evidence=(non_auth_evidence,)
    )

    assert auth["principal_ref"] == _ref("principal")
    assert auth["affected_session_refs"] == (_ref("session"),)
    assert non_auth["auth_outcome"] == "not_applicable"
    assert non_auth["principal_ref"] == "not_applicable"
    assert non_auth["affected_session_refs"] == ()


@pytest.mark.parametrize("mutator", [
    lambda value: value.__setitem__("token", "forbidden"),
    lambda value: value.__setitem__("raw_provider_response", "forbidden"),
    lambda value: value.__setitem__("raw_content_visible", True),
    lambda value: value["recommended_actions"][0].__setitem__("summary", "Bearer secret"),
])
def test_explanation_rejects_private_or_raw_input(mutator):
    incident = copy.deepcopy(_incident(actions=(_action("read_only_diagnostics", "act-read"),)))
    mutator(incident)
    with pytest.raises(SecurityIncidentExplanationError):
        build_security_incident_explanation(incident, evidence=(_evidence(),))


def test_action_dispositions_cover_allowed_gated_blocked_and_denied_truthfully():
    allowed = _action("read_only_diagnostics", "act-allowed")
    gated = _action("service_restart", "act-gated")
    blocked = _action("crowdsec_temp_block", "act-blocked")
    denied = dict(_action("read_only_diagnostics", "act-denied"))
    denied["type"] = "firewall_change"
    incident = _incident(actions=(allowed, gated, blocked), confidence=0.2)
    incident["recommended_actions"] = (*incident["recommended_actions"], denied)
    evidence = _evidence()
    incident["evidence_refs"] = (evidence.evidence_ref,)
    explanation = build_security_incident_explanation(incident, evidence=(evidence,))
    rows = {row["action_id"]: row for row in explanation["why_each_action_is_allowed_gated_blocked_or_denied"]}

    assert rows["act-allowed"]["disposition"] == "allowed"
    assert rows["act-gated"]["disposition"] == "blocked"  # low confidence wins over gate readiness
    assert rows["act-blocked"]["disposition"] == "blocked"
    assert rows["act-denied"]["disposition"] == "denied"
    assert action_policy_disposition("service_restart") == ("manual_handoff", "OPS-REMEDIATION-GO")
    assert action_policy_disposition("firewall_change") == ("never_allowed_in_SIRP", "none")
    assert all("executed" not in json.dumps(row) for row in rows.values())


def test_gated_action_is_not_claimed_executed_or_verified():
    evidence = _evidence()
    explanation = build_security_incident_explanation(
        _bound_incident((evidence,), actions=(_action("service_restart", "act-gated"),)), evidence=(evidence,)
    )
    row = explanation["why_each_action_is_allowed_gated_blocked_or_denied"][0]

    assert row["disposition"] == "gated"
    assert row["gate"] == "OPS-REMEDIATION-GO"
    assert explanation["containment_state"] == {"state": "not_executed", "receipt_ref": "not_available", "verification_state": "unknown"}


def test_only_exact_incident_bound_evidence_drives_explanation_facts():
    bound = _evidence()
    unrelated = build_security_evidence_envelope(reverse_proxy_projection(surface="api", status="warn", request_count=2, error_count=1))
    positive = build_security_incident_explanation(_bound_incident((bound,)), evidence=(bound,))
    negative = build_security_incident_explanation(_bound_incident((bound,)), evidence=(unrelated,))

    assert positive["evidence_refs"] == (bound.evidence_ref,)
    assert positive["auth_outcome"] == "failed"
    assert negative["evidence_refs"] == ()
    assert negative["auth_outcome"] == "not_applicable"
    assert "unbound_broker_evidence_excluded" in negative["missing_or_conflicting_evidence"]["missing"]


def test_conflicting_auth_identity_facts_and_correlation_are_not_misattributed():
    first = _evidence(principal_ref=_ref("principal", "a"), source_familiarity="familiar", session_created="yes", affected_session_refs=(_ref("session", "a"),))
    second = _evidence(principal_ref=_ref("principal", "b"), source_familiarity="unfamiliar", session_created="no", affected_session_refs=(_ref("session", "b"),))
    explanation = build_security_incident_explanation(_bound_incident((first, second)), evidence=(first, second))
    conflicts = set(explanation["missing_or_conflicting_evidence"]["conflicting"])

    assert explanation["principal_ref"] == "not_available"
    assert explanation["source_familiarity"] == "unknown"
    assert explanation["session_created"] == "unknown"
    assert explanation["affected_session_refs"] == ()
    assert {"conflicting_auth_principal_ref", "conflicting_auth_source_familiarity", "conflicting_auth_session_created", "conflicting_auth_affected_session_refs"} <= conflicts

    same_principal = _evidence(principal_ref=_ref("principal", "a"), source_familiarity="unfamiliar", session_created="no", affected_session_refs=(_ref("session", "b"),))
    same_correlation = build_security_incident_explanation(_bound_incident((first, same_principal)), evidence=(first, same_principal))
    assert {"conflicting_correlation_dedupe_truth", "conflicting_correlation_facts"} <= set(same_correlation["missing_or_conflicting_evidence"]["conflicting"])


def test_classifier_rejects_private_or_executable_direct_envelopes_and_spoofed_provenance():
    event = build_runtime_event(surface="auth", component="login", event_type="auth_failure", status="failed", severity="warn", owner_scope="security_test", correlation_id="corr", event_id="evt", ts="2026-07-27T10:00:00Z")
    unsafe = dict(event)
    unsafe["surface"] = "203.0.113.7"
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies((unsafe,))
    command = dict(event)
    command["command"] = "powershell forbidden"
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies((command,))
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies((), observability_summary={"note": "C:/private"})
    evidence = _evidence()
    with pytest.raises(SecurityIncidentExplanationError):
        build_security_incident_explanation(_bound_incident((evidence,)), evidence=(evidence,), classifier_family="llm_attribution")


def test_notification_is_delivery_gated_and_collections_time_and_evidence_are_bounded():
    evidence = _evidence()
    notification = build_security_incident_explanation(
        _bound_incident((evidence,), actions=(_action("operator_notification", "act-notify"),)), evidence=(evidence,)
    )
    row = notification["why_each_action_is_allowed_gated_blocked_or_denied"][0]
    assert row["disposition"] == "gated" and row["gate"] == "OPS-ALERT-DELIVERY-GO"
    delivery_policy = decide_action(_action("operator_notification", "act-notify"), incident_level=3, incident_confidence=0.9)
    assert delivery_policy["policy_gate"] == "OPS-ALERT-DELIVERY-GO"
    assert delivery_policy["allowed_to_execute"] is False

    too_many_surfaces = _bound_incident((evidence,))
    too_many_surfaces["affected_surfaces"] = tuple(f"surface_{index}" for index in range(9))
    duplicate_actions = _bound_incident((evidence,), actions=(_action("read_only_diagnostics", "act-duplicate"), _action("redacted_debug_bundle", "act-duplicate")))
    backwards_time = _bound_incident((evidence,))
    backwards_time["created_at"] = "2026-07-27T11:00:00Z"
    backwards_time["updated_at"] = "2026-07-27T10:00:00Z"
    for incident, values in ((too_many_surfaces, (evidence,)), (duplicate_actions, (evidence,)), (backwards_time, (evidence,)), (_bound_incident((evidence,)), (evidence, evidence))):
        with pytest.raises(SecurityIncidentExplanationError):
            build_security_incident_explanation(incident, evidence=values)


def test_declared_evidence_is_completely_accounted_without_exposing_missing_refs():
    first = _evidence(principal_ref=_ref("principal", "a"))
    second = _evidence(principal_ref=_ref("principal", "b"))
    partial = build_security_incident_explanation(_bound_incident((first, second)), evidence=(first,))
    nonopaque_incident = _bound_incident((first,))
    nonopaque_incident["evidence_refs"] = (first.evidence_ref, "legacy-evidence")
    nonopaque = build_security_incident_explanation(nonopaque_incident, evidence=(first,))

    assert partial["missing_or_conflicting_evidence"]["declared_evidence_missing_count"] == 1
    assert "declared_broker_evidence_not_supplied" in partial["missing_or_conflicting_evidence"]["missing"]
    assert nonopaque["missing_or_conflicting_evidence"]["nonopaque_declared_evidence_count"] == 1
    assert "declared_evidence_not_broker_bound" in nonopaque["missing_or_conflicting_evidence"]["missing"]
    assert "legacy-evidence" not in json.dumps(nonopaque)


def test_executed_or_contained_claims_are_unknown_without_receipt_authority():
    evidence = _evidence()
    executed = _bound_incident((evidence,), actions=(_action("service_restart", "act-executed"),))
    executed["recommended_actions"][0]["status"] = "executed"
    contained = _bound_incident((evidence,))
    contained["status"] = "contained"
    ordinary = build_security_incident_explanation(_bound_incident((evidence,)), evidence=(evidence,))
    for incident in (executed, contained):
        explanation = build_security_incident_explanation(incident, evidence=(evidence,))
        assert explanation["containment_state"] == {"state": "unknown", "receipt_ref": "receipt_not_available", "verification_state": "unknown"}
        assert "containment_claim_without_receipt_authority" in explanation["known_unknowns"]
    assert ordinary["containment_state"]["state"] == "not_executed"


def test_classifier_unavailable_time_sentinel_is_not_presented_as_observed_time():
    events = []
    for index in range(3):
        event = build_runtime_event(surface="auth", component="login", event_type="auth_failure", status="failed", severity="warn", owner_scope="security_test", correlation_id=f"corr-sentinel-{index}", event_id=f"evt-sentinel-{index}", ts="2026-07-27T10:00:00Z")
        event.pop("schema")
        event.pop("ts")
        events.append(event)
    candidate = classify_security_anomalies(events)["incidents"][0]
    explanation = build_security_incident_explanation(candidate)

    assert explanation["observation_window"]["start"] == "not_available"
    assert explanation["observation_window"]["end"] == "not_available"
    assert "observation_time_not_available" in explanation["known_unknowns"]


def test_nested_raw_flags_and_incomplete_schema_tagged_events_reject():
    evidence = _evidence()
    incident = _bound_incident((evidence,))
    incident["metadata"] = {"raw_content_visible": True}
    with pytest.raises(SecurityIncidentExplanationError):
        build_security_incident_explanation(incident, evidence=(evidence,))
    event = build_runtime_event(surface="auth", component="login", event_type="auth_failure", status="failed", severity="warn", owner_scope="security_test", correlation_id="corr-nested", event_id="evt-nested", ts="2026-07-27T10:00:00Z", metadata={"attempt": 1})
    nested = dict(event)
    nested["metadata"] = {"raw_content_visible": True}
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies((nested,))
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies(({"schema": "odysseus.runtime_event.v1", "raw_content_visible": False},))


def test_closed_debug_server_legacy_event_projection_is_compatible_but_near_misses_reject():
    legacy = {
        "schema": "odysseus.runtime_event.v1", "event_id": "evt-legacy", "surface": "ops",
        "component": "podman", "event_type": "service_down", "status": "failed",
        "severity": "error", "correlation_id": "corr-legacy", "raw_content_visible": False,
    }
    assert classify_security_anomalies((legacy,))["incident_count"] == 1
    missing = dict(legacy)
    missing.pop("severity")
    extra = dict(legacy)
    extra["unknown"] = "value"
    nested = dict(legacy)
    nested["metadata"] = {"raw_content_visible": True}
    for value in (missing, extra, nested):
        with pytest.raises(SecurityAnomalyClassifierError):
            classify_security_anomalies((value,))
