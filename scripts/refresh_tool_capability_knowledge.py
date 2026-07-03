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
    build_tool_capability_memory_write_intent,
    execute_tool_capability_memory_write,
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
    }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    if not args.allow_index_failure and report.index_status.get("status") not in {"ok", "skipped"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
