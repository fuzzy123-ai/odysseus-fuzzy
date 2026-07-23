"""Validate and query per-roadmap multi-agent execution guidance."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = ROOT / "docs" / "plans" / "multi-agent-execution-guidance.json"
ROADMAP_NAME_PATTERN = re.compile(
    r"roadmap|masterplan|execution|build-plan|implementation|priority-process|workplan",
    re.IGNORECASE,
)
SUPPORTED_SUFFIXES = {".md", ".json"}


def _repo_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_guidance(path: Path = DEFAULT_INDEX_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("guidance index must contain a JSON object")
    return payload


def discover_roadmaps(
    root: Path = ROOT,
    *,
    excluded: Iterable[str] = (),
) -> list[str]:
    excluded_paths = {str(item).replace("\\", "/") for item in excluded}
    discovered: set[str] = set()

    plan_dir = root / "docs" / "plans"
    if plan_dir.is_dir():
        for path in plan_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if not ROADMAP_NAME_PATTERN.search(path.name):
                continue
            discovered.add(_repo_path(path, root))

    spec_dir = root / "specs" / "roadmaps"
    if spec_dir.is_dir():
        for path in spec_dir.glob("*.json"):
            if path.is_file():
                discovered.add(_repo_path(path, root))

    return sorted(discovered - excluded_paths)


def validate_guidance(
    payload: dict[str, Any],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    errors: list[str] = []
    inventory = payload.get("inventory")
    profiles = payload.get("profiles")
    entries = payload.get("roadmaps")

    if not isinstance(inventory, dict):
        inventory = {}
        errors.append("inventory must be an object")
    if not isinstance(profiles, dict) or not profiles:
        profiles = {}
        errors.append("profiles must be a non-empty object")
    else:
        for profile_name, contract in profiles.items():
            if not isinstance(contract, dict):
                errors.append(f"profile {profile_name!r} must be an object")
                continue
            if not isinstance(contract.get("write_mode"), str):
                errors.append(f"profile {profile_name!r} must define write_mode")
            if not isinstance(contract.get("parallelism"), str):
                errors.append(f"profile {profile_name!r} must define parallelism")
            behavior = contract.get("required_behavior")
            if not isinstance(behavior, list) or not behavior:
                errors.append(
                    f"profile {profile_name!r} must define required_behavior"
                )
    if not isinstance(entries, list):
        entries = []
        errors.append("roadmaps must be a list")

    listed_paths: list[str] = []
    profile_counts: Counter[str] = Counter()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"roadmaps[{position}] must be an object")
            continue
        path = entry.get("path")
        profile = entry.get("profile")
        state = entry.get("state")
        hint = entry.get("ai_hint")
        label = path if isinstance(path, str) and path else f"roadmaps[{position}]"
        if not isinstance(path, str) or not path:
            errors.append(f"roadmaps[{position}].path must be a non-empty string")
        else:
            normalized = path.replace("\\", "/")
            listed_paths.append(normalized)
            if normalized != path:
                errors.append(f"{label}: path must use forward slashes")
        if profile not in profiles:
            errors.append(f"{label}: unknown profile {profile!r}")
        elif isinstance(profile, str):
            profile_counts[profile] += 1
        if not isinstance(state, str) or not state.strip():
            errors.append(f"{label}: state must be a non-empty string")
        if not isinstance(hint, str) or len(hint.strip()) < 20:
            errors.append(f"{label}: ai_hint must contain a concrete instruction")

    duplicate_paths = sorted(
        path for path, count in Counter(listed_paths).items() if count > 1
    )
    for path in duplicate_paths:
        errors.append(f"duplicate roadmap entry: {path}")

    excluded = inventory.get("excluded", [])
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        excluded = []
        errors.append("inventory.excluded must be a list of paths")
    discovered_paths = discover_roadmaps(root, excluded=excluded)
    listed_set = set(listed_paths)
    discovered_set = set(discovered_paths)
    missing_paths = sorted(discovered_set - listed_set)
    unknown_paths = sorted(listed_set - discovered_set)
    for path in missing_paths:
        errors.append(f"missing roadmap guidance: {path}")
    for path in unknown_paths:
        errors.append(f"guidance references an undiscovered roadmap: {path}")

    expected_total = inventory.get("total_count")
    if expected_total != len(discovered_paths):
        errors.append(
            "inventory.total_count does not match discovered roadmap count: "
            f"expected {expected_total!r}, discovered {len(discovered_paths)}"
        )
    if len(entries) != len(discovered_paths):
        errors.append(
            f"roadmaps entry count {len(entries)} does not match discovered count "
            f"{len(discovered_paths)}"
        )

    docs_plans_count = sum(
        path.startswith("docs/plans/") for path in discovered_paths
    )
    structured_specs_count = sum(
        path.startswith("specs/roadmaps/") for path in discovered_paths
    )
    if inventory.get("docs_plans_count", docs_plans_count) != docs_plans_count:
        errors.append(
            "inventory.docs_plans_count does not match discovery: "
            f"expected {inventory.get('docs_plans_count')!r}, "
            f"discovered {docs_plans_count}"
        )
    if (
        inventory.get("structured_specs_count", structured_specs_count)
        != structured_specs_count
    ):
        errors.append(
            "inventory.structured_specs_count does not match discovery: "
            f"expected {inventory.get('structured_specs_count')!r}, "
            f"discovered {structured_specs_count}"
        )

    actual_profile_counts = dict(sorted(profile_counts.items()))
    analysis_summary = payload.get("analysis_summary", {})
    declared_profile_counts = analysis_summary.get("profile_counts")
    if (
        declared_profile_counts is not None
        and declared_profile_counts != actual_profile_counts
    ):
        errors.append(
            "analysis_summary.profile_counts does not match roadmap entries: "
            f"declared {declared_profile_counts!r}, "
            f"actual {actual_profile_counts!r}"
        )

    return {
        "valid": not errors,
        "index_kind": payload.get("kind"),
        "updated_at": payload.get("updated_at"),
        "discovered_count": len(discovered_paths),
        "docs_plans_count": docs_plans_count,
        "structured_specs_count": structured_specs_count,
        "listed_count": len(entries),
        "profile_counts": actual_profile_counts,
        "missing_paths": missing_paths,
        "unknown_paths": unknown_paths,
        "duplicate_paths": duplicate_paths,
        "errors": errors,
    }


def select_guidance(payload: dict[str, Any], roadmap: str) -> dict[str, Any]:
    query = roadmap.replace("\\", "/").lstrip("./")
    entries = payload.get("roadmaps", [])
    exact = [entry for entry in entries if entry.get("path") == query]
    if len(exact) == 1:
        entry = exact[0]
    else:
        by_name = [entry for entry in entries if Path(str(entry.get("path", ""))).name == query]
        if not by_name:
            raise KeyError(f"no guidance found for {roadmap!r}")
        if len(by_name) > 1:
            matches = ", ".join(str(item["path"]) for item in by_name)
            raise KeyError(f"ambiguous roadmap name {roadmap!r}: {matches}")
        entry = by_name[0]

    profile_name = entry["profile"]
    return {
        "roadmap": entry,
        "profile_contract": payload["profiles"][profile_name],
        "authority": payload.get("authority", {}),
        "global_ai_preflight": payload.get("global_ai_preflight", []),
        "required_handoff": payload.get("required_handoff", []),
    }


def render_validation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Roadmap Multi-Agent Guidance Audit",
        "",
        f"- Valid: {'yes' if report['valid'] else 'no'}",
        f"- Discovered roadmaps: {report['discovered_count']}",
        f"- docs/plans roadmaps: {report['docs_plans_count']}",
        f"- specs/roadmaps JSON: {report['structured_specs_count']}",
        f"- Guidance entries: {report['listed_count']}",
        "",
        "## Profiles",
        "",
        "| Profile | Roadmaps |",
        "| --- | ---: |",
    ]
    for profile, count in report["profile_counts"].items():
        lines.append(f"| {profile} | {count} |")
    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def render_guidance_markdown(packet: dict[str, Any]) -> str:
    roadmap = packet["roadmap"]
    profile = packet["profile_contract"]
    lines = [
        f"# Multi-Agent Guidance: {roadmap['path']}",
        "",
        f"- Profile: `{roadmap['profile']}`",
        f"- State: `{roadmap['state']}`",
        f"- Write mode: `{profile['write_mode']}`",
        f"- AI hint: {roadmap['ai_hint']}",
        "",
        "## Profile Rules",
        "",
        f"- Parallelism: {profile['parallelism']}",
    ]
    lines.extend(f"- {item}" for item in profile["required_behavior"])
    lines.extend(["", "## Preflight", ""])
    lines.extend(f"- {item}" for item in packet["global_ai_preflight"])
    lines.extend(["", "## Required Handoff", ""])
    lines.extend(f"- `{item}`" for item in packet["required_handoff"])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or query roadmap multi-agent execution guidance."
    )
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--roadmap", help="Repository path or unique roadmap filename")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = load_guidance(args.index)
    validation = validate_guidance(payload, root=args.root)
    if args.roadmap:
        if not validation["valid"]:
            if args.format == "markdown":
                print(render_validation_markdown(validation), end="")
            else:
                print(json.dumps({"validation": validation}, indent=2, ensure_ascii=True))
            return 1
        else:
            try:
                output = select_guidance(payload, args.roadmap)
            except KeyError as exc:
                output = {"error": str(exc)}
                if args.format == "markdown":
                    print(f"# Guidance Error\n\n- {exc}\n")
                else:
                    print(json.dumps(output, indent=2, ensure_ascii=True))
                return 2
        if args.format == "markdown":
            print(render_guidance_markdown(output), end="")
        else:
            print(json.dumps(output, indent=2, ensure_ascii=True))
    elif args.format == "markdown":
        print(render_validation_markdown(validation), end="")
    else:
        print(json.dumps(validation, indent=2, ensure_ascii=True))
    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
