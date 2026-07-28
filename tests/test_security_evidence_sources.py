from __future__ import annotations
import json
import pytest
from src.observability_clients import ObservabilityClientConfig, query_loki_readonly, query_prometheus_readonly
from src.runtime_event_envelope import build_runtime_event
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_evidence_sources import *

def _ref(kind,char="a"): return f"{kind}:sha256:{char*64}"
def _obs(source):
    body={"source":source,"api_status":"success","result_type":"vector","result_count":1,"results":[{"labels":{"surface":"auth","instance":"node_1"},"sample_count":2,"last_value":1.0}]} if source=="prometheus" else {"source":"loki","api_status":"success","result_type":"streams","stream_count":1,"streams":[{"labels":{"surface":"auth","instance":"node_1"},"line_count":2,"first_ts":"1","last_ts":"2","log_lines_included":False}]}
    return {"schema":"odysseus.observability_clients.v1","tool":f"{source}_query_readonly","status":"success","reason":"query_summarized","query_ref":"sha256:"+"a"*64,"read_only":True,"redacted_output":True,"raw_content_visible":False,"writes_performed":False,"result":body}
def _probe():
    keys={"DATA_BRAVE_API_KEY","EMBEDDING_API_KEY","GH_TOKEN","GITHUB_TOKEN","GOOGLE_API_KEY","HF_TOKEN","HUGGING_FACE_HUB_TOKEN","NEXTCLOUD_WEBDAV_APP_PASSWORD","ODYSSEUS_ADMIN_PASSWORD","ODYSSEUS_INTERNAL_TOKEN","OPENAI_API_KEY","SERPER_API_KEY","TAVILY_API_KEY","TELEGRAM_BOT_TOKEN"}
    return {"schema_id":"odysseus.homeserver.redacted_runtime_probe.v1","status":"ok","container":"odysseus_odysseus_1","container_running":True,"environment_entry_count":14,"credential_presence":{k:False for k in keys},"unknown_sensitive_key_count":0,"raw_environment_visible":False,"secret_values_visible":False,"telegram_delivery_readiness":{"opaque_target_configured":False,"agent_reply_enabled":False,"send_ready":False,"raw_target_visible":False,"secret_values_visible":False}}

def test_auth_crowdsec_reverse_proxy_and_actual_debian_shapes_are_brokerable():
    projections=[auth_outcome_projection(outcome="success",principal_ref=_ref("principal"),source_familiarity="not_applicable",session_created="not_applicable"),crowdsec_decision_projection(decision_class="ban",severity="warn",window="hour",decision_count=2,evidence_ref=_ref("evidence"),scope_ref=_ref("scope")),reverse_proxy_projection(surface="api",status="warn",request_count=5,error_count=1),debian_redacted_probe_projection(_probe())]
    for value in projections: assert build_security_evidence_envelope(value).raw_content_visible is False
    assert "OPENAI_API_KEY" not in json.dumps(debian_redacted_probe_projection(_probe()))

def test_observability_adapters_consume_existing_result_shapes_without_lines_or_unrestricted_labels():
    prometheus=prometheus_projection(_obs("prometheus")); loki=loki_projection(_obs("loki"))
    assert prometheus["references"]["query_ref"]=="query:sha256:"+"a"*64
    assert build_security_evidence_envelope(prometheus).source=="prometheus"
    assert build_security_evidence_envelope(loki).source=="loki"
    bad=_obs("loki"); bad["result"]["streams"][0]["labels"]["instance"]="203.0.113.7"
    with pytest.raises(SecurityEvidenceSourceError): loki_projection(bad)

@pytest.mark.parametrize("source,mutator",[
    ("prometheus",lambda value: value["result"].__setitem__("api_status","authorization")),
    ("loki",lambda value: value["result"]["streams"][0].__setitem__("first_ts","Bearer token")),
    ("prometheus",lambda value: value["result"]["results"][0]["labels"].__setitem__("job","private")),
    ("prometheus",lambda value: value["result"]["results"][0].__setitem__("last_value",float("nan"))),
])
def test_discarded_observability_fields_are_validated(source,mutator):
    value=_obs(source); mutator(value)
    with pytest.raises(SecurityEvidenceSourceError): (prometheus_projection if source=="prometheus" else loki_projection)(value)

def test_observability_adapter_matches_real_readonly_clients_with_fake_transports():
    def prometheus_transport(*_): return {"status":"success","data":{"resultType":"vector","result":[{"metric":{"surface":"auth","instance":"node_1"},"value":["1","2"]}]}}
    def loki_transport(*_): return {"status":"success","data":{"resultType":"streams","result":[{"stream":{"surface":"auth","instance":"node_1"},"values":[["1","not forwarded"]]}]}}
    prom=query_prometheus_readonly("up",config=ObservabilityClientConfig(prometheus_url="http://127.0.0.1:9090",enabled=True,transport=prometheus_transport))
    loki=query_loki_readonly('{surface="auth"}',config=ObservabilityClientConfig(loki_url="http://127.0.0.1:3100",enabled=True,transport=loki_transport))
    assert build_security_evidence_envelope(prometheus_projection(prom)).source=="prometheus"
    assert build_security_evidence_envelope(loki_projection(loki)).source=="loki"

def test_query_result_type_is_not_a_correlation_identity():
    first=_obs("prometheus"); second=_obs("prometheus"); second["result"]["result_type"]="matrix"
    assert build_security_evidence_envelope(prometheus_projection(first)).correlation_ref==build_security_evidence_envelope(prometheus_projection(second)).correlation_ref

def test_observability_adapter_accepts_only_the_actual_safe_blocked_shape():
    blocked={"schema":"odysseus.observability_clients.v1","tool":"prometheus_query_readonly","status":"blocked","reason":"prometheus_not_configured","query_ref":"sha256:"+"a"*64,"limit":20,"records":(),"read_only":True,"redacted_output":True,"raw_content_visible":False,"writes_performed":False,"next_action":"configure_observability_endpoint_server_side"}
    assert prometheus_projection(blocked)["status"]=="blocked"
    blocked["raw_output"]="x"
    with pytest.raises(SecurityEvidenceSourceError): prometheus_projection(blocked)

def test_runtime_adapter_discards_identifiers_and_metadata():
    event=build_runtime_event(surface="auth",component="login",event_type="login_attempt",status="failed",severity="warn",owner_scope="scope",correlation_id="corr",duration_ms=4,retry_count=1,metadata={"attempt":2})
    assert build_security_evidence_envelope(runtime_event_projection(event)).source=="runtime_event"

def test_runtime_identity_stays_distinct_and_secret_indicator_maps_safely():
    first=build_runtime_event(surface="security",component="redaction",event_type="secret_leak_indicator",status="blocked",severity="error",owner_scope="scope",correlation_id="corr_one",event_id="evt-one")
    second=build_runtime_event(surface="security",component="redaction",event_type="secret_leak_indicator",status="blocked",severity="error",owner_scope="scope",correlation_id="corr_two",event_id="evt-two")
    one=build_security_evidence_envelope(runtime_event_projection(first)); two=build_security_evidence_envelope(runtime_event_projection(second))
    assert one.event_type=="redaction_indicator" and one.correlation_ref!=two.correlation_ref
    unsafe=dict(first); unsafe["correlation_id"]="203.0.113.7"
    with pytest.raises(SecurityEvidenceSourceError): runtime_event_projection(unsafe)

@pytest.mark.parametrize("probe",[{"schema_id":"odysseus.homeserver.redacted_runtime_probe.v1","status":"blocked","error_code":"podman_unavailable","raw_environment_visible":False,"secret_values_visible":False,"telegram_delivery_readiness":{"opaque_target_configured":False,"agent_reply_enabled":False,"send_ready":False,"raw_target_visible":False,"secret_values_visible":False}},{"schema_id":"odysseus.homeserver.redacted_runtime_probe.v1","status":"blocked","error_code":"podman_unavailable","raw_environment_visible":False,"secret_values_visible":False,"telegram_delivery_readiness":{"opaque_target_configured":False,"agent_reply_enabled":False,"send_ready":False,"raw_target_visible":False,"secret_values_visible":False},"raw_output":"x"}])
def test_debian_blocked_shape_is_fixed(probe):
    if "raw_output" in probe:
        with pytest.raises(SecurityEvidenceSourceError): debian_redacted_probe_projection(probe)
    else: assert debian_redacted_probe_projection(probe)["dimensions"]["probe_state"]=="blocked"

def test_debian_status_is_not_a_correlation_identity():
    blocked={"schema_id":"odysseus.homeserver.redacted_runtime_probe.v1","status":"blocked","error_code":"podman_unavailable","raw_environment_visible":False,"secret_values_visible":False,"telegram_delivery_readiness":{"opaque_target_configured":False,"agent_reply_enabled":False,"send_ready":False,"raw_target_visible":False,"secret_values_visible":False}}
    assert build_security_evidence_envelope(debian_redacted_probe_projection(_probe())).correlation_ref==build_security_evidence_envelope(debian_redacted_probe_projection(blocked)).correlation_ref


@pytest.mark.parametrize("mutator", [
    lambda value: value["telegram_delivery_readiness"].__setitem__("agent_reply_enabled", "true"),
    lambda value: value["telegram_delivery_readiness"].__setitem__("unexpected", False),
    lambda value: value["telegram_delivery_readiness"].__setitem__("send_ready", True),
])
def test_debian_adapter_rejects_invalid_nested_readiness_without_forwarding_its_booleans(mutator):
    probe = _probe(); mutator(probe)
    with pytest.raises(SecurityEvidenceSourceError): debian_redacted_probe_projection(probe)
