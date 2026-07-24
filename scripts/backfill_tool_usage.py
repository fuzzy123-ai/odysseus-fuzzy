#!/usr/bin/env python3
"""Run the bundled synthetic tool-usage backfill fixture in dry-run mode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_usage_backfill import dry_run_synthetic_fixture


SYNTHETIC_FIXTURE = (
    ROOT / "tests" / "fixtures" / "tool_usage" / "synthetic_chat_tool_events.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the bundled synthetic metadata-only backfill fixture."
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=("synthetic-fixture",),
        help="Only the repository-owned synthetic fixture is available.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Retained for an explicit operator-readable invocation; always enabled.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.source != "synthetic-fixture" or args.dry_run is not True:
        raise SystemExit(2)
    fixture = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    result = dry_run_synthetic_fixture(fixture)
    print(json.dumps(result.report.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
