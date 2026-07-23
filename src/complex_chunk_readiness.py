"""Frontend-safe Go/Partial/No-Go projection for complex chunk systems."""

from __future__ import annotations

from typing import Any, Mapping


COMPLEX_CHUNK_READINESS_SCHEMA = "odysseus.complex_chunk_readiness.v1"


def build_complex_chunk_readiness(
    *,
    multihop_report: Any = None,
    raptor_report: Any = None,
) -> dict[str, Any]:
    multihop = _to_mapping(multihop_report, preferred_method="to_redacted_dict")
    raptor = _to_mapping(raptor_report, preferred_method="to_dict")
    multihop_status = _multihop_status(multihop)
    raptor_status = _raptor_status(raptor)
    blockers = []
    warnings = []
    if multihop_status == "no_go":
        blockers.append("multihop_benchmark_failed")
    elif multihop_status == "partial":
        warnings.append("multihop_benchmark_partial")
    if raptor_status == "no_go":
        blockers.append("raptor_scale_failed")
    elif raptor_status == "partial":
        warnings.append("raptor_scale_partial")
    if not multihop:
        blockers.append("multihop_evidence_missing")
    if not raptor:
        blockers.append("raptor_evidence_missing")

    status = "go"
    if blockers:
        status = "no_go"
    elif warnings:
        status = "partial"

    payload = {
        "schema": COMPLEX_CHUNK_READINESS_SCHEMA,
        "status": status,
        "go": status == "go",
        "partial": status == "partial",
        "no_go": status == "no_go",
        "summary": _summary(status),
        "thresholds": {
            "min_multihop_score": 90.0,
            "min_retrieval_precision_pass_rate": 95.0,
            "min_raptor_nodes": 100_000,
            "min_raptor_edges": 250_000,
            "requires_output_budget_clipping": True,
        },
        "evidence_packets": {
            "multihop": _multihop_packet(multihop),
            "raptor": _raptor_packet(raptor),
        },
        "blockers": tuple(blockers),
        "warnings": tuple(warnings),
        "raw_content_visible": False,
        "private_content_visible": False,
        "live_model_rerun_required": False,
    }
    return payload


def _to_mapping(value: Any, *, preferred_method: str) -> dict[str, Any]:
    if value is None:
        return {}
    method = getattr(value, preferred_method, None)
    if callable(method):
        value = method()
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _multihop_status(payload: Mapping[str, Any]) -> str:
    if not payload:
        return "no_go"
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    score = _float(payload.get("score"))
    precision = _float(metrics.get("retrieval_precision_pass_rate"))
    evidence = _float(metrics.get("evidence_pass_rate"))
    policy = _float(metrics.get("policy_pass_rate"))
    if payload.get("status") == "passed" and score >= 90.0 and precision >= 95.0 and evidence >= 95.0 and policy >= 95.0:
        return "go"
    if score >= 70.0 and precision >= 80.0:
        return "partial"
    return "no_go"


def _raptor_status(payload: Mapping[str, Any]) -> str:
    if not payload:
        return "no_go"
    gates = payload.get("gates") if isinstance(payload.get("gates"), Mapping) else {}
    returned = payload.get("returned") if isinstance(payload.get("returned"), Mapping) else {}
    passed = bool(gates) and all(status == "passed" for status in gates.values())
    large_enough = _int(payload.get("node_count")) >= 100_000 and _int(payload.get("edge_count")) >= 250_000
    clipped = bool(returned.get("clipped"))
    if passed and large_enough and clipped:
        return "go"
    if large_enough and clipped:
        return "partial"
    return "no_go"


def _multihop_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    return {
        "schema": str(payload.get("schema") or ""),
        "status": str(payload.get("status") or "missing"),
        "score": _float(payload.get("score")),
        "corpus_chunk_count": _int(payload.get("corpus_chunk_count")),
        "retrieval_budget": _int(payload.get("retrieval_budget")),
        "case_count": _int(metrics.get("case_count")),
        "retrieval_precision_pass_rate": _float(metrics.get("retrieval_precision_pass_rate")),
        "avg_budget_waste_rate": _float(metrics.get("avg_budget_waste_rate")),
    }


def _raptor_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    returned = payload.get("returned") if isinstance(payload.get("returned"), Mapping) else {}
    cache = payload.get("cache") if isinstance(payload.get("cache"), Mapping) else {}
    return {
        "schema": str(payload.get("schema") or ""),
        "node_count": _int(payload.get("node_count")),
        "edge_count": _int(payload.get("edge_count")),
        "returned_nodes": _int(returned.get("nodes")),
        "returned_edges": _int(returned.get("edges")),
        "clipped": bool(returned.get("clipped")),
        "cache_hit_ratio": _float(cache.get("hit_ratio")),
        "failed_gate_count": sum(1 for status in (payload.get("gates") or {}).values() if status != "passed")
        if isinstance(payload.get("gates"), Mapping)
        else 0,
    }


def _summary(status: str) -> str:
    if status == "go":
        return "complex chunk retrieval is ready for bounded Harbor One evidence display"
    if status == "partial":
        return "complex chunk retrieval has usable evidence but one threshold needs review"
    return "complex chunk retrieval is not ready for product display"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return max(0.0, round(float(value or 0.0), 6))
    except (TypeError, ValueError):
        return 0.0
