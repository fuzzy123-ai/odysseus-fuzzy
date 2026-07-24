"""CLI for the explicitly-scoped, read-only Todo drift audit."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.todo_state_drift_audit import SCHEMA, TodoStateDriftAuditError, audit_todo_state_files

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only, owner-exact Todo state audit")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--memory-file", required=True)
    parser.add_argument("--digest-limit", type=int, default=20)
    parser.add_argument("--review-details", action="store_true")
    parser.add_argument("--operator-authorized", action="store_true")
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_todo_state_files(owner=args.owner, database_path=args.database, memory_path=args.memory_file,
                                        digest_limit=args.digest_limit, include_review_details=args.review_details,
                                        operator_authorized=args.operator_authorized)
    except TodoStateDriftAuditError:
        print(json.dumps({"schema": SCHEMA, "status": "blocked", "read_only": True,
                          "mutations_performed": False, "raw_content_visible": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 1 if report["status"] == "drift_detected" else 0 if report["status"] == "consistent" else 2

if __name__ == "__main__": raise SystemExit(main())
