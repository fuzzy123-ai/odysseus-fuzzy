#!/usr/bin/env python3
"""Report oversized source-like files for refactoring planning.

The report is advisory by default. It does not edit files, fail CI, or decide a
refactor target on its own. It gives ABC slices a repeatable view of the same
threshold bands used by the large-file refactoring overview.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]

MONITOR_MIN = 600
WARNING_MIN = 801
CANDIDATE_MIN = 2001

SOURCE_EXTENSIONS = {
    ".css",
    ".html",
    ".htm",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
}

ROOT_EXCLUDED_DIRS = {
    "backups",
    "build",
    "coverage",
    "data",
    "dist",
    "logs",
    "output",
    "vault",
}

NESTED_EXCLUDED_DIRS = {
    ".git",
    ".agents",
    ".codex",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}

PRODUCTION_EXCLUDED_PREFIXES = (
    "docs/",
    "specs/",
    "tests/",
)

MOCKUP_MARKERS = (
    "/mockup",
    "/mockups/",
    "-mockup.",
)

ALLOWLIST_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("services/hwfit/data/*.json", "generated_data", "large hardware-fit model index is data, not runtime code"),
    ("tests/OVERSIZED_TEST_SPLIT_PLAN.md", "planning_doc", "test split planning artifact"),
    ("tests/LAYOUT_INVENTORY.md", "planning_doc", "test/layout inventory artifact"),
    ("tests/TESTING_STANDARD.md", "planning_doc", "testing policy document"),
    ("*.min.js", "minified_asset", "minified assets are not hand-refactored"),
    ("*.min.css", "minified_asset", "minified assets are not hand-refactored"),
)


@dataclass(frozen=True, slots=True)
class LargeFileMetric:
    path: str
    lines: int
    band: str
    area: str
    production_runtime: bool
    allowlisted: bool
    allowlist_category: str = ""
    allowlist_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "lines": self.lines,
            "band": self.band,
            "area": self.area,
            "production_runtime": self.production_runtime,
            "allowlisted": self.allowlisted,
            "allowlist_category": self.allowlist_category,
            "allowlist_reason": self.allowlist_reason,
        }


def collect_large_file_metrics(root: str | Path = ROOT) -> tuple[LargeFileMetric, ...]:
    base = Path(root).resolve()
    metrics: list[LargeFileMetric] = []
    for path in sorted(_source_like_files(base)):
        rel = path.relative_to(base).as_posix()
        lines = _line_count(path)
        band = _band(lines)
        if band == "normal":
            continue
        category, reason = _allowlist_for(rel)
        metrics.append(
            LargeFileMetric(
                path=rel,
                lines=lines,
                band=band,
                area=rel.split("/", 1)[0],
                production_runtime=_is_production_runtime(rel),
                allowlisted=bool(category),
                allowlist_category=category,
                allowlist_reason=reason,
            )
        )
    return tuple(sorted(metrics, key=lambda item: (-item.lines, item.path)))


def build_report(root: str | Path = ROOT) -> dict[str, Any]:
    metrics = collect_large_file_metrics(root)
    source_summary = _summary(metrics)
    production = tuple(item for item in metrics if item.production_runtime)
    production_summary = _summary(production)
    candidates = tuple(item for item in production if item.band == "candidate" and not item.allowlisted)
    return {
        "schema": "odysseus.large_file_report.v1",
        "thresholds": {
            "monitor": "600-800",
            "warning": "801-2000",
            "candidate": ">2000",
        },
        "source_like_summary": source_summary,
        "production_runtime_summary": production_summary,
        "candidate_count": len(candidates),
        "allowlisted_count": sum(1 for item in metrics if item.allowlisted),
        "items": tuple(item.to_dict() for item in metrics),
    }


def render_markdown(report: dict[str, Any]) -> str:
    thresholds = report["thresholds"]
    lines = [
        "# Large File Report",
        "",
        "Thresholds:",
        f"- monitor: {thresholds['monitor']}",
        f"- warning: {thresholds['warning']}",
        f"- candidate: {thresholds['candidate']}",
        "",
        "## Summary",
        "",
        "| View | Monitor | Warning | Candidate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key in (
        ("Source-like", "source_like_summary"),
        ("Production/runtime", "production_runtime_summary"),
    ):
        summary = report[key]
        lines.append(
            f"| {label} | {summary['monitor']} | {summary['warning']} | {summary['candidate']} |"
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "| Lines | Band | Runtime | Allowlist | File |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for item in report["items"]:
        allowlist = item["allowlist_category"] or "-"
        runtime = "yes" if item["production_runtime"] else "no"
        lines.append(
            f"| {item['lines']} | {item['band']} | {runtime} | {allowlist} | `{item['path']}` |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _source_like_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if rel_parts and rel_parts[0] in ROOT_EXCLUDED_DIRS:
            continue
        if any(part in NESTED_EXCLUDED_DIRS for part in rel_parts[:-1]):
            continue
        if any(part.startswith(".pytest-") for part in rel_parts[:-1]):
            continue
        if any(part.startswith(".tmp") for part in rel_parts[:-1]):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        yield path


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _line in handle)


def _band(lines: int) -> str:
    if MONITOR_MIN <= lines <= 800:
        return "monitor"
    if WARNING_MIN <= lines <= 2000:
        return "warning"
    if lines >= CANDIDATE_MIN:
        return "candidate"
    return "normal"


def _is_production_runtime(rel_path: str) -> bool:
    if rel_path.startswith(PRODUCTION_EXCLUDED_PREFIXES):
        return False
    lowered = f"/{rel_path.lower()}"
    if any(marker in lowered for marker in MOCKUP_MARKERS):
        return False
    return True


def _allowlist_for(rel_path: str) -> tuple[str, str]:
    for pattern, category, reason in ALLOWLIST_PATTERNS:
        if fnmatch.fnmatch(rel_path, pattern):
            return category, reason
    return "", ""


def _summary(metrics: Iterable[LargeFileMetric]) -> dict[str, int]:
    summary = {"monitor": 0, "warning": 0, "candidate": 0}
    for item in metrics:
        summary[item.band] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="repository root to scan")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)

    report = build_report(args.root)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
