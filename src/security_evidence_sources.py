"""Pure adapters from redacted source results to closed broker projections."""
from __future__ import annotations
import hashlib
import math
import re
from typing import Any, Mapping, Iterable
from src.runtime_event_envelope import event_for_loki

class SecurityEvidenceSourceError(ValueError): pass
_REF = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PATH = re.compile(r"[A-Za-z]:[\\/]|/(?:home|users|var|mnt|srv|opt)/|~[\\/]", re.I)
_LABELS = {"surface","component","event_type","status","severity","job","instance"}
_CREDENTIAL_KEYS = {"DATA_BRAVE_API_KEY","EMBEDDING_API_KEY","GH_TOKEN","GITHUB_TOKEN","GOOGLE_API_KEY","HF_TOKEN","HUGGING_FACE_HUB_TOKEN","NEXTCLOUD_WEBDAV_APP_PASSWORD","ODYSSEUS_ADMIN_PASSWORD","ODYSSEUS_INTERNAL_TOKEN","OPENAI_API_KEY","SERPER_API_KEY","TAVILY_API_KEY","TELEGRAM_BOT_TOKEN"}
_TELEGRAM_READINESS_KEYS = {"opaque_target_configured","agent_reply_enabled","send_ready","raw_target_visible","secret_values_visible"}

def auth_outcome_projection(*, outcome: Any, principal_ref: Any, source_familiarity: Any, session_created: Any, affected_session_refs: Iterable[Any] = ()) -> dict[str,Any]:
    outcome = _enum(outcome, {"success","failed","blocked","unknown","not_applicable"})
    familiar = _enum(source_familiarity, {"familiar","unfamiliar","unknown","not_applicable"})
    created = _enum(session_created, {"yes","no","not_applicable"})
    refs = {"principal_ref": _typed_ref(principal_ref, "principal")}
    values = tuple(affected_session_refs)
    if len(values) > 4 or (created == "not_applicable" and values): raise SecurityEvidenceSourceError("invalid auth projection")
    for index,value in enumerate(values): refs[f"affected_ref_{index}"] = _typed_ref(value, "session")
    return _projection("auth_outcome", "authentication", outcome, _severity(outcome), {"outcome":outcome,"source_familiarity":familiar,"session_created":created}, refs, {"event_count":1})

def crowdsec_decision_projection(*, decision_class: Any, severity: Any, window: Any, decision_count: Any, evidence_ref: Any, scope_ref: Any) -> dict[str,Any]:
    kind = _enum(decision_class, {"ban","captcha","alert","unknown","not_applicable"})
    severity = _enum(severity, {"info","notice","warn","error","critical"})
    return _projection("crowdsec_decision", "decision_summary", "not_applicable" if kind == "not_applicable" else "success", severity, {"decision_class":kind,"window":_enum(window,{"minute","hour","day","not_applicable"})}, {"evidence_ref":_typed_ref(evidence_ref,"evidence"),"scope_ref":_typed_ref(scope_ref,"scope")}, {"decision_count":_count(decision_count)})

def reverse_proxy_projection(*, surface: Any, status: Any, request_count: Any, error_count: Any) -> dict[str,Any]:
    return _projection("reverse_proxy", "aggregate", _status(status), "warn" if _count(error_count) else "info", {"surface":_enum(surface,{"ingress","api","webhook","admin","unknown"})}, {}, {"request_count":_count(request_count),"error_count":_count(error_count)})

def prometheus_projection(result: Mapping[str,Any]) -> dict[str,Any]: return _observability_projection(result, "prometheus")
def loki_projection(result: Mapping[str,Any]) -> dict[str,Any]: return _observability_projection(result, "loki")

def runtime_event_projection(event: Mapping[str,Any]) -> dict[str,Any]:
    p = event_for_loki(event); labels=p["labels"]; payload=p["payload"]
    event_type=_runtime_event_type(labels["event_type"]); surface=_token(labels["surface"]); component=_token(labels["component"])
    refs={"event_ref":_runtime_ref(event.get("event_id"),"runtime_event"),"correlation_ref":_runtime_ref(event.get("correlation_id"),"runtime_correlation")}
    return _projection("runtime_event", event_type, _status(labels["status"]), _enum(labels["severity"],{"info","notice","warn","error","critical"}), {"surface":surface if surface in {"auth","http","telegram","ops","scheduler","security"} else "unknown","component":component if component in {"login","router","polling","podman","redaction"} else "unknown"}, refs, {"duration_ms":_count(payload.get("duration_ms")),"retry_count":_count(payload.get("retry_count"))})

def debian_redacted_probe_projection(probe: Mapping[str,Any]) -> dict[str,Any]:
    if not isinstance(probe,Mapping) or probe.get("schema_id") != "odysseus.homeserver.redacted_runtime_probe.v1": raise SecurityEvidenceSourceError("invalid Debian probe projection")
    if probe.get("status") == "blocked":
        if set(probe) != {"schema_id","status","error_code","raw_environment_visible","secret_values_visible","telegram_delivery_readiness"} or probe["raw_environment_visible"] is not False or probe["secret_values_visible"] is not False or _token(probe["error_code"]) not in {"invalid_container_name","podman_unavailable","container_probe_timeout","container_probe_internal_error","container_probe_failed","invalid_probe_payload"}: raise SecurityEvidenceSourceError("invalid Debian probe projection")
        readiness=_telegram_delivery_readiness(probe["telegram_delivery_readiness"])
        if readiness["send_ready"]: raise SecurityEvidenceSourceError("invalid Debian probe projection")
        return _projection("debian_redacted_probe","readiness_projection","blocked","warn",{"probe_state":"blocked"},{},{"entry_count":0,"unexpected_sensitive_count":0,"configured_present_count":0,"container_running":0})
    expected={"schema_id","status","container","container_running","environment_entry_count","credential_presence","unknown_sensitive_key_count","raw_environment_visible","secret_values_visible","telegram_delivery_readiness"}
    if set(probe)!=expected or probe.get("status")!="ok" or probe["raw_environment_visible"] is not False or probe["secret_values_visible"] is not False or probe["container_running"] is not True or not isinstance(probe["credential_presence"],Mapping) or set(probe["credential_presence"])!=_CREDENTIAL_KEYS or any(type(v) is not bool for v in probe["credential_presence"].values()) or not _safe_container(probe["container"]): raise SecurityEvidenceSourceError("invalid Debian probe projection")
    readiness=_telegram_delivery_readiness(probe["telegram_delivery_readiness"])
    entries=_count(probe["environment_entry_count"],4096); unknown=_count(probe["unknown_sensitive_key_count"],4096); present=sum(probe["credential_presence"].values())
    if present > entries or unknown > entries or present + unknown > entries: raise SecurityEvidenceSourceError("invalid Debian probe projection")
    return _projection("debian_redacted_probe","readiness_projection","ok","info",{"probe_state":"ok"},{},{"entry_count":entries,"unexpected_sensitive_count":unknown,"configured_present_count":present,"container_running":1})

def _telegram_delivery_readiness(value:Any)->dict[str,bool]:
    if not isinstance(value,Mapping) or set(value)!=_TELEGRAM_READINESS_KEYS or any(type(value[key]) is not bool for key in _TELEGRAM_READINESS_KEYS) or value["raw_target_visible"] is not False or value["secret_values_visible"] is not False or value["send_ready"] != (value["opaque_target_configured"] and value["agent_reply_enabled"]): raise SecurityEvidenceSourceError("invalid Debian probe projection")
    return {key:value[key] for key in _TELEGRAM_READINESS_KEYS}

def _observability_projection(value: Mapping[str,Any], source: str) -> dict[str,Any]:
    if not isinstance(value,Mapping) or value.get("schema")!="odysseus.observability_clients.v1" or value.get("tool") != f"{source}_query_readonly" or value.get("read_only") is not True or value.get("redacted_output") is not True or value.get("raw_content_visible") is not False or value.get("writes_performed") is not False or not _SHA.fullmatch(str(value.get("query_ref") or "")): raise SecurityEvidenceSourceError("invalid observability projection")
    refs={"query_ref":"query:"+str(value["query_ref"])}
    if value.get("status")=="blocked":
        expected={"schema","tool","status","reason","query_ref","limit","records","read_only","redacted_output","raw_content_visible","writes_performed","next_action"}
        if set(value)!=expected or value.get("reason")!=f"{source}_not_configured" or _count(value.get("limit"),100) < 1 or value.get("records") != () or value.get("next_action")!="configure_observability_endpoint_server_side": raise SecurityEvidenceSourceError("invalid observability projection")
        return _projection(source, "metric_projection" if source=="prometheus" else "stream_projection", "blocked", "warn", {"result_type":"unknown"}, refs, {"series_count":0,"sample_count":0} if source=="prometheus" else {"stream_count":0,"line_count":0})
    if set(value)!={"schema","tool","status","reason","query_ref","read_only","redacted_output","raw_content_visible","writes_performed","result"} or value.get("status")!="success" or value.get("reason")!="query_summarized" or not isinstance(value["result"],Mapping): raise SecurityEvidenceSourceError("invalid observability projection")
    body=value["result"]
    if source=="prometheus":
        if set(body)!={"source","api_status","result_type","result_count","results"} or body.get("source")!="prometheus" or _enum(body.get("api_status"),{"success","error","unknown"}) is None or _enum(body.get("result_type"),{"vector","matrix","scalar","string","unknown"}) is None or not isinstance(body["results"],(list,tuple)) or len(body["results"])>100 or _count(body["result_count"])!=len(body["results"]): raise SecurityEvidenceSourceError("invalid observability projection")
        for row in body["results"]:
            if not isinstance(row,Mapping) or set(row)!={"labels","sample_count","last_value"}: raise SecurityEvidenceSourceError("invalid observability projection")
            _labels(row["labels"]); _count(row["sample_count"])
            if row["last_value"] is not None and (isinstance(row["last_value"],bool) or not isinstance(row["last_value"],(int,float)) or not math.isfinite(float(row["last_value"])) or abs(float(row["last_value"]))>1_000_000_000_000): raise SecurityEvidenceSourceError("invalid observability projection")
        samples=sum(row["sample_count"] for row in body["results"])
        if samples>1_000_000: raise SecurityEvidenceSourceError("invalid observability projection")
        return _projection(source,"metric_projection","success","info",{"result_type":body["result_type"]},refs,{"series_count":len(body["results"]),"sample_count":samples})
    if set(body)!={"source","api_status","result_type","stream_count","streams"} or body.get("source")!="loki" or _enum(body.get("api_status"),{"success","error","unknown"}) is None or body.get("result_type") not in {"streams","unknown"} or not isinstance(body["streams"],(list,tuple)) or len(body["streams"])>100 or _count(body["stream_count"])!=len(body["streams"]): raise SecurityEvidenceSourceError("invalid observability projection")
    for row in body["streams"]:
        if not isinstance(row,Mapping) or set(row)!={"labels","line_count","first_ts","last_ts","log_lines_included"} or row["log_lines_included"] is not False: raise SecurityEvidenceSourceError("invalid observability projection")
        _labels(row["labels"]); _count(row["line_count"]); _timestamp(row["first_ts"]); _timestamp(row["last_ts"])
    lines=sum(row["line_count"] for row in body["streams"])
    if lines>1_000_000: raise SecurityEvidenceSourceError("invalid observability projection")
    return _projection(source,"stream_projection","success","info",{"result_type":body["result_type"]},refs,{"stream_count":len(body["streams"]),"line_count":lines})

def _projection(source,event,status,severity,dimensions,references,measurements): return {"source":source,"event_type":event,"status":status,"severity":severity,"dimensions":dimensions,"references":references,"measurements":measurements}
def _typed_ref(value:Any,prefix:str)->str:
    if not isinstance(value,str) or not _REF.fullmatch(value) or not value.startswith(prefix+":sha256:"): raise SecurityEvidenceSourceError("invalid opaque reference")
    return value
def _token(value:Any)->str:
    if not isinstance(value,str) or not _TOKEN.fullmatch(value.strip().lower()) or any(x in value.strip().lower().split("_") for x in {"raw","token","cookie","credential","private","path","ip","content","log","provider","environment","secret","password","authorization"}): raise SecurityEvidenceSourceError("invalid source field")
    return value.strip().lower()
def _enum(value,allowed):
    token=_token(value)
    if token not in allowed: raise SecurityEvidenceSourceError("invalid source field")
    return token
def _status(value): return _enum(value,{"ok","success","failed","blocked","warn","unknown","not_applicable"})
def _severity(status): return {"success":"info","failed":"warn","blocked":"warn","unknown":"notice","not_applicable":"info"}[status]
def _count(value,maximum=1_000_000):
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=maximum: raise SecurityEvidenceSourceError("invalid bounded count")
    return value
def _labels(value):
    if not isinstance(value,Mapping) or not set(value)<=_LABELS: raise SecurityEvidenceSourceError("invalid observability labels")
    for v in value.values():
        if not isinstance(v,str) or _IP.search(v) or _PATH.search(v): raise SecurityEvidenceSourceError("invalid observability labels")
        _token(v)
def _safe_container(value): return isinstance(value,str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",value)) and not _IP.search(value) and not _PATH.search(value)
def _timestamp(value):
    if not isinstance(value,str) or len(value)>32 or (value and not value.isdigit()): raise SecurityEvidenceSourceError("invalid observability timestamp")
def _runtime_ref(value,kind):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,180}",value) or _IP.search(value) or _PATH.search(value) or any(x in value.lower() for x in ("authorization","bearer","token","cookie","password","private","secret")): raise SecurityEvidenceSourceError("invalid runtime identity")
    return f"{kind}:sha256:{hashlib.sha256(value.encode()).hexdigest()}"
def _runtime_event_type(value):
    if value == "secret_leak_indicator": return "redaction_indicator"
    token=_token(value)
    return token if token in {"auth_failure","login_attempt","endpoint_probe","service_down","telegram_rate_limit"} else "runtime_event"
__all__=["SecurityEvidenceSourceError","auth_outcome_projection","crowdsec_decision_projection","reverse_proxy_projection","prometheus_projection","loki_projection","runtime_event_projection","debian_redacted_probe_projection"]
