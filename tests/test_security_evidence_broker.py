from __future__ import annotations
import pytest
from src.security_evidence_broker import SecurityEvidenceBroker, SecurityEvidenceError, build_security_evidence_envelope, is_opaque_digest_ref
from src.security_evidence_sources import SecurityEvidenceSourceError, auth_outcome_projection, crowdsec_decision_projection, reverse_proxy_projection
from src.security_incident_service import SecurityIncidentService
from src.security_incident_store import SecurityIncidentStore

def _ref(kind="principal", char="a"): return f"{kind}:sha256:{char*64}"
def _auth(**overrides):
    value={"outcome":"failed","principal_ref":_ref(),"source_familiarity":"unknown","session_created":"no","affected_session_refs":()}; value.update(overrides)
    return auth_outcome_projection(**value)

def test_auth_envelope_is_deterministic_and_strictly_opaque():
    first=build_security_evidence_envelope(_auth()); second=build_security_evidence_envelope(_auth())
    assert first==second and all(is_opaque_digest_ref(v) for v in (first.evidence_ref,first.correlation_ref,first.dedupe_ref))
    assert first.to_dict()["raw_content_visible"] is False

def test_correlation_omits_status_and_counts_but_includes_stable_refs():
    broker=SecurityEvidenceBroker()
    first=reverse_proxy_projection(surface="api",status="success",request_count=3,error_count=0)
    changed=reverse_proxy_projection(surface="api",status="warn",request_count=4,error_count=1)
    assert broker.correlation_key(first)==broker.correlation_key(changed)
    assert broker.dedupe_key(first)!=broker.dedupe_key(changed)
    assert broker.correlation_key(_auth(principal_ref=_ref("principal","b"))) != broker.correlation_key(_auth())

def test_correlation_keeps_observation_refs_out_but_keeps_scope_and_principal():
    broker=SecurityEvidenceBroker()
    first=crowdsec_decision_projection(decision_class="ban",severity="warn",window="hour",decision_count=1,evidence_ref=_ref("evidence","a"),scope_ref=_ref("scope","a"))
    changed=crowdsec_decision_projection(decision_class="ban",severity="warn",window="hour",decision_count=1,evidence_ref=_ref("evidence","b"),scope_ref=_ref("scope","a"))
    other_scope=crowdsec_decision_projection(decision_class="ban",severity="warn",window="hour",decision_count=1,evidence_ref=_ref("evidence","b"),scope_ref=_ref("scope","b"))
    assert broker.correlation_key(first)==broker.correlation_key(changed)!=broker.correlation_key(other_scope)
    assert broker.correlation_key(_auth(affected_session_refs=(_ref("session"),)))==broker.correlation_key(_auth())

def test_correlation_uses_only_the_source_stable_identity():
    broker=SecurityEvidenceBroker()
    assert broker.correlation_key(_auth(outcome="failed",session_created="no")) == broker.correlation_key(_auth(outcome="success",session_created="yes"))
    crowd_a=crowdsec_decision_projection(decision_class="ban",severity="warn",window="hour",decision_count=1,evidence_ref=_ref("evidence","a"),scope_ref=_ref("scope"))
    crowd_b=crowdsec_decision_projection(decision_class="alert",severity="info",window="day",decision_count=9,evidence_ref=_ref("evidence","b"),scope_ref=_ref("scope"))
    assert broker.correlation_key(crowd_a)==broker.correlation_key(crowd_b)
    assert broker.correlation_key(reverse_proxy_projection(surface="api",status="ok",request_count=1,error_count=0)) != broker.correlation_key(reverse_proxy_projection(surface="ingress",status="ok",request_count=1,error_count=0))

@pytest.mark.parametrize("payload",[
    {"source":"auth_outcome","event_type":"authentication","status":"failed","severity":"warn","dimensions":{"outcome":"failed","source_familiarity":"unknown","session_created":"no","token":"x"},"references":{"principal_ref":_ref()},"measurements":{"event_count":1}},
    {"source":"auth_outcome","event_type":"authentication","status":"failed","severity":"warn","dimensions":{"outcome":"failed","source_familiarity":"unknown","session_created":"no"},"references":{"principal_ref":[_ref()]},"measurements":{"event_count":1}},
    {"source":"auth_outcome","event_type":"authentication","status":"failed","severity":"warn","dimensions":{"outcome":"failed","source_familiarity":"unknown","session_created":"no"},"references":{"principal_ref":_ref()},"measurements":{"event_count":[{"private_path":"x"}]}},
])
def test_recursive_forbidden_corpus_rejects_before_persistence(payload):
    with pytest.raises(SecurityEvidenceError): build_security_evidence_envelope(payload)

@pytest.mark.parametrize("bad",["C:/private","203.0.113.7","session-not-opaque",_ref("session").replace("a","g",1)])
def test_auth_rejects_raw_or_malformed_session_refs(bad):
    with pytest.raises(SecurityEvidenceSourceError): _auth(affected_session_refs=(bad,))

def test_service_cannot_accept_a_custom_broker_and_revalidates_projection(tmp_path):
    store=SecurityIncidentStore(tmp_path/"incidents.sqlite3",clock=lambda:1.0)
    service=SecurityIncidentService(store)
    result=service.create_from_evidence(_auth())
    assert result.incident.incident_ref==result.evidence.evidence_ref
    bad=_auth(); bad["references"]["principal_ref"]="principal:sha256:"+"g"*64
    with pytest.raises(SecurityEvidenceError): service.create_from_evidence(bad)
