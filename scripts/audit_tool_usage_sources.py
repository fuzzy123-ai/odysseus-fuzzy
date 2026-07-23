#!/usr/bin/env python3
"""Audit tool-usage source coverage without reading messages or runtime data."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
INVENTORY_KIND = "odysseus.tool_usage_source_overlap"
BASELINE_DATE = "2026-07-13"
CLASSIFICATIONS = frozenset(
    {"primary_candidate", "coverage_only", "domain_audit", "not_usage"}
)


@dataclass(frozen=True)
class SourceEvidence:
    path: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class UsageSource:
    source_id: str
    name: str
    classification: str
    scope: str
    time_basis: str
    time_start: str | None
    time_end: str | None
    key_capabilities: tuple[str, ...]
    status_capabilities: tuple[str, ...]
    duration_capability: str
    privacy_posture: str
    historical_counts: tuple[tuple[str, int], ...]
    historically_missing: tuple[str, ...]
    evidence: tuple[SourceEvidence, ...]


@dataclass(frozen=True)
class OverlapRisk:
    left: str
    right: str
    risk: str
    shared_key_capability: str
    aggregation_rule: str


SOURCES: tuple[UsageSource, ...] = (
    UsageSource(
        "chat_metadata",
        "Persisted chat tool metadata",
        "coverage_only",
        "historical_chat_messages",
        "historical_window",
        "2026-06-06",
        "2026-06-17",
        ("legacy_session_scope", "tool_name"),
        ("partial_event_kind",),
        "historically_incomplete",
        "raw_chat_context_co_located_not_read_by_this_audit",
        (
            ("tool_event_count", 1104),
            ("distinct_tool_name_count", 46),
            ("message_count", 84),
            ("session_count", 32),
        ),
        ("canonical_invocation_key", "consistent_terminal_status", "duration_ms"),
        (SourceEvidence("core/database.py", ("ChatMessage",)),),
    ),
    UsageSource(
        "agent_run_ledger",
        "Agent run ledger",
        "coverage_only",
        "agent_run_events",
        "historical_window",
        "2026-06-13",
        "2026-07-05",
        ("legacy_run_scope", "tool_name"),
        ("run_status", "partial_tool_result"),
        "historically_incomplete",
        "legacy_preview_risk_not_read_by_this_audit",
        (
            ("run_started_event_count", 607),
            ("output_event_count", 606),
            ("distinct_tool_name_count", 43),
            ("run_count", 111),
        ),
        ("canonical_invocation_key", "content_free_error_class", "complete_duration_ms"),
        (
            SourceEvidence(
                "src/agent_run_ledger.py",
                ("append_event", "append_run_started", "summarize_sse_event"),
            ),
        ),
    ),
    UsageSource(
        "tool_execution_boundary",
        "Central execute_tool_block boundary",
        "primary_candidate",
        "builtin_plugin_and_mcp_dispatch",
        "runtime_boundary",
        None,
        None,
        ("one_wrapper_call", "tool_name"),
        ("started", "terminal_result", "exception"),
        "monotonic_elapsed_ms",
        "content_free_emission_possible",
        (),
        ("persistent_global_invocation_store", "canonical_tax_identity"),
        (
            SourceEvidence(
                "src/tool_execution.py",
                ("execute_tool_block", "_emit_ai_lens_tool_event"),
            ),
        ),
    ),
    UsageSource(
        "ai_lens",
        "AI Lens tool lifecycle events",
        "coverage_only",
        "optional_lens_enabled_calls",
        "runtime_optional",
        None,
        None,
        ("event_id", "hashed_tool_ref"),
        ("tool_call_started", "tool_call_result"),
        "latency_ms",
        "content_free_hashed_refs",
        (),
        ("global_capture_guarantee", "canonical_tax_identity"),
        (
            SourceEvidence(
                "src/ai_lens_events.py",
                ("AiLensEvent", "AiLensEventType"),
            ),
        ),
    ),
    UsageSource(
        "ai_activity_ledger",
        "AI activity ledger",
        "not_usage",
        "model_calls_and_tokens",
        "runtime_ledger",
        None,
        None,
        ("model_activity_record",),
        ("model_call_status",),
        "model_call_duration",
        "content_free_model_metrics",
        (),
        ("tool_invocation_identity", "tool_terminal_status"),
        (
            SourceEvidence(
                "src/ai_activity_ledger.py",
                ("append_ai_activity", "build_ai_activity_record"),
            ),
        ),
    ),
    UsageSource(
        "mcp_audit",
        "MCP audit events",
        "domain_audit",
        "mcp_calls_only",
        "runtime_domain_audit",
        None,
        None,
        ("mcp_audit_event", "tool_name", "argument_shape_hash"),
        ("domain_status",),
        "duration_ms",
        "argument_shape_without_values",
        (),
        ("builtin_and_plugin_coverage", "shared_wrapper_invocation_key"),
        (
            SourceEvidence(
                "src/mcp_audit_events.py",
                ("McpAuditEvent", "build_mcp_audit_event"),
            ),
        ),
    ),
    UsageSource(
        "tool_transaction_ledger",
        "Tool transaction ledger",
        "domain_audit",
        "effectful_evidence_only",
        "derived_runtime_evidence",
        None,
        None,
        ("transaction_evidence_key",),
        ("effect_status",),
        "not_a_usage_duration_source",
        "derived_claim_evidence",
        (),
        ("read_only_call_coverage", "independent_invocation_semantics"),
        (
            SourceEvidence(
                "src/tool_transaction_ledger.py",
                ("ToolTransaction", "transactions_from_tool_events"),
            ),
        ),
    ),
    UsageSource(
        "runtime_observability",
        "Runtime observability metrics",
        "not_usage",
        "low_cardinality_runtime_export",
        "runtime_snapshot",
        None,
        None,
        ("bounded_metric_name", "bounded_labels"),
        ("metric_sample",),
        "metric_specific",
        "content_free_low_cardinality",
        (),
        ("general_tool_usage_metric", "invocation_dedupe_key"),
        (
            SourceEvidence(
                "src/observability_metrics.py",
                ("RuntimeMetricSample", "render_prometheus_text"),
            ),
        ),
    ),
)


OVERLAPS: tuple[OverlapRisk, ...] = (
    OverlapRisk(
        "chat_metadata",
        "agent_run_ledger",
        "same_logical_calls_can_appear_in_both_legacy_surfaces",
        "no_reliable_common_invocation_key",
        "never_sum",
    ),
    OverlapRisk(
        "agent_run_ledger",
        "tool_execution_boundary",
        "run_events_can_describe_calls_observed_at_the_wrapper",
        "historical_key_not_canonical",
        "wrapper_is_future_primary_ledger_is_coverage_only",
    ),
    OverlapRisk(
        "ai_lens",
        "tool_execution_boundary",
        "wrapper_emits_optional_lens_lifecycle_events",
        "emitter_event_only",
        "count_once_at_wrapper",
    ),
    OverlapRisk(
        "mcp_audit",
        "tool_execution_boundary",
        "mcp_domain_audit_can_describe_a_wrapper_call",
        "no_shared_canonical_invocation_key_yet",
        "domain_audit_never_added_to_usage_total",
    ),
    OverlapRisk(
        "tool_transaction_ledger",
        "tool_execution_boundary",
        "effectful_transactions_are_derived_evidence_for_some_calls",
        "transaction_key_has_different_semantics",
        "transaction_counts_never_added_to_usage_total",
    ),
)


EXPECTED_BASELINE = {
    "chat_metadata": {
        "tool_event_count": 1104,
        "distinct_tool_name_count": 46,
        "message_count": 84,
        "session_count": 32,
    },
    "agent_run_ledger": {
        "run_started_event_count": 607,
        "output_event_count": 606,
        "distinct_tool_name_count": 43,
        "run_count": 111,
    },
}


def _violation(code: str, entity: str, detail: str) -> dict[str, str]:
    return {"code": code, "entity": entity, "detail": detail}


def _repo_path(root: Path, relative: str) -> Path | None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _python_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _validate_sources(root: Path, sources: Sequence[UsageSource]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for duplicate in _duplicates(item.source_id for item in sources):
        violations.append(_violation("duplicate_source", duplicate, "source_id must be unique"))
    for source in sources:
        if source.classification not in CLASSIFICATIONS:
            violations.append(
                _violation("invalid_classification", source.source_id, source.classification)
            )
        for evidence in source.evidence:
            path = _repo_path(root, evidence.path)
            if path is None:
                violations.append(_violation("unsafe_source_path", source.source_id, evidence.path))
                continue
            if not path.is_file():
                violations.append(_violation("missing_source_path", source.source_id, evidence.path))
                continue
            try:
                symbols = _python_symbols(path)
            except (OSError, SyntaxError, UnicodeError) as exc:
                violations.append(
                    _violation("unreadable_source", source.source_id, type(exc).__name__)
                )
                continue
            for symbol in evidence.symbols:
                if symbol not in symbols:
                    violations.append(
                        _violation(
                            "missing_source_symbol",
                            source.source_id,
                            f"{evidence.path}:{symbol}",
                        )
                    )
    primary = [item.source_id for item in sources if item.classification == "primary_candidate"]
    if primary != ["tool_execution_boundary"]:
        violations.append(
            _violation(
                "invalid_primary_source",
                "primary_candidate",
                ",".join(primary) or "none",
            )
        )
    return sorted(violations, key=lambda item: (item["code"], item["entity"], item["detail"]))


def _validate_baseline(sources: Sequence[UsageSource]) -> list[dict[str, str]]:
    observed = {
        item.source_id: dict(item.historical_counts)
        for item in sources
        if item.historical_counts
    }
    violations: list[dict[str, str]] = []
    if observed != EXPECTED_BASELINE:
        violations.append(
            _violation(
                "historical_baseline_drift",
                "legacy_aggregate_counts",
                "expected 1104/46/84/32 and 607/606/43/111",
            )
        )
    return violations


def _source_rows(root: Path, sources: Sequence[UsageSource]) -> list[dict]:
    rows: list[dict] = []
    for source in sorted(sources, key=lambda item: item.source_id):
        row = asdict(source)
        row["key_capabilities"] = list(source.key_capabilities)
        row["status_capabilities"] = list(source.status_capabilities)
        row["historically_missing"] = list(source.historically_missing)
        row["historical_counts"] = dict(sorted(source.historical_counts))
        row["time_bounds"] = {
            "start": source.time_start,
            "end": source.time_end,
        }
        row.pop("time_start")
        row.pop("time_end")
        row["evidence"] = []
        for evidence in sorted(source.evidence, key=lambda item: item.path):
            path = _repo_path(root, evidence.path)
            row["evidence"].append(
                {
                    "path": evidence.path,
                    "required_symbols": list(sorted(evidence.symbols)),
                    "sha256": _source_hash(path) if path and path.is_file() else None,
                }
            )
        rows.append(row)
    return rows


def audit_usage_sources(
    root: Path,
    *,
    sources: Sequence[UsageSource] = SOURCES,
    overlaps: Sequence[OverlapRisk] = OVERLAPS,
) -> dict:
    """Return a deterministic aggregate-only source and overlap report."""
    root = root.resolve()
    violations = [*_validate_sources(root, sources), *_validate_baseline(sources)]
    violations = sorted(
        violations, key=lambda item: (item["code"], item["entity"], item["detail"])
    )
    source_rows = _source_rows(root, sources)
    overlap_rows = [
        asdict(item)
        for item in sorted(overlaps, key=lambda item: (item.left, item.right, item.risk))
    ]
    classification_counts = Counter(item.classification for item in sources)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": INVENTORY_KIND,
        "baseline_date": BASELINE_DATE,
        "summary": {
            "source_count": len(source_rows),
            "overlap_risk_count": len(overlap_rows),
            "classification_counts": dict(sorted(classification_counts.items())),
            "chat_baseline": EXPECTED_BASELINE["chat_metadata"],
            "agent_run_baseline": EXPECTED_BASELINE["agent_run_ledger"],
            "primary_source": "tool_execution_boundary",
            "legacy_counts_additive": False,
            "independent_legacy_invocation_total": None,
            "aggregate_only": True,
            "runtime_or_private_data_read": False,
            "raw_message_visible": False,
            "command_visible": False,
            "tool_output_visible": False,
            "direct_identity_visible": False,
            "violation_count": len(violations),
            "clean": not violations,
        },
        "counting_policy": {
            "primary_measurement_point": "tool_execution_boundary",
            "legacy_sources": "coverage_only_not_additive",
            "domain_audits": "never_added_to_usage_total",
            "missing_common_key": "report_overlap_do_not_infer_independence",
        },
        "sources": source_rows,
        "overlaps": overlap_rows,
        "violations": violations,
    }


def render_report(payload: Mapping) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read usage source report: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ValueError("usage source report must be a JSON object")
    return value


def _write_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(payload), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/plans/tool-usage-source-overlap.json"),
    )
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="required safety mode; no raw-source mode exists",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", action="store_true", dest="print_payload")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.aggregate_only:
        print("Only --aggregate-only mode is supported.", file=sys.stderr)
        return 2
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    payload = audit_usage_sources(root)
    if args.print_payload:
        print(render_report(payload), end="")
    if args.check:
        if not output.is_file():
            print("Tool usage source report is missing.", file=sys.stderr)
            return 1
        try:
            existing = _read_json(output)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if payload != existing:
            print("Tool usage source overlap drift detected.", file=sys.stderr)
            return 1
    else:
        _write_json(output, payload)

    if payload["violations"]:
        for item in payload["violations"]:
            print(f"{item['code']}: {item['entity']}: {item['detail']}", file=sys.stderr)
        return 1
    print(
        "Tool usage source overlap clean: "
        f"{payload['summary']['source_count']} sources, "
        f"{payload['summary']['overlap_risk_count']} overlap risks, "
        "legacy counts non-additive"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
