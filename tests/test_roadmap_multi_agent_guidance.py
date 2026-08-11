import json
from pathlib import Path
import sys

import pytest

from scripts.roadmap_multi_agent_guidance import (
    DEFAULT_INDEX_PATH,
    GuidanceLoadError,
    MAX_GUIDANCE_BYTES,
    ROOT,
    discover_roadmaps,
    load_guidance,
    main,
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
    assert report["discovered_count"] == report["listed_count"] == 139
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


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind":"first","kind":"second"}',
        '{"nested":{"state":"first","state":"second"}}',
    ],
)
def test_load_guidance_rejects_duplicate_keys_at_every_depth(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "duplicate_key"
    assert str(captured.value) == "guidance index contains duplicate JSON keys"
    assert "first" not in str(captured.value)
    assert "second" not in str(captured.value)


@pytest.mark.parametrize(
    "raw",
    [
        '{"private_marker":"DO_NOT_LEAK",',
        '{"private_marker":"DO_NOT_LEAK"',
    ],
)
def test_load_guidance_maps_malformed_or_truncated_json_to_fixed_error(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "malformed.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "malformed_json"
    assert str(captured.value) == "guidance index contains malformed JSON"
    assert "DO_NOT_LEAK" not in str(captured.value)


@pytest.mark.parametrize("raw", ["[]", "null", '"DO_NOT_LEAK"', "17"])
def test_load_guidance_rejects_non_object_roots_with_fixed_error(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "non-object.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "non_object_root"
    assert str(captured.value) == "guidance index root must be an object"
    assert "DO_NOT_LEAK" not in str(captured.value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    "template",
    [
        "{constant}",
        '{"PRIVATE_NONFINITE_MARKER":{constant}}',
        '["PRIVATE_NONFINITE_MARKER",{constant}]',
    ],
)
def test_load_guidance_rejects_nonfinite_values_at_every_depth(
    tmp_path: Path, template: str, constant: str
) -> None:
    path = tmp_path / "nonfinite.json"
    path.write_text(template.replace("{constant}", constant), encoding="utf-8")

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "malformed_json"
    assert str(captured.value) == "guidance index contains malformed JSON"
    assert captured.value.__cause__ is None
    assert "PRIVATE_NONFINITE_MARKER" not in repr(captured.value)
    assert "ValueError" not in repr(captured.value)


def test_load_guidance_rejects_invalid_utf8_without_raw_content(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.json"
    path.write_bytes(b'{"private_marker":"DO_NOT_LEAK\xff"}')

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "invalid_utf8"
    assert str(captured.value) == "guidance index is not valid UTF-8"
    assert "DO_NOT_LEAK" not in str(captured.value)


def test_load_guidance_enforces_a_byte_limit_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (MAX_GUIDANCE_BYTES + 1))

    with pytest.raises(GuidanceLoadError) as captured:
        load_guidance(path)

    assert captured.value.code == "size_limit"
    assert str(captured.value) == "guidance index exceeds the parser size limit"


@pytest.mark.parametrize(
    "document",
    [
        '{"private_marker":"DO_NOT_LEAK",',
        '{"private_marker":"DO_NOT_LEAK","value":NaN}',
    ],
)
def test_cli_serializes_parser_failure_without_raw_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    document: str,
) -> None:
    path = tmp_path / "malformed.json"
    path.write_text(document, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["roadmap_multi_agent_guidance.py", "--index", str(path)])

    assert main() == 1

    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["valid"] is False
    assert report["errors"] == ["guidance index contains malformed JSON"]
    assert "DO_NOT_LEAK" not in output
    assert "JSONDecodeError" not in output
    assert "ValueError" not in output


def test_open_work_guidance_lookup_remains_available() -> None:
    payload = load_guidance(DEFAULT_INDEX_PATH)

    packet = select_guidance(payload, "open-work-completion-master-roadmap.json")

    assert packet["roadmap"]["path"] == (
        "docs/plans/open-work-completion-master-roadmap.json"
    )
    assert packet["profile_contract"] == payload["profiles"][
        packet["roadmap"]["profile"]
    ]
