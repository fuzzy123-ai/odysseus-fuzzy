"""CLI for the offline real-backend RAPTOR performance suite."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory_perf_suite_real_raptor import (  # noqa: E402
    REAL_RAPTOR_PROFILES,
    render_real_raptor_markdown,
    report_to_json,
    run_real_raptor_benchmark,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real Obsidian Memory/RAPTOR backend on a temporary synthetic corpus."
    )
    parser.add_argument("--profile", choices=tuple(REAL_RAPTOR_PROFILES), default="quick")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--markdown-output", default="")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_real_raptor_benchmark(args.profile)
    encoded = report_to_json(report)
    print(encoded)
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    if args.markdown_output:
        output = Path(args.markdown_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_real_raptor_markdown(report), encoding="utf-8")
    return 0 if report["release_verdict"] in {"go", "diagnostic"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
