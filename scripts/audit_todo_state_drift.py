"""Print a read-only Todo/Memory/digest drift audit and repair preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.constants import APP_DB, MEMORY_FILE
from src.todo_state_drift_audit import (
    TodoStateDriftAuditError,
    audit_todo_state_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read Notes and Memory without mutation, then print a privacy-safe "
            "Todo drift report and non-applying repair preview."
        )
    )
    parser.add_argument("--owner", required=True, help="Exact owner scope to audit")
    parser.add_argument("--database", default=APP_DB, help="SQLite app database path")
    parser.add_argument("--memory-file", default=MEMORY_FILE, help="Memory JSON path")
    parser.add_argument("--digest-limit", type=int, default=20)
    parser.add_argument(
        "--review-details",
        action="store_true",
        help="Include ephemeral exact synthetic/private review details in stdout",
    )
    parser.add_argument(
        "--operator-authorized",
        action="store_true",
        help="Acknowledge that exact review output is operator-authorized and must not be persisted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.review_details and not args.operator_authorized:
        parser.error("--review-details requires --operator-authorized")
    try:
        report = audit_todo_state_files(
            owner=args.owner,
            database_path=args.database,
            memory_path=args.memory_file,
            digest_limit=args.digest_limit,
            include_review_details=args.review_details,
            operator_authorized=args.operator_authorized,
        )
    except TodoStateDriftAuditError as exc:
        print(json.dumps({
            "schema": "odysseus.todo_state_drift_audit.v1",
            "status": "blocked",
            "reason": str(exc),
            "read_only": True,
            "mutations_performed": False,
            "raw_content_visible": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if report.get("status") == "drift_detected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
