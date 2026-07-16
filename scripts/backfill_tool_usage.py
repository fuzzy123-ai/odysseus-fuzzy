"""Run the synthetic-only metadata backfill preview.

There is intentionally no apply mode and no caller-supplied filesystem path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.tool_usage_backfill import preview_tool_usage_backfill  # noqa: E402


FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "tool_usage"
PRIMARY_FIXTURE = FIXTURE_ROOT / "synthetic_primary.jsonl"
COVERAGE_FIXTURE = FIXTURE_ROOT / "synthetic_agent_coverage.json"


def _load_primary_fixture() -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for line in PRIMARY_FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("synthetic primary fixture rows must be objects")
        rows.append(value)
    return tuple(rows)


def _load_coverage_count() -> int:
    value = json.loads(COVERAGE_FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("source_id") != "synthetic_agent_ledger":
        raise ValueError("synthetic coverage fixture is invalid")
    count = value.get("terminal_count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("synthetic coverage count is invalid")
    return count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview the fixed synthetic metadata-only tool usage fixture.",
    )
    parser.add_argument("--source", choices=("synthetic-fixture",), required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required immutable mode; no writes or destination store exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    result = preview_tool_usage_backfill(
        _load_primary_fixture(),
        agent_ledger_coverage_count=_load_coverage_count(),
    )
    print(json.dumps(result.to_safe_report(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
