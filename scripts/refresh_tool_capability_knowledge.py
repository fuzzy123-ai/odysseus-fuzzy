#!/usr/bin/env python3
"""Refresh Odysseus tool index and system capability knowledge."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_capability_maintenance import (
    append_tool_capability_raptorgraph_event,
    build_tool_capability_memory_write_intent,
    execute_tool_capability_memory_write,
    read_tool_capability_diagnostics,
    refresh_tool_capability_knowledge,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--commit", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--allow-index-failure", action="store_true")
    parser.add_argument("--write-memory", action="store_true")
    parser.add_argument("--dry-run-memory-write", action="store_true")
    parser.add_argument("--memory-owner", default=os.getenv("ODYSSEUS_TOOL_CAPABILITY_MEMORY_OWNER", "system"))
    parser.add_argument("--write-raptorgraph", action="store_true")
    parser.add_argument("--dry-run-raptorgraph-write", action="store_true")
    parser.add_argument("--raptorgraph-dir", default="")
    parser.add_argument("--read-diagnostics", action="store_true")
    parser.add_argument("--assert-acceptance", action="store_true")
    args = parser.parse_args(argv)

    report = refresh_tool_capability_knowledge(
        reason=args.reason,
        commit=args.commit,
        data_dir=args.data_dir or None,
        persist=not args.no_persist,
        refresh_index=not args.skip_index,
    )
    memory_write = {"status": "not_requested"}
    write_requested = args.write_memory or os.getenv("ODYSSEUS_TOOL_CAPABILITY_MEMORY_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}
    if write_requested:
        intent = build_tool_capability_memory_write_intent(
            snapshot=report.snapshot,
            memory_records=report.memory_records,
            raptorgraph_event=report.raptorgraph_event,
            owner=args.memory_owner,
        )
        memory_manager = None
        memory_vector = None
        if not args.dry_run_memory_write:
            from src.constants import DATA_DIR
            from src.memory import MemoryManager
            from src.memory_vector import MemoryVectorStore

            memory_manager = MemoryManager(DATA_DIR)
            memory_vector = MemoryVectorStore(DATA_DIR)
        write_report = execute_tool_capability_memory_write(
            intent,
            write_gate_open=True,
            dry_run=args.dry_run_memory_write,
            memory_manager=memory_manager,
            memory_vector=memory_vector,
            owner=args.memory_owner,
            confirmation_source="tool_capability_update_gate",
        )
        memory_write = write_report.to_dict()
    raptorgraph_write = {"status": "not_requested"}
    raptorgraph_requested = args.write_raptorgraph or os.getenv("ODYSSEUS_TOOL_CAPABILITY_RAPTOR_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}
    if raptorgraph_requested:
        if args.dry_run_raptorgraph_write:
            raptorgraph_write = {
                "status": "planned",
                "reason": "dry_run_only",
                "event": report.raptorgraph_event.get("event"),
                "memory_record_ids": list(report.raptorgraph_event.get("memory_record_ids") or ()),
                "raw_content_visible": False,
            }
        else:
            raptorgraph_write = append_tool_capability_raptorgraph_event(
                report.raptorgraph_event,
                root=args.raptorgraph_dir or None,
            ).to_dict()
    diagnostics = {"status": "not_requested"}
    if args.read_diagnostics or args.assert_acceptance:
        diagnostics = read_tool_capability_diagnostics(
            data_dir=args.data_dir or None,
            raptorgraph_dir=args.raptorgraph_dir or None,
        )
    summary = {
        "status": report.status,
        "snapshot_id": report.snapshot.get("id"),
        "fingerprint": report.snapshot.get("fingerprint"),
        "builtin_tool_count": report.snapshot.get("builtin_tool_count"),
        "memory_records": len(report.memory_records),
        "raptorgraph_event": bool(report.raptorgraph_event),
        "persisted": report.persisted,
        "index_status": report.index_status,
        "memory_write": memory_write,
        "raptorgraph_write": raptorgraph_write,
        "diagnostics": diagnostics,
    }
    acceptance_errors = _acceptance_errors(
        summary,
        expect_raptorgraph_store=bool(raptorgraph_requested and not args.dry_run_raptorgraph_write),
    ) if args.assert_acceptance else []
    if args.assert_acceptance:
        summary["acceptance"] = {
            "status": "passed" if not acceptance_errors else "failed",
            "errors": acceptance_errors,
        }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    if not args.allow_index_failure and report.index_status.get("status") not in {"ok", "skipped"}:
        return 2
    if acceptance_errors:
        return 3
    return 0


def _acceptance_errors(summary: dict, *, expect_raptorgraph_store: bool) -> list[str]:
    errors: list[str] = []
    if summary.get("status") != "refreshed":
        errors.append("refresh_status_not_refreshed")
    if not summary.get("snapshot_id"):
        errors.append("missing_snapshot_id")
    if int(summary.get("memory_records") or 0) <= 0:
        errors.append("missing_memory_records")
    if not summary.get("raptorgraph_event"):
        errors.append("missing_raptorgraph_event")
    for key in ("memory_write", "raptorgraph_write"):
        payload = summary.get(key) if isinstance(summary.get(key), dict) else {}
        if payload.get("raw_content_visible"):
            errors.append(f"{key}_raw_content_visible")
    diagnostics = summary.get("diagnostics") if isinstance(summary.get("diagnostics"), dict) else {}
    if diagnostics.get("status") != "success":
        errors.append("diagnostics_not_success")
    if diagnostics.get("raw_content_visible"):
        errors.append("diagnostics_raw_content_visible")
    records = diagnostics.get("memory_records") if isinstance(diagnostics.get("memory_records"), dict) else {}
    if int(records.get("count") or 0) != int(summary.get("memory_records") or 0):
        errors.append("diagnostics_memory_record_count_mismatch")
    graph = diagnostics.get("raptorgraph") if isinstance(diagnostics.get("raptorgraph"), dict) else {}
    if not graph.get("event_present"):
        errors.append("diagnostics_raptorgraph_event_missing")
    if expect_raptorgraph_store and int(graph.get("store_event_count") or 0) <= 0:
        errors.append("diagnostics_raptorgraph_store_empty")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
