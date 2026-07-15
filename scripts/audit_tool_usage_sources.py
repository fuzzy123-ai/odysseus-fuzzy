#!/usr/bin/env python3
"""Build the aggregate-only TUA0 source coverage and overlap snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "odysseus.tool_usage_source_overlap.v1"
SNAPSHOT_DATE = "2026-07-13"
SOURCE_CLASSIFICATIONS = {
    "primary_candidate",
    "coverage_only",
    "domain_audit",
    "not_usage",
}


def _source(
    source_id: str,
    classification: str,
    *,
    scope_capabilities: list[str],
    time_start: str | None,
    time_end: str | None,
    observed_counts: dict[str, int],
    key_capabilities: list[str],
    status_capabilities: list[str],
    privacy_capabilities: dict[str, bool],
    historically_missing_fields: list[str],
    overlap_risk: str,
    overlaps_with: list[str],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "classification": classification,
        "scope_capabilities": sorted(scope_capabilities),
        "time_bounds": {
            "start": time_start,
            "end": time_end,
        },
        "observed_counts": dict(sorted(observed_counts.items())),
        "key_capabilities": sorted(key_capabilities),
        "status_capabilities": sorted(status_capabilities),
        "privacy_capabilities": dict(sorted(privacy_capabilities.items())),
        "historically_missing_fields": sorted(historically_missing_fields),
        "overlap": {
            "risk": overlap_risk,
            "may_sum_with_other_sources": False,
            "with_sources": sorted(overlaps_with),
        },
    }


def build_snapshot() -> dict[str, Any]:
    """Return the frozen, content-free TUA0 baseline."""

    sources = [
        _source(
            "agent_run_ledger",
            "coverage_only",
            scope_capabilities=["agent_run_tool_starts", "agent_run_tool_outputs"],
            time_start="2026-06-13",
            time_end="2026-07-05",
            observed_counts={
                "runs": 111,
                "tool_names": 43,
                "tool_outputs": 606,
                "tool_starts": 607,
            },
            key_capabilities=["run_reference", "tool_name"],
            status_capabilities=["start_observed", "output_observed"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": False,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "canonical_analytics_id",
                "consistent_invocation_id",
                "normalized_terminal_status",
            ],
            overlap_risk="high",
            overlaps_with=["chat_metadata", "ai_lens"],
        ),
        _source(
            "ai_activity_ledger",
            "not_usage",
            scope_capabilities=["llm_call_count", "model_latency", "token_count"],
            time_start=None,
            time_end=None,
            observed_counts={},
            key_capabilities=["model_call_reference"],
            status_capabilities=["model_call_status"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": True,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "canonical_analytics_id",
                "invocation_id",
                "tool_effect_class",
                "tool_terminal_status",
            ],
            overlap_risk="not_applicable",
            overlaps_with=[],
        ),
        _source(
            "ai_lens",
            "primary_candidate",
            scope_capabilities=["execute_tool_block_boundary", "optional_tool_lifecycle_events"],
            time_start=None,
            time_end=None,
            observed_counts={},
            key_capabilities=["hashed_tool_reference"],
            status_capabilities=["tool_call_result", "tool_call_started"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": True,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "complete_global_coverage",
                "persistent_invocation_id",
                "versioned_terminal_status",
            ],
            overlap_risk="high",
            overlaps_with=["agent_run_ledger", "chat_metadata", "mcp_audit"],
        ),
        _source(
            "chat_metadata",
            "coverage_only",
            scope_capabilities=["chat_message_tool_metadata", "session_coverage"],
            time_start="2026-06-06",
            time_end="2026-06-17",
            observed_counts={
                "messages_with_tool_events": 84,
                "sessions": 32,
                "tool_events": 1104,
                "tool_names": 46,
            },
            key_capabilities=["message_reference", "session_reference", "tool_name"],
            status_capabilities=["partial_tool_status"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": False,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "canonical_analytics_id",
                "duration_ms",
                "invocation_id",
                "normalized_terminal_status",
            ],
            overlap_risk="high",
            overlaps_with=["agent_run_ledger", "ai_lens"],
        ),
        _source(
            "mcp_audit",
            "domain_audit",
            scope_capabilities=["mcp_security_audit", "mcp_tool_subset"],
            time_start=None,
            time_end=None,
            observed_counts={},
            key_capabilities=["argument_shape_hash", "mcp_tool_name"],
            status_capabilities=["duration", "status"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": True,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "built_in_coverage",
                "canonical_analytics_id",
                "global_invocation_id",
                "plugin_coverage",
            ],
            overlap_risk="high",
            overlaps_with=["ai_lens"],
        ),
        _source(
            "tool_transaction_ledger",
            "domain_audit",
            scope_capabilities=["effectful_transaction_evidence", "recovery_evidence"],
            time_start=None,
            time_end=None,
            observed_counts={},
            key_capabilities=["transaction_reference", "tool_name"],
            status_capabilities=["transaction_outcome"],
            privacy_capabilities={
                "aggregate_only": True,
                "content_free": True,
                "direct_identifiers_visible": False,
                "raw_content_visible": False,
            },
            historically_missing_fields=[
                "read_only_tool_coverage",
                "usage_counting_semantics",
            ],
            overlap_risk="high",
            overlaps_with=["ai_lens", "mcp_audit"],
        ),
    ]
    sources.sort(key=lambda item: item["source_id"])

    classification_counts = {
        classification: sum(item["classification"] == classification for item in sources)
        for classification in sorted(SOURCE_CLASSIFICATIONS)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": SNAPSHOT_DATE,
        "aggregate_only": True,
        "source_count": len(sources),
        "classification_counts": classification_counts,
        "counting_policy": {
            "independent_invocation_total": None,
            "legacy_counts_are_coverage_evidence": True,
            "may_sum_across_sources": False,
            "primary_signal_boundary": "execute_tool_block",
        },
        "sources": sources,
        "privacy": {
            "direct_identifiers_visible": False,
            "raw_content_visible": False,
            "raw_records_read": False,
        },
    }


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected schema version")
    if snapshot.get("aggregate_only") is not True:
        raise ValueError("snapshot must be aggregate-only")
    if snapshot.get("source_count") != 6 or len(snapshot.get("sources", [])) != 6:
        raise ValueError("snapshot must classify exactly six sources")
    if snapshot.get("counting_policy", {}).get("may_sum_across_sources") is not False:
        raise ValueError("overlapping source counts must never be summed")
    if snapshot.get("counting_policy", {}).get("independent_invocation_total") is not None:
        raise ValueError("the legacy sources do not establish an independent total")

    by_id = {item["source_id"]: item for item in snapshot["sources"]}
    if len(by_id) != 6:
        raise ValueError("source identifiers must be unique")
    expected_classifications = {
        "agent_run_ledger": "coverage_only",
        "ai_activity_ledger": "not_usage",
        "ai_lens": "primary_candidate",
        "chat_metadata": "coverage_only",
        "mcp_audit": "domain_audit",
        "tool_transaction_ledger": "domain_audit",
    }
    if {key: value["classification"] for key, value in by_id.items()} != expected_classifications:
        raise ValueError("source classification drift")

    if by_id["chat_metadata"]["observed_counts"] != {
        "messages_with_tool_events": 84,
        "sessions": 32,
        "tool_events": 1104,
        "tool_names": 46,
    }:
        raise ValueError("chat metadata baseline drift")
    if by_id["agent_run_ledger"]["observed_counts"] != {
        "runs": 111,
        "tool_names": 43,
        "tool_outputs": 606,
        "tool_starts": 607,
    }:
        raise ValueError("agent run baseline drift")

    for source in snapshot["sources"]:
        if source["classification"] not in SOURCE_CLASSIFICATIONS:
            raise ValueError("unknown source classification")
        if source["overlap"]["may_sum_with_other_sources"] is not False:
            raise ValueError("per-source overlap policy must remain non-additive")
        privacy = source["privacy_capabilities"]
        if privacy.get("raw_content_visible") is not False:
            raise ValueError("raw content visibility is forbidden")
        if privacy.get("direct_identifiers_visible") is not False:
            raise ValueError("direct identifier visibility is forbidden")

    privacy = snapshot.get("privacy", {})
    if privacy != {
        "direct_identifiers_visible": False,
        "raw_content_visible": False,
        "raw_records_read": False,
    }:
        raise ValueError("privacy invariant drift")


def render_snapshot(snapshot: dict[str, Any]) -> str:
    validate_snapshot(snapshot)
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="required guard: never inspect or emit raw usage records",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.aggregate_only:
        raise SystemExit("--aggregate-only is required")

    rendered = render_snapshot(build_snapshot())
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("tool usage source overlap snapshot drift")
        print("tool usage source overlap snapshot is current: 6 sources, no additive total")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print("wrote aggregate-only tool usage overlap snapshot: 6 sources, no additive total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
