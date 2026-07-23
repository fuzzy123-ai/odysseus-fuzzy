import json
from pathlib import Path

import pytest

from scripts.roadmap_multi_agent_guidance import (
    DEFAULT_INDEX_PATH,
    ROOT,
    discover_roadmaps,
    load_guidance,
    select_guidance,
    validate_guidance,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _payload(paths: list[str]) -> dict:
    return {
        "kind": "test.guidance",
        "updated_at": "2026-07-11",
        "inventory": {"excluded": [], "total_count": len(paths)},
        "profiles": {
            "active_parallel": {
                "write_mode": "disjoint_scoped_writers",
                "parallelism": "Only disjoint paths.",
                "required_behavior": ["Claim one slice."],
            }
        },
        "authority": {},
        "global_ai_preflight": ["Inspect status."],
        "required_handoff": ["slice_id"],
        "roadmaps": [
            {
                "path": path,
                "profile": "active_parallel",
                "state": "active",
                "ai_hint": "Claim one explicit disjoint slice before any write.",
            }
            for path in paths
        ],
    }


def test_repository_guidance_covers_every_discovered_roadmap():
    payload = load_guidance(DEFAULT_INDEX_PATH)
    report = validate_guidance(payload, root=ROOT)

    assert report["valid"] is True, report["errors"]
    assert report["discovered_count"] == payload["inventory"]["total_count"]
    assert report["docs_plans_count"] == payload["inventory"]["docs_plans_count"]
    assert report["structured_specs_count"] == payload["inventory"]["structured_specs_count"]
    assert report["listed_count"] == payload["inventory"]["total_count"]
    assert not report["missing_paths"]
    assert not report["unknown_paths"]
    assert not report["duplicate_paths"]


def test_validation_reports_missing_and_unknown_entries(tmp_path):
    known = tmp_path / "docs" / "plans" / "known-roadmap.md"
    known.parent.mkdir(parents=True)
    known.write_text("# Known\n", encoding="utf-8")
    _write_json(tmp_path / "specs" / "roadmaps" / "runtime.v1.json", {"status": "active"})
    payload = _payload(["docs/plans/unknown-roadmap.md"])

    report = validate_guidance(payload, root=tmp_path)

    assert report["valid"] is False
    assert report["missing_paths"] == [
        "docs/plans/known-roadmap.md",
        "specs/roadmaps/runtime.v1.json",
    ]
    assert report["unknown_paths"] == ["docs/plans/unknown-roadmap.md"]


def test_discovery_honors_explicit_index_exclusion(tmp_path):
    roadmap = tmp_path / "docs" / "plans" / "work-roadmap.md"
    index = tmp_path / "docs" / "plans" / "multi-agent-execution-guidance.json"
    roadmap.parent.mkdir(parents=True)
    roadmap.write_text("# Work\n", encoding="utf-8")
    index.write_text("{}", encoding="utf-8")

    discovered = discover_roadmaps(
        tmp_path,
        excluded=["docs/plans/multi-agent-execution-guidance.json"],
    )

    assert discovered == ["docs/plans/work-roadmap.md"]


def test_select_guidance_returns_profile_and_global_rules():
    path = "docs/plans/example-roadmap.md"
    payload = _payload([path])

    packet = select_guidance(payload, "example-roadmap.md")

    assert packet["roadmap"]["path"] == path
    assert packet["profile_contract"]["write_mode"] == "disjoint_scoped_writers"
    assert packet["global_ai_preflight"] == ["Inspect status."]


def test_select_guidance_rejects_unknown_roadmap():
    payload = _payload(["docs/plans/example-roadmap.md"])

    with pytest.raises(KeyError, match="no guidance found"):
        select_guidance(payload, "missing-roadmap.md")
