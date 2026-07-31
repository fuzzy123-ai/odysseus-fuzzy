from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_routes import setup_auth_routes

from src.security_incident_network_context import (
    SecurityIncidentNetworkContextError,
    build_own_public_egress_snapshot,
    canonical_ip,
    decide_self_egress_suppression,
    derive_access_source_context,
)


def _request(peer: str, **headers):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def _own(*addresses: str, observed_at: float = 100.0):
    return build_own_public_egress_snapshot(addresses, observed_at=observed_at, ttl_seconds=60)


@pytest.mark.parametrize(("value", "expected"), [
    ("8.8.8.8", "8.8.8.8"), ("::ffff:8.8.8.8", "8.8.8.8"),
    ("2606:4700:4700::1111", "2606:4700:4700::1111"),
])
def test_canonical_ip_normalizes_ipv4_mapped_and_ipv6(value, expected):
    assert canonical_ip(value) == expected


@pytest.mark.parametrize("value", ["8.8.8.8:443", "[8.8.8.8]", "host.example", "fe80::1%eth0", "8.8.8.8/32", "8.8.8.8, 1.1.1.1"])
def test_canonical_ip_rejects_non_bare_or_ambiguous_values(value):
    with pytest.raises(SecurityIncidentNetworkContextError):
        canonical_ip(value)


def test_direct_peer_is_canonical_and_untrusted_forwarding_is_never_evidence():
    context = derive_access_source_context(_request("8.8.8.8", **{"x-forwarded-for": "1.1.1.1"}), trusted_proxy_networks=("10.0.0.0/8",))
    assert context.canonical_ip == "8.8.8.8"
    assert context.provenance == "direct_peer" and context.is_public is True


def test_trusted_proxy_requires_one_non_conflicting_single_hop_candidate():
    request = _request("10.1.2.3", **{"forwarded": "for=8.8.8.8", "x-real-ip": "8.8.8.8"})
    context = derive_access_source_context(request, trusted_proxy_networks=("10.0.0.0/8",))
    assert context.canonical_ip == "8.8.8.8" and context.provenance == "trusted_proxy_forwarded"
    chain = derive_access_source_context(_request("10.1.2.3", **{"x-forwarded-for": "8.8.8.8, 1.1.1.1"}), trusted_proxy_networks=("10.0.0.0/8",))
    conflict = derive_access_source_context(_request("10.1.2.3", **{"x-real-ip": "8.8.8.8", "cf-connecting-ip": "1.1.1.1"}), trusted_proxy_networks=("10.0.0.0/8",))
    assert chain.canonical_ip is None and conflict.canonical_ip is None


@pytest.mark.parametrize("network", ["0.0.0.0/0", "8.8.8.0/24", "2000::/3", "::/0"])
def test_trusted_proxy_configuration_rejects_broad_or_public_networks(network):
    context = derive_access_source_context(_request("8.8.8.8", **{"x-real-ip": "1.1.1.1"}), trusted_proxy_networks=(network,))
    assert context.canonical_ip is None and context.reason_code == "trusted_proxy_configuration_invalid"


def test_own_egress_snapshot_is_configured_public_data_only_and_freshness_is_bounded():
    snapshot = _own("8.8.8.8", "2606:4700:4700::1111")
    assert snapshot.is_fresh(now=160.0) is True and snapshot.is_fresh(now=161.0) is False
    for values in (("10.0.0.1",), (), tuple(f"8.8.8.{value}" for value in range(1, 18))):
        with pytest.raises(SecurityIncidentNetworkContextError):
            _own(*values)
    with pytest.raises(SecurityIncidentNetworkContextError):
        build_own_public_egress_snapshot(("8.8.8.8",), observed_at=100, source="provider_response")


def test_exact_fresh_public_match_is_the_only_suppression_case_and_is_audited_without_ip():
    context = derive_access_source_context(_request("8.8.8.8"))
    decision = decide_self_egress_suppression(incident_id="inc-1", event_class="external_access_origin_only", source_context=context, own_public_egress=_own("8.8.8.8"), now=120)
    assert decision["decision"] == "suppress_notification"
    assert decision["reason_code"] == "suppressed_exact_fresh_self_egress_match"
    assert "8.8.8.8" not in str(decision) and decision["source_ref"].startswith("source_ip:sha256:")
    for own, now in ((_own("1.1.1.1"), 120), (_own("8.8.8.8"), 161), (None, 120)):
        result = decide_self_egress_suppression(incident_id="inc-1", event_class="external_access_origin_only", source_context=context, own_public_egress=own, now=now)
        assert result["decision"] == "notify"


def test_suppression_audit_projects_untrusted_event_classes_to_fixed_unknown():
    context = derive_access_source_context(_request("8.8.8.8"))
    result = decide_self_egress_suppression(
        incident_id="inc-1", event_class="caller_controlled_" + "x" * 1000,
        source_context=context, own_public_egress=_own("8.8.8.8"), now=120,
    )
    assert result["event_class"] == "unknown" and result["decision"] == "notify"
    assert "caller_controlled" not in str(result)


@pytest.mark.parametrize("event_class", ["authentication_failure", "lockout", "new_privileged_session", "role_change", "credential_change", "step_up_failure", "break_glass", "session_anomaly", "remediation"])
def test_security_critical_events_are_never_suppressed(event_class):
    result = decide_self_egress_suppression(incident_id="inc-critical", event_class=event_class, source_context=derive_access_source_context(_request("8.8.8.8")), own_public_egress=_own("8.8.8.8"), now=120)
    assert result["decision"] == "notify" and result["reason_code"] == "notification_required_security_critical"


def test_private_and_unknown_sources_fail_open_even_for_origin_only_events():
    private = derive_access_source_context(_request("10.0.0.4"))
    unknown = derive_access_source_context(_request("not-an-ip"))
    for context in (private, unknown):
        result = decide_self_egress_suppression(incident_id="inc-nat", event_class="external_access_origin_only", source_context=context, own_public_egress=_own("8.8.8.8"), now=120)
        assert result["decision"] == "notify"


def test_auth_route_binds_canonical_direct_peer_in_a_bounded_internal_record():
    class Auth:
        signup_enabled = False
        def status(self, _token): return {"authenticated": False, "username": None, "is_admin": False}

    app = FastAPI()
    app.include_router(setup_auth_routes(Auth()))
    client = TestClient(app, client=("8.8.8.8", 45678))
    for _ in range(65):
        response = client.get("/api/auth/status", headers={"X-Forwarded-For": "1.1.1.1"})
        assert response.status_code == 200
    assert len(app.state.security_auth_access_contexts) == 64
    record = app.state.security_auth_access_contexts[-1]
    assert record["accessing_ip_context"]["canonical_ip"] == "8.8.8.8"
    assert record["suppression_audit"]["decision"] == "notify"
    assert all("1.1.1.1" not in str(value) for value in app.state.security_auth_access_contexts)
