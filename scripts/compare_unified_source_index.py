#!/usr/bin/env python3
"""Run the content-free synthetic USI legacy compatibility comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.unified_source_index_legacy_comparison import (  # noqa: E402
    LegacyComparisonError,
    run_synthetic_comparison,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare bounded synthetic Personal Docs, RAG, Memory and "
            "Obsidian/Lens observations with USI evidence."
        )
    )
    parser.add_argument(
        "--fixture",
        choices=("complete", "missing", "locator_mismatch", "policy_mismatch"),
        default="complete",
        help="Deterministic synthetic fixture profile; no live corpus is read.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Return a non-zero status when any synthetic cutover gate fails.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_synthetic_comparison(args.fixture)
    except LegacyComparisonError as exc:
        print(f"USI comparison failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    payload = report.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(
        "USI synthetic comparison: "
        f"profile={report.fixture_profile} "
        f"lanes={len(report.lanes)} "
        f"ready={str(report.all_gates_ready).lower()} "
        "live_cutover_authorized=false"
    )
    return 1 if args.check and not report.all_gates_ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
