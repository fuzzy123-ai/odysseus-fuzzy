"""Redacted diagnostic bridge for Odysseus operational questions.

The bridge maps an operator question to a bounded diagnostic packet using
already-redacted metrics, alert routes and quick summaries. It never includes
the raw question text, private document content, chat identifiers, tokens,
provider output or host paths.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from src.observability_alert_routing import build_observability_alert_routes
from src.observability_metrics import RuntimeMetricSample, build_runtime_metric_sample


OBSERVABILITY_DIAGNOSTIC_BRIDGE_SCHEMA = "odysseus.observability_diagnostic_bridge.v1"

FORBIDDEN_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "unredacted_tool_output",
    "raw_prompt",
    "raw_output",
    "document_text",
    "email_body",
    "message_text",
)
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)


class ObservabilityDiagnosticsBridgeError(ValueError):
    """Raised when a diagnostic packet would be unsafe or unsupported."""


def build_observability_diagnostic_packet(
    *,
    question: str,
    metrics_snapshot: Mapping[str, Any] | None = None,
    quick_summary: Mapping[str, Any] | None = None,
    alert_routes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if quick_summary is not None:
        _reject_forbidden_output_values(quick_summary)
    if alert_routes is not None:
        _reject_forbidden_output_values(alert_routes)
    intent = classify_observability_question(question)
    samples = _samples_by_name(metrics_snapshot)
    routes = alert_routes if isinstance(alert_routes, Mapping) else build_observability_alert_routes(samples.values())
    findings = _intent_findings(intent, samples, routes, quick_summary or {})
    packet = {
        "schema": OBSERVABILITY_DIAGNOSTIC_BRIDGE_SCHEMA,
        "status": _status(findings, samples),
        "intent": intent,
        "findings": tuple(findings),
        "recommended_next_actions": tuple(_recommended_actions(intent, findings)),
        "source_contracts": (
            "odysseus.observability_metrics.v1",
            "odysseus.observability_alert_routing.v1",
            "odysseus.diagnostics_quick_summary.v1",
        ),
        "query_text_included": False,
        "raw_content_visible": False,
        "raw_logs_visible": False,
        "raw_prompts_visible": False,
        "raw_outputs_visible": False,
        "host_paths_visible": False,
        "chat_targets_visible": False,
        "tokens_visible": False,
        "live_queries_performed": False,
        "writes_performed": False,
    }
    _reject_forbidden_output_values(packet)
    return packet


def classify_observability_question(question: str) -> str:
    text = str(question or "").lower()
    if any(term in text for term in ("telegram", "sprachnachricht", "voice", "antwort", "todo", "to-do", "erinner")):
        return "telegram_or_reminder_delivery"
    if any(term in text for term in ("datei", "file", "inbox", "nextcloud", "import", "pdf", "dokument")):
        return "file_import_or_inbox"
    if any(term in text for term in ("raptor", "graph", "memory", "gedächtnis", "gedaechtnis")):
        return "memory_or_raptorgraph"
    if any(term in text for term in ("gemma", "local model", "lokal", "modell", "llm", "ki")):
        return "model_runtime"
    return "general_operations"


def _intent_findings(
    intent: str,
    samples: Mapping[str, RuntimeMetricSample],
    routes: Mapping[str, Any],
    quick_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if intent == "telegram_or_reminder_delivery":
        _append_metric_finding(findings, samples, "telegram_poll_failure_total", "error", "telegram_poll_failures")
        _append_metric_finding(findings, samples, "telegram_last_update_age_seconds", "warning", "telegram_poll_stale", threshold=900)
        _append_metric_finding(findings, samples, "scheduler_delivery_failures_total", "error", "scheduler_delivery_failures")
        _append_metric_finding(findings, samples, "scheduler_due_tasks", "warning", "scheduler_due_tasks")
    elif intent == "file_import_or_inbox":
        _append_metric_finding(findings, samples, "universal_inbox_blocked_total", "warning", "universal_inbox_blocked")
        _append_metric_finding(findings, samples, "memory_write_blocked_total", "warning", "memory_write_blocked")
    elif intent == "memory_or_raptorgraph":
        _append_metric_finding(findings, samples, "raptorgraph_maintenance_failures_total", "error", "raptorgraph_maintenance_failures")
        _append_metric_finding(findings, samples, "memory_write_blocked_total", "warning", "memory_write_blocked")
    elif intent == "model_runtime":
        _append_metric_finding(findings, samples, "local_model_latency_seconds", "warning", "local_model_latency_high", threshold=30)
        _append_metric_finding(findings, samples, "llm_call_failures_total", "warning", "llm_call_failures")
    else:
        for route in tuple(routes.get("routes") or ())[:8]:
            if isinstance(route, Mapping) and route.get("status") in {"notify_dry_run", "suppressed"}:
                findings.append(
                    {
                        "code": _safe_token(route.get("rule_id") or "observability_alert"),
                        "severity": _safe_severity(route.get("severity")),
                        "evidence": _safe_token(route.get("metric_name")),
                        "reason": _safe_token(route.get("reason")),
                    }
                )

    _append_summary_findings(findings, quick_summary)
    if not findings:
        findings.append(
            {
                "code": "no_matching_signal",
                "severity": "info",
                "evidence": "redacted_diagnostics",
                "reason": "no_matching_metric_or_summary_signal",
            }
        )
    return findings[:10]


def _append_metric_finding(
    findings: list[dict[str, Any]],
    samples: Mapping[str, RuntimeMetricSample],
    metric_name: str,
    severity: str,
    code: str,
    *,
    threshold: float = 0,
) -> None:
    sample = samples.get(metric_name)
    if sample is None or sample.value <= threshold:
        return
    findings.append(
        {
            "code": code,
            "severity": severity,
            "evidence": metric_name,
            "observed": _bounded_number(sample.value),
            "threshold": _bounded_number(threshold),
        }
    )


def _append_summary_findings(findings: list[dict[str, Any]], quick_summary: Mapping[str, Any]) -> None:
    if not isinstance(quick_summary, Mapping):
        return
    status = _safe_token(quick_summary.get("status"))
    if status in {"error", "warn"}:
        findings.append(
            {
                "code": "quick_summary_attention",
                "severity": "warning" if status == "warn" else "error",
                "evidence": "diagnostics_quick_summary",
                "reason": status,
            }
        )


def _recommended_actions(intent: str, findings: Iterable[Mapping[str, Any]]) -> list[str]:
    codes = {_safe_token(finding.get("code")) for finding in findings if isinstance(finding, Mapping)}
    actions: list[str] = []
    if intent == "telegram_or_reminder_delivery":
        actions.append("inspect_telegram_flow_events")
        if "scheduler_delivery_failures" in codes or "scheduler_due_tasks" in codes:
            actions.append("inspect_scheduler_queue")
    elif intent == "file_import_or_inbox":
        actions.append("inspect_universal_inbox_blockers")
        if "memory_write_blocked" in codes:
            actions.append("inspect_memory_write_policy")
    elif intent == "memory_or_raptorgraph":
        actions.extend(("inspect_memory_provenance", "inspect_raptorgraph_maintenance"))
    elif intent == "model_runtime":
        actions.extend(("inspect_ai_activity_ledger", "inspect_local_model_latency"))
    else:
        actions.append("inspect_runtime_metrics_and_alert_routes")
    if "no_matching_signal" in codes:
        actions.append("collect_more_redacted_evidence")
    return list(dict.fromkeys(actions))[:6]


def _status(findings: Iterable[Mapping[str, Any]], samples: Mapping[str, RuntimeMetricSample]) -> str:
    severities = {_safe_severity(finding.get("severity")) for finding in findings if isinstance(finding, Mapping)}
    if "error" in severities:
        return "needs_attention"
    if "warning" in severities:
        return "attention"
    if not samples:
        return "insufficient_evidence"
    return "no_problem_detected"


def _samples_by_name(metrics_snapshot: Mapping[str, Any] | None) -> dict[str, RuntimeMetricSample]:
    if not isinstance(metrics_snapshot, Mapping):
        return {}
    result: dict[str, RuntimeMetricSample] = {}
    for item in metrics_snapshot.get("samples") or ():
        if not isinstance(item, Mapping):
            continue
        sample = build_runtime_metric_sample(item.get("name") or "", item.get("value"), labels=item.get("labels") or {})
        result[sample.name] = sample
    return result


def _safe_severity(value: Any) -> str:
    severity = _safe_token(value)
    return severity if severity in {"info", "warning", "error"} else "info"


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or ch in "._:-")[:120]


def _bounded_number(value: float) -> float:
    return max(0.0, min(float(value), 1_000_000_000.0))


def _reject_forbidden_output_values(value: Any) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_forbidden_output_values(nested)
        return
    if isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_forbidden_output_values(nested)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_MARKERS):
            raise ObservabilityDiagnosticsBridgeError("diagnostic packet contains forbidden marker")
        if HOST_PATH_RE.search(value):
            raise ObservabilityDiagnosticsBridgeError("diagnostic packet contains a private host path")
