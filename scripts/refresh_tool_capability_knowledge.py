#!/usr/bin/env python3
"""Refresh Odysseus tool index and system capability knowledge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_capability_maintenance import refresh_tool_capability_knowledge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--commit", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--allow-index-failure", action="store_true")
    args = parser.parse_args(argv)

    report = refresh_tool_capability_knowledge(
        reason=args.reason,
        commit=args.commit,
        data_dir=args.data_dir or None,
        persist=not args.no_persist,
        refresh_index=not args.skip_index,
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
    }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    if not args.allow_index_failure and report.index_status.get("status") not in {"ok", "skipped"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
