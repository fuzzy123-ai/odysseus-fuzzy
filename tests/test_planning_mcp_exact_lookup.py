import hashlib
import json
import os
from pathlib import Path
import traceback

import pytest

import src.planning_mcp_service as service_module
from src.planning_mcp_service import (
    MAX_SOURCE_BYTES,
    PlanningMcpService,
    PlanningServiceError,
)
from src.planning_source_inventory import planning_source_id


def _roadmap(roadmap_id: str, title: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "odysseus.planning.roadmap",
        "project_id": "lookup-project",
        "roadmap_id": roadmap_id,
        "title": title,
        "goal": f"Read {title} exactly once.",
        "status": "planned",
        "slices": [],
        "gates": [],
        "source_refs": [],
        "verification": [],
        "stop_rules": [],
    }


def _write_roadmap(root: Path, relative: str, roadmap_id: str, title: str) -> tuple[Path, bytes]:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        _roadmap(roadmap_id, title),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(raw)
    return path, raw


def _norm(path: os.PathLike[str] | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class _StatOverride:
    def __init__(self, value, **overrides):
        self._value = value
        for name, override in overrides.items():
            setattr(self, name, override)

    def __getattr__(self, name):
        return getattr(self._value, name)


def test_exact_path_reads_only_target_and_hashes_the_same_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target, raw = _write_roadmap(
        tmp_path,
        "docs/plans/Target-Roadmap.json",
        "target-map",
        "Target Roadmap",
    )
    other, _ = _write_roadmap(
        tmp_path,
        "specs/roadmaps/other.json",
        "other-map",
        "Other Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    opened: list[str] = []
    original_open = service_module.os.open

    def tracking_open(path, flags):
        opened.append(_norm(path))
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", tracking_open)
    result = service.read_roadmap("docs/plans/Target-Roadmap.json")

    assert opened == [_norm(target)]
    assert _norm(other) not in opened
    assert result["source"]["source_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert result["roadmap"]["title"] == "Target Roadmap"


def test_source_id_resolution_is_names_only_unknown_is_zero_read_and_matches_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target, _ = _write_roadmap(
        tmp_path,
        "docs/plans/id-roadmap.json",
        "id-map",
        "ID Roadmap",
    )
    other, _ = _write_roadmap(
        tmp_path,
        "docs/plans/unrelated-roadmap.json",
        "other-map",
        "Unrelated Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    opened: list[str] = []
    original_open = service_module.os.open

    def tracking_open(path, flags):
        opened.append(_norm(path))
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", tracking_open)
    with pytest.raises(PlanningServiceError) as missing:
        service.read_roadmap("repo-plan:0000000000000000")
    assert missing.value.code == "roadmap_not_found"
    assert opened == []

    source_id = planning_source_id("docs/plans/id-roadmap.json")
    by_id = service.read_roadmap(source_id)
    assert opened == [_norm(target)]
    assert _norm(other) not in opened

    opened.clear()
    by_path = service.read_roadmap("docs/plans/id-roadmap.json")
    assert opened == [_norm(target)]
    assert by_id["source"] == by_path["source"]
    assert by_id["roadmap"] == by_path["roadmap"]


@pytest.mark.parametrize(
    "alias",
    [
        "docs%2fplans/oracle-roadmap.json",
        "docs/plans/%6fracle-roadmap.json",
        "ｄocs/plans/oracle-roadmap.json",
        r"docs\plans\oracle-roadmap.json",
        " docs/plans/oracle-roadmap.json ",
    ],
)
def test_noncanonical_public_aliases_cannot_select_canonical_candidate(
    tmp_path: Path,
    monkeypatch,
    alias: str,
) -> None:
    target, _ = _write_roadmap(
        tmp_path,
        "docs/plans/oracle-roadmap.json",
        "oracle-map",
        "Oracle Roadmap",
    )
    other, _ = _write_roadmap(
        tmp_path,
        "specs/roadmaps/other-roadmap.json",
        "other-map",
        "Other Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    opened: list[str] = []
    original_open = service_module.os.open

    def tracking_open(path, flags):
        opened.append(_norm(path))
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", tracking_open)
    with pytest.raises(PlanningServiceError) as rejected:
        service.read_roadmap(alias)
    assert rejected.value.code == "invalid_path"
    assert opened == []

    by_path = service.read_roadmap("docs/plans/oracle-roadmap.json")
    assert by_path["roadmap"]["roadmap_id"] == "oracle-map"
    assert opened == [_norm(target)]
    assert _norm(other) not in opened

    opened.clear()
    by_id = service.read_roadmap(planning_source_id("docs/plans/oracle-roadmap.json"))
    assert by_id["roadmap"] == by_path["roadmap"]
    assert opened == [_norm(target)]
    assert _norm(other) not in opened


def test_list_reads_each_json_once_never_markdown_and_preserves_order_and_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first, _ = _write_roadmap(
        tmp_path,
        "docs/plans/A-roadmap.json",
        "a-map",
        "A Roadmap",
    )
    second, _ = _write_roadmap(
        tmp_path,
        "specs/roadmaps/b.json",
        "b-map",
        "B Roadmap",
    )
    invalid = tmp_path / "docs" / "plans" / "invalid-roadmap.json"
    invalid.write_bytes(b"{")
    settings = tmp_path / "docs" / "plans" / "settings.json"
    settings.write_text('{"name":"settings"}', encoding="utf-8")
    markdown = tmp_path / "docs" / "plans" / "private-notes.md"
    markdown.write_text("# Private\n\ntoken=must-not-be-read", encoding="utf-8")
    service = PlanningMcpService(tmp_path)
    opened: list[str] = []
    original_open = service_module.os.open

    def tracking_open(path, flags):
        opened.append(_norm(path))
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", tracking_open)
    result = service.list_roadmaps()

    assert opened.count(_norm(first)) == 1
    assert opened.count(_norm(second)) == 1
    assert opened.count(_norm(invalid)) == 1
    assert opened.count(_norm(settings)) == 1
    assert _norm(markdown) not in opened
    assert [item["source_ref"] for item in result["roadmaps"]] == [
        "docs/plans/A-roadmap.json",
        "docs/plans/invalid-roadmap.json",
        "specs/roadmaps/b.json",
    ]
    assert result["summary"] == {
        "total_roadmaps": 3,
        "total_matches": 3,
        "returned": 3,
        "clipped": False,
    }
    assert result["roadmaps"][1]["valid_json"] is False


def test_list_then_atomic_replace_read_observes_fresh_bytes_and_hash(tmp_path: Path) -> None:
    path, first_raw = _write_roadmap(
        tmp_path,
        "docs/plans/fresh-roadmap.json",
        "fresh-map",
        "Before Replace",
    )
    service = PlanningMcpService(tmp_path)
    listed = service.list_roadmaps()["roadmaps"][0]
    assert listed["source_hash"] == "sha256:" + hashlib.sha256(first_raw).hexdigest()

    replacement = path.with_suffix(".replacement")
    second_raw = json.dumps(
        _roadmap("fresh-map", "After Replace"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    replacement.write_bytes(second_raw)
    os.replace(replacement, path)

    read = service.read_roadmap("docs/plans/fresh-roadmap.json")
    assert read["roadmap"]["title"] == "After Replace"
    assert read["source"]["source_hash"] == "sha256:" + hashlib.sha256(second_raw).hexdigest()
    assert read["source"]["source_hash"] != listed["source_hash"]


def test_oversize_and_unreadable_list_fallbacks_are_body_safe_and_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    oversized = tmp_path / "docs" / "plans" / "oversized-roadmap.json"
    oversized.parent.mkdir(parents=True, exist_ok=True)
    oversized.write_bytes(b"{" + (b" " * MAX_SOURCE_BYTES))
    unreadable, _ = _write_roadmap(
        tmp_path,
        "docs/plans/unreadable-roadmap.json",
        "unreadable-map",
        "Unreadable Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    original_open = service_module.os.open
    attempts: list[str] = []

    def guarded_open(path, flags):
        normalized = _norm(path)
        attempts.append(normalized)
        if normalized == _norm(unreadable):
            raise OSError("SENTINEL_PRIVATE_OPEN_FAILURE")
        if normalized == _norm(oversized):
            raise AssertionError("oversized body was opened")
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", guarded_open)
    listed = service.list_roadmaps()
    by_path = {item["source_ref"]: item for item in listed["roadmaps"]}

    assert _norm(oversized) not in attempts
    assert attempts.count(_norm(unreadable)) == 1
    for relative in (
        "docs/plans/oversized-roadmap.json",
        "docs/plans/unreadable-roadmap.json",
    ):
        assert by_path[relative]["valid_json"] is False
        assert by_path[relative]["source_hash"] == ""
        assert by_path[relative]["preview"] == ""
    assert by_path["docs/plans/oversized-roadmap.json"]["size_bytes"] == MAX_SOURCE_BYTES
    assert all("mtime" not in item for item in listed["roadmaps"])
    assert "SENTINEL_PRIVATE_OPEN_FAILURE" not in json.dumps(listed)
    assert str(tmp_path) not in json.dumps(listed)

    with pytest.raises(PlanningServiceError) as too_large:
        service.read_roadmap("docs/plans/oversized-roadmap.json")
    assert too_large.value.code == "roadmap_too_large"
    assert _norm(oversized) not in attempts

    with pytest.raises(PlanningServiceError) as too_large_by_id:
        service.read_roadmap(planning_source_id("docs/plans/oversized-roadmap.json"))
    assert too_large_by_id.value.code == "roadmap_too_large"
    assert _norm(oversized) not in attempts

    with pytest.raises(PlanningServiceError) as unavailable_by_path:
        service.read_roadmap("docs/plans/unreadable-roadmap.json")
    assert unavailable_by_path.value.code == "roadmap_unavailable"
    assert "SENTINEL_PRIVATE_OPEN_FAILURE" not in str(unavailable_by_path.value)

    with pytest.raises(PlanningServiceError) as unavailable:
        service.read_roadmap(planning_source_id("docs/plans/unreadable-roadmap.json"))
    assert unavailable.value.code == "roadmap_unavailable"
    assert "SENTINEL_PRIVATE_OPEN_FAILURE" not in str(unavailable.value)
    rendered = "".join(
        traceback.format_exception(
            type(unavailable.value),
            unavailable.value,
            unavailable.tb,
        )
    )
    assert "SENTINEL_PRIVATE_OPEN_FAILURE" not in rendered
    assert attempts.count(_norm(unreadable)) == 3


@pytest.mark.parametrize(
    "unsafe",
    [
        "docs/plans/../plans/Core-Roadmap.json",
        "docs/plans/%2e%2e/private.json",
        "C:\\private\\roadmap.json",
        "file://docs/plans/Core-Roadmap.json",
        "docs/plans/*.json",
        "docs/plans/core-roadmap.json",
    ],
)
def test_hostile_and_wrong_case_paths_are_rejected_without_body_reads(
    tmp_path: Path,
    monkeypatch,
    unsafe: str,
) -> None:
    _write_roadmap(
        tmp_path,
        "docs/plans/Core-Roadmap.json",
        "case-map",
        "Case Roadmap",
    )
    service = PlanningMcpService(tmp_path)

    def forbidden_open(*args, **kwargs):
        raise AssertionError("rejected reference reached body I/O")

    monkeypatch.setattr(service_module.os, "open", forbidden_open)
    with pytest.raises(PlanningServiceError):
        service.read_roadmap(unsafe)


def test_noncanonical_candidate_names_are_omitted_and_cannot_be_selected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    valid, _ = _write_roadmap(
        tmp_path,
        "docs/plans/valid-roadmap.json",
        "valid-map",
        "Valid Roadmap",
    )
    invalid_paths = [
        _write_roadmap(
            tmp_path,
            "docs/plans/encoded%2froadmap.json",
            "encoded-map",
            "Encoded Roadmap",
        )[0],
        _write_roadmap(
            tmp_path,
            "docs/plans/Ｆullwidth-roadmap.json",
            "fullwidth-map",
            "Fullwidth Roadmap",
        )[0],
        _write_roadmap(
            tmp_path,
            "docs/plans/pattern[1]-roadmap.json",
            "pattern-map",
            "Pattern Roadmap",
        )[0],
        _write_roadmap(
            tmp_path,
            "docs/plans/encoded%2fdir/nested-roadmap.json",
            "nested-map",
            "Nested Roadmap",
        )[0],
    ]
    service = PlanningMcpService(tmp_path)
    opened: list[str] = []
    original_open = service_module.os.open

    def tracking_open(path, flags):
        opened.append(_norm(path))
        return original_open(path, flags)

    monkeypatch.setattr(service_module.os, "open", tracking_open)
    listed = service.list_roadmaps()

    assert [item["source_ref"] for item in listed["roadmaps"]] == [
        "docs/plans/valid-roadmap.json",
    ]
    assert opened == [_norm(valid)]
    assert all(_norm(path) not in opened for path in invalid_paths)

    for path in invalid_paths:
        relative = path.relative_to(tmp_path).as_posix()
        for reference in (relative, planning_source_id(relative)):
            opened.clear()
            with pytest.raises(PlanningServiceError):
                service.read_roadmap(reference)
            assert opened == []

    opened.clear()
    by_path = service.read_roadmap("docs/plans/valid-roadmap.json")
    assert by_path["roadmap"]["roadmap_id"] == "valid-map"
    assert opened == [_norm(valid)]


def test_reparse_metadata_and_midread_size_drift_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target, _ = _write_roadmap(
        tmp_path,
        "docs/plans/identity-roadmap.json",
        "identity-map",
        "Identity Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    original_lstat = service_module.os.lstat

    class ReparseInfo:
        def __init__(self, value):
            self._value = value
            self.st_file_attributes = 0x400

        def __getattr__(self, name):
            return getattr(self._value, name)

    def reparse_lstat(path):
        value = original_lstat(path)
        return ReparseInfo(value) if _norm(path) == _norm(target) else value

    with monkeypatch.context() as scoped:
        scoped.setattr(service_module.os, "lstat", reparse_lstat)
        assert service.list_roadmaps()["roadmaps"] == []
        with pytest.raises(PlanningServiceError) as reparse:
            service.read_roadmap("docs/plans/identity-roadmap.json")
        assert reparse.value.code == "path_escape"

    def unavailable_lstat(path):
        if _norm(path) == _norm(target):
            raise OSError("SENTINEL_PRIVATE_METADATA_FAILURE")
        return original_lstat(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(service_module.os, "lstat", unavailable_lstat)
        assert service.list_roadmaps()["roadmaps"] == []
        with pytest.raises(PlanningServiceError) as metadata:
            service.read_roadmap("docs/plans/identity-roadmap.json")
        assert metadata.value.code == "roadmap_metadata_unavailable"
        assert "SENTINEL_PRIVATE_METADATA_FAILURE" not in str(metadata.value)

    original_fstat = service_module.os.fstat
    calls = 0

    class ChangedSizeInfo:
        def __init__(self, value):
            self._value = value
            self.st_size = value.st_size + 1

        def __getattr__(self, name):
            return getattr(self._value, name)

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        value = original_fstat(descriptor)
        return ChangedSizeInfo(value) if calls == 2 else value

    monkeypatch.setattr(service_module.os, "fstat", drifting_fstat)
    with pytest.raises(PlanningServiceError) as drift:
        service.read_roadmap("docs/plans/identity-roadmap.json")
    assert drift.value.code == "roadmap_changed"
    assert calls == 2


def test_reparse_ancestor_is_rejected_during_service_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_roadmap(
        tmp_path,
        "docs/plans/construction-roadmap.json",
        "construction-map",
        "Construction Roadmap",
    )
    docs = tmp_path / "docs"
    original_lstat = service_module.os.lstat
    original_scandir = service_module.os.scandir
    enumerated: list[str] = []
    opened: list[str] = []

    def reparse_lstat(path):
        value = original_lstat(path)
        if _norm(path) != _norm(docs):
            return value
        attributes = getattr(value, "st_file_attributes", 0)
        return _StatOverride(value, st_file_attributes=attributes | 0x400)

    def tracking_scandir(path):
        enumerated.append(_norm(path))
        return original_scandir(path)

    def forbidden_open(path, flags):
        opened.append(_norm(path))
        raise AssertionError("unsafe root reached body I/O")

    monkeypatch.setattr(service_module.os, "lstat", reparse_lstat)
    monkeypatch.setattr(service_module.os, "scandir", tracking_scandir)
    monkeypatch.setattr(service_module.os, "open", forbidden_open)

    with pytest.raises(PlanningServiceError) as unsafe:
        PlanningMcpService(tmp_path)

    assert unsafe.value.code == "unsafe_planning_root"
    assert _norm(tmp_path) in enumerated
    assert _norm(docs) not in enumerated
    assert opened == []


def test_allowed_root_case_alias_is_rejected_during_service_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alias = tmp_path / "Docs" / "plans"
    alias.mkdir(parents=True)
    opened: list[str] = []

    def forbidden_open(path, flags):
        opened.append(_norm(path))
        raise AssertionError("case-aliased root reached body I/O")

    monkeypatch.setattr(service_module.os, "open", forbidden_open)
    with pytest.raises(PlanningServiceError) as unsafe:
        PlanningMcpService(tmp_path)

    assert unsafe.value.code == "unsafe_planning_root"
    assert opened == []


@pytest.mark.parametrize(
    ("relative", "swapped_component", "swap_kind"),
    [
        ("docs/plans/docs-ancestor-roadmap.json", "docs", "identity"),
        ("specs/roadmaps/specs-ancestor-roadmap.json", "specs", "identity"),
        ("docs/plans/docs-root-roadmap.json", "docs/plans", "reparse"),
        ("specs/roadmaps/specs-root-roadmap.json", "specs/roadmaps", "identity"),
    ],
)
def test_post_construction_ancestor_and_allowed_root_swaps_reject_without_body_reads(
    tmp_path: Path,
    monkeypatch,
    relative: str,
    swapped_component: str,
    swap_kind: str,
) -> None:
    target, _ = _write_roadmap(
        tmp_path,
        relative,
        "swapped-map",
        "Swapped Roadmap",
    )
    service = PlanningMcpService(tmp_path)
    swapped = tmp_path.joinpath(*swapped_component.split("/"))
    original_lstat = service_module.os.lstat
    opened: list[str] = []

    def swapped_lstat(path):
        value = original_lstat(path)
        if _norm(path) != _norm(swapped):
            return value
        if swap_kind == "reparse":
            attributes = getattr(value, "st_file_attributes", 0)
            return _StatOverride(value, st_file_attributes=attributes | 0x400)
        return _StatOverride(value, st_ino=value.st_ino + 1)

    def forbidden_open(path, flags):
        opened.append(_norm(path))
        raise AssertionError("swapped root reached body I/O")

    monkeypatch.setattr(service_module.os, "lstat", swapped_lstat)
    monkeypatch.setattr(service_module.os, "open", forbidden_open)

    assert service.list_roadmaps()["roadmaps"] == []
    with pytest.raises(PlanningServiceError) as by_path:
        service.read_roadmap(relative)
    assert by_path.value.code == "path_escape"
    with pytest.raises(PlanningServiceError) as by_id:
        service.read_roadmap(planning_source_id(relative))
    assert by_id.value.code == "roadmap_not_found"
    assert _norm(target) not in opened
    assert opened == []
