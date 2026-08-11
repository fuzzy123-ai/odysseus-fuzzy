from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from types import MappingProxyType

import pytest

import src.memory_owner_eligibility as eligibility_module
from src.memory_owner_eligibility import (
    MEMORY_ELIGIBILITY_SCHEMA,
    MemoryOwnerEligibilityError,
    capture_memory_owner_eligibility_snapshot,
)


POLICY_REF = "sha256:" + "a" * 64
REVIEW_REF = "sha256:" + "b" * 64


def _stamp(**overrides):
    value = {
        "schema": MEMORY_ELIGIBILITY_SCHEMA,
        "source_status": "active",
        "acceptance_status": "accepted",
        "incognito": False,
        "policy_status": "go",
        "policy_evidence_ref": POLICY_REF,
        "review_status": "accepted",
        "review_evidence_ref": REVIEW_REF,
    }
    value.update(overrides)
    return value


def _record(*, memory_id="memory-1", owner="alice", text="private marker", stamp=None, **extra):
    value = {
        "id": memory_id,
        "owner": owner,
        "text": text,
        "timestamp": 1_700_000_000,
        "source": "user",
        "category": "fact",
        "uses": 0,
        "metadata": {"memory_eligibility": _stamp() if stamp is None else stamp},
    }
    value.update(extra)
    return value


def _write(tmp_path: Path, rows, *, raw: str | None = None) -> Path:
    path = tmp_path / "memory.json"
    path.write_text(raw if raw is not None else json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _capture(tmp_path: Path, rows, *, owner="alice", **kwargs):
    return capture_memory_owner_eligibility_snapshot(
        _write(tmp_path, rows), owner=owner, **kwargs
    )


@pytest.mark.parametrize("review_status", ["accepted", "not_required"])
def test_explicit_eligible_record_is_detached_and_content_free_in_evidence(tmp_path, review_status):
    private_text = "private-memory-body-marker"
    rows = [_record(text=private_text, stamp=_stamp(review_status=review_status))]
    snapshot = _capture(tmp_path, rows)

    assert snapshot.eligible_count == 1
    assert snapshot.ineligible_count == 0
    record = snapshot.eligible_records[0]
    assert record.memory_id == "memory-1"
    assert record.owner == "alice"
    assert record.text == private_text
    assert isinstance(record.record, MappingProxyType)
    assert isinstance(record.record["metadata"], MappingProxyType)

    evidence = snapshot.to_evidence_dict()
    encoded = json.dumps(evidence, sort_keys=True)
    assert private_text not in encoded
    assert "alice" not in encoded
    assert str(tmp_path) not in encoded
    assert evidence["eligible_count"] == 1
    assert evidence["raw_content_visible"] is False
    assert evidence["source_path_visible"] is False
    assert repr(snapshot).find(private_text) == -1
    assert repr(record).find(private_text) == -1

    rows[0]["text"] = "changed input"
    rows[0]["metadata"]["memory_eligibility"]["source_status"] = "deleted"
    assert record.text == private_text
    assert record.source_status == "active"
    with pytest.raises(TypeError):
        record.record["text"] = "mutation"
    with pytest.raises(TypeError):
        record.record["metadata"]["memory_eligibility"]["source_status"] = "deleted"
    with pytest.raises(FrozenInstanceError):
        record.source_status = "deleted"


def test_exact_owner_isolation_does_not_normalize_case_whitespace_or_unicode(tmp_path):
    composed = "caf\u00e9"
    decomposed = "cafe\u0301"
    rows = [
        _record(memory_id="a", owner="Alice"),
        _record(memory_id="b", owner="alice "),
        _record(memory_id="c", owner=composed),
        _record(memory_id="d", owner=decomposed),
    ]
    snapshot = _capture(tmp_path, rows, owner=composed)
    assert [item.memory_id for item in snapshot.eligible_records] == ["c"]
    assert dict(snapshot.rejection_counts)["owner_mismatch"] == 3
    assert snapshot.contains_exact_owner(composed) is True
    assert snapshot.contains_exact_owner(decomposed) is False


def test_multiline_memory_text_remains_an_eligible_detached_body(tmp_path):
    text = "line one\nline two\twith a tab\r\nline three"
    snapshot = _capture(tmp_path, [_record(text=text)])
    assert snapshot.eligible_count == 1
    assert snapshot.eligible_records[0].text == text


@pytest.mark.parametrize(
    ("stamp_changes", "reason"),
    [
        ({"source_status": "deleted"}, "inactive"),
        ({"acceptance_status": "rejected"}, "not_accepted"),
        ({"incognito": True}, "incognito"),
        ({"policy_status": "review"}, "policy_not_go"),
        ({"policy_status": "blocked"}, "policy_not_go"),
        ({"review_status": "pending"}, "review_not_accepted"),
        ({"review_status": "rejected"}, "review_not_accepted"),
    ],
)
def test_explicit_ineligible_states_fail_closed(tmp_path, stamp_changes, reason):
    snapshot = _capture(tmp_path, [_record(stamp=_stamp(**stamp_changes))])
    assert snapshot.eligible_records == ()
    assert dict(snapshot.rejection_counts)[reason] == 1


def test_legacy_missing_unknown_coerced_and_extra_stamp_fields_fail_closed(tmp_path):
    base = _stamp()
    unknown = _stamp(policy_status="allow")
    coerced = _stamp(incognito="false")
    extra = _stamp()
    extra["extra"] = True
    missing = _stamp()
    missing.pop("review_status")
    rows = [
        _record(memory_id="legacy", stamp=None),
        _record(memory_id="unknown", stamp=unknown),
        _record(memory_id="coerced", stamp=coerced),
        _record(memory_id="extra", stamp=extra),
        _record(memory_id="missing", stamp=missing),
    ]
    rows[0]["metadata"] = {}
    snapshot = _capture(tmp_path, rows)
    counts = dict(snapshot.rejection_counts)
    assert snapshot.eligible_count == 0
    assert counts["legacy_or_unstamped"] == 1
    assert counts["invalid_stamp"] == 4
    assert base["schema"] == MEMORY_ELIGIBILITY_SCHEMA


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("record", "source_status", "deleted"),
        ("metadata", "acceptance_status", "rejected"),
        ("record", "incognito", True),
        ("metadata", "policy_status", "blocked"),
        ("record", "review_status", "pending"),
        ("record", "accepted", False),
        ("metadata", "deleted", True),
        ("record", "policy_blocked", True),
        ("metadata", "review_required", True),
    ],
)
def test_contradictory_aliases_fail_closed(tmp_path, location, field, value):
    row = _record()
    target = row if location == "record" else row["metadata"]
    target[field] = value
    snapshot = _capture(tmp_path, [row])
    assert snapshot.eligible_count == 0
    assert dict(snapshot.rejection_counts)["contradictory_state"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"owner": ""},
        {"text": ""},
        {"timestamp": True},
        {"timestamp": -1},
        {"source": ""},
        {"category": ""},
        {"session_id": 7},
    ],
)
def test_invalid_core_records_fail_closed(tmp_path, change):
    snapshot = _capture(tmp_path, [_record(**change)])
    assert snapshot.eligible_count == 0
    assert dict(snapshot.rejection_counts)["invalid_record"] == 1


def test_duplicate_record_id_is_source_failure_even_across_owners(tmp_path):
    path = _write(tmp_path, [_record(memory_id="same"), _record(memory_id="same", owner="bob")])
    with pytest.raises(MemoryOwnerEligibilityError, match="^duplicate_record_id$") as raised:
        capture_memory_owner_eligibility_snapshot(path, owner="alice")
    assert raised.value.code == "duplicate_record_id"


def test_duplicate_json_key_nonfinite_and_overflow_float_are_rejected(tmp_path):
    duplicate = _write(tmp_path, [], raw='[{"id":"a","id":"b"}]')
    with pytest.raises(MemoryOwnerEligibilityError, match="^duplicate_json_key$"):
        capture_memory_owner_eligibility_snapshot(duplicate, owner="alice")

    for index, token in enumerate(("NaN", "Infinity", "-Infinity", "1e999")):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = _write(directory, [], raw=f"[{token}]")
        with pytest.raises(MemoryOwnerEligibilityError, match="^nonfinite_json_number$"):
            capture_memory_owner_eligibility_snapshot(path, owner="alice")


@pytest.mark.parametrize("raw", ["{", "{}", '"rows"', "null"])
def test_malformed_or_non_list_sources_fail_closed(tmp_path, raw):
    path = _write(tmp_path, [], raw=raw)
    expected = "invalid_json" if raw == "{" else "source_not_record_list"
    with pytest.raises(MemoryOwnerEligibilityError, match=f"^{expected}$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")


def test_missing_file_is_not_created_and_wrong_name_is_rejected(tmp_path):
    missing = tmp_path / "memory.json"
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_missing$"):
        capture_memory_owner_eligibility_snapshot(missing, owner="alice")
    assert not missing.exists()

    wrong = tmp_path / "other.json"
    wrong.write_text("[]", encoding="utf-8")
    with pytest.raises(MemoryOwnerEligibilityError, match="^invalid_capture_request$"):
        capture_memory_owner_eligibility_snapshot(wrong, owner="alice")


def test_bounds_for_bytes_records_depth_and_nodes(tmp_path):
    oversized = _write(tmp_path, [_record(text="x" * 500)])
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_too_large$"):
        capture_memory_owner_eligibility_snapshot(oversized, owner="alice", max_source_bytes=64)

    rows_path = _write(tmp_path, [_record(memory_id="a"), _record(memory_id="b")])
    with pytest.raises(MemoryOwnerEligibilityError, match="^too_many_records$"):
        capture_memory_owner_eligibility_snapshot(rows_path, owner="alice", max_records=1)

    deep = {"leaf": True}
    for _ in range(8):
        deep = {"nested": deep}
    deep_path = _write(tmp_path, [deep])
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_too_deep$"):
        capture_memory_owner_eligibility_snapshot(deep_path, owner="alice", max_depth=4)

    complex_path = _write(tmp_path, [[1, 2, 3, 4]])
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_too_complex$"):
        capture_memory_owner_eligibility_snapshot(complex_path, owner="alice", max_json_nodes=4)


def test_real_symlink_is_rejected_when_platform_allows(tmp_path):
    target_dir = tmp_path / "target"
    link_dir = tmp_path / "link"
    target_dir.mkdir()
    link_dir.mkdir()
    target = _write(target_dir, [_record()])
    link = link_dir / "memory.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_symlinked$"):
        capture_memory_owner_eligibility_snapshot(link, owner="alice")


def test_symlink_mode_is_always_covered_without_platform_privilege(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])
    real_lstat = eligibility_module.os.lstat

    def symlinked_lstat(value):
        result = real_lstat(value)
        if Path(value) == path:
            values = list(result)
            values[0] = stat.S_IFLNK | 0o777
            return os.stat_result(values)
        return result

    monkeypatch.setattr(eligibility_module.os, "lstat", symlinked_lstat)
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_symlinked$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")


def test_symlinked_parent_is_rejected(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])
    real_lstat = eligibility_module.os.lstat

    def parent_symlink_lstat(value):
        result = real_lstat(value)
        if Path(value) == tmp_path:
            values = list(result)
            values[0] = stat.S_IFLNK | 0o777
            return os.stat_result(values)
        return result

    monkeypatch.setattr(eligibility_module.os, "lstat", parent_symlink_lstat)
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_symlinked$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")


def test_windows_reparse_like_parent_is_rejected_before_leaf_read(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])
    real_lstat = eligibility_module.os.lstat
    leaf_observed = False

    def reparse_parent_lstat(value):
        nonlocal leaf_observed
        result = real_lstat(value)
        if Path(value) == path:
            leaf_observed = True
        if Path(value) == tmp_path:
            fields = {
                name: getattr(result, name)
                for name in (
                    "st_mode",
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            }
            fields["st_file_attributes"] = 0x400
            fields["st_reparse_tag"] = 0xA0000003
            return SimpleNamespace(**fields)
        return result

    monkeypatch.setattr(eligibility_module.os, "lstat", reparse_parent_lstat)
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_symlinked$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")
    assert leaf_observed is False


def test_real_windows_junction_parent_is_rejected_and_cleaned(tmp_path):
    if os.name != "nt":
        return
    target = tmp_path / "junction-target"
    junction = tmp_path / "junction-parent"
    target.mkdir()
    _write(target, [_record()])
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail("windows_junction_fixture_unavailable")
    try:
        with pytest.raises(MemoryOwnerEligibilityError, match="^source_symlinked$"):
            capture_memory_owner_eligibility_snapshot(
                junction / "memory.json",
                owner="alice",
            )
        assert (target / "memory.json").is_file()
    finally:
        os.rmdir(junction)
    assert not junction.exists()
    assert (target / "memory.json").is_file()


def test_parent_replacement_observation_fails_closed(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])
    real_lstat = eligibility_module.os.lstat
    target_calls = 0

    def drifting_parent_lstat(value):
        nonlocal target_calls
        result = real_lstat(value)
        if Path(value) == tmp_path:
            target_calls += 1
            if target_calls == 2:
                values = list(result)
                values[1] = int(result.st_ino) + 1
                return os.stat_result(values)
        return result

    monkeypatch.setattr(eligibility_module.os, "lstat", drifting_parent_lstat)
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_replaced$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")


def test_file_replacement_observation_fails_closed(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])
    real_lstat = eligibility_module.os.lstat
    calls = 0

    def drifting_lstat(value):
        nonlocal calls
        calls += 1
        result = real_lstat(value)
        if calls == 2:
            values = list(result)
            values[1] = int(result.st_ino) + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(eligibility_module.os, "lstat", drifting_lstat)
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_replaced$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")


def test_captured_bytes_remain_authoritative_after_file_replacement(tmp_path):
    path = _write(tmp_path, [_record(text="first private body")])
    snapshot = capture_memory_owner_eligibility_snapshot(path, owner="alice")
    path.write_text(json.dumps([_record(text="second private body")]), encoding="utf-8")
    assert snapshot.eligible_records[0].text == "first private body"


def test_stable_digests_and_deterministic_record_order(tmp_path):
    rows = [_record(memory_id="z"), _record(memory_id="a")]
    first = _capture(tmp_path, rows)
    second = capture_memory_owner_eligibility_snapshot(tmp_path / "memory.json", owner="alice")
    assert first.source_digest == second.source_digest
    assert first.snapshot_digest == second.snapshot_digest
    assert [item.memory_id for item in first.eligible_records] == ["a", "z"]
    assert [item.record_digest for item in first.eligible_records] == [
        item.record_digest for item in second.eligible_records
    ]


def test_error_repr_and_text_are_bounded_and_content_free(tmp_path):
    private_owner = "private-owner-marker"
    private_path_marker = str(tmp_path)
    path = _write(tmp_path, [], raw="PRIVATE RAW BODY {")
    with pytest.raises(MemoryOwnerEligibilityError) as raised:
        capture_memory_owner_eligibility_snapshot(path, owner=private_owner)
    rendered = str(raised.value) + repr(raised.value)
    assert private_owner not in rendered
    assert private_path_marker not in rendered
    assert "PRIVATE RAW BODY" not in rendered
    assert rendered.count("invalid_json") == 2


def test_unexpected_boundary_exception_is_reserialized_without_raw_detail(monkeypatch, tmp_path):
    path = _write(tmp_path, [_record()])

    def hostile_lstat(_value):
        raise AssertionError("private owner path and body marker")

    monkeypatch.setattr(eligibility_module.os, "lstat", hostile_lstat)
    with pytest.raises(
        MemoryOwnerEligibilityError,
        match="^memory_eligibility_capture_failed$",
    ) as raised:
        capture_memory_owner_eligibility_snapshot(path, owner="private-owner")
    assert "private" not in (str(raised.value) + repr(raised.value))


def test_invalid_utf8_and_invalid_evidence_refs_fail_closed(tmp_path):
    path = tmp_path / "memory.json"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(MemoryOwnerEligibilityError, match="^source_not_utf8$"):
        capture_memory_owner_eligibility_snapshot(path, owner="alice")

    rows = [
        _record(memory_id="policy", stamp=_stamp(policy_evidence_ref="policy:private")),
        _record(memory_id="review", stamp=_stamp(review_evidence_ref="sha256:" + "A" * 64)),
    ]
    snapshot = _capture(tmp_path, rows)
    assert snapshot.eligible_count == 0
    assert dict(snapshot.rejection_counts)["invalid_stamp"] == 2


def test_matching_redundant_aliases_do_not_create_false_conflicts(tmp_path):
    row = _record(
        source_status="active",
        acceptance_status="accepted",
        incognito=False,
        policy_status="go",
        review_status="accepted",
        accepted=True,
        deleted=False,
        policy_blocked=False,
        review_required=False,
    )
    snapshot = _capture(tmp_path, [row])
    assert snapshot.eligible_count == 1


@pytest.mark.parametrize(
    "memory_status",
    ["deleted", "rejected", "inactive", "blocked", "pending", "review", "unknown", 7],
)
@pytest.mark.parametrize("location", ["record", "metadata"])
def test_legacy_memory_status_cannot_contradict_eligible_v1_stamp(
    tmp_path,
    memory_status,
    location,
):
    row = _record()
    target = row if location == "record" else row["metadata"]
    target["memory_status"] = memory_status
    snapshot = _capture(tmp_path, [row])
    assert snapshot.eligible_count == 0
    assert dict(snapshot.rejection_counts)["contradictory_state"] == 1


@pytest.mark.parametrize(
    "memory_status",
    [
        "accepted",
        "active",
        "approved",
        "available",
        "current",
        "current_source_of_truth",
        "supporting_plan_source",
    ],
)
def test_known_positive_or_role_memory_status_is_compatible(tmp_path, memory_status):
    row = _record()
    row["metadata"]["memory_status"] = memory_status
    snapshot = _capture(tmp_path, [row])
    assert snapshot.eligible_count == 1


@pytest.mark.parametrize(
    "ambiguous_key",
    ["analysis_policy", "policy", "policy_review", "review", "review_decision"],
)
def test_parallel_nested_policy_or_review_authority_is_contradictory(tmp_path, ambiguous_key):
    row = _record()
    row["metadata"][ambiguous_key] = {"status": "blocked"}
    snapshot = _capture(tmp_path, [row])
    assert snapshot.eligible_count == 0
    assert dict(snapshot.rejection_counts)["contradictory_state"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner": ""},
        {"owner": 7},
        {"owner": "bad\nowner"},
        {"owner": "alice", "max_source_bytes": True},
        {"owner": "alice", "max_records": 0},
        {"owner": "alice", "max_depth": 2},
        {"owner": "alice", "max_json_nodes": 0},
    ],
)
def test_invalid_capture_requests_are_bounded(tmp_path, kwargs):
    path = _write(tmp_path, [])
    with pytest.raises(MemoryOwnerEligibilityError, match="^invalid_capture_request$"):
        capture_memory_owner_eligibility_snapshot(path, **kwargs)


def test_static_contract_has_no_forbidden_runtime_imports_or_calls():
    source_path = Path(eligibility_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = set()
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    forbidden_imports = {
        "src.memory",
        "src.memory_provider",
        "src.memory_vector",
        "requests",
        "httpx",
        "urllib",
        "socket",
    }
    forbidden_calls = {
        "MemoryManager",
        "NativeMemoryProvider",
        "save",
        "recall",
        "remember",
        "migrate",
        "replace",
        "write_text",
        "write_bytes",
        "unlink",
    }
    assert imported.isdisjoint(forbidden_imports)
    assert calls.isdisjoint(forbidden_calls)


def test_error_constructor_does_not_dispatch_to_foreign_string_subclasses():
    class HostileCode(str):
        def __hash__(self):
            raise AssertionError("private hash detail")

        def __eq__(self, _other):
            raise AssertionError("private equality detail")

        def __str__(self):
            raise AssertionError("private string detail")

        def __repr__(self):
            raise AssertionError("private repr detail")

    error = MemoryOwnerEligibilityError(HostileCode("source_missing"))
    assert error.code == "memory_eligibility_capture_failed"
    assert str(error) == "memory_eligibility_capture_failed"
    assert repr(error) == (
        "MemoryOwnerEligibilityError(code='memory_eligibility_capture_failed')"
    )


def test_error_constructor_rejects_non_string_without_foreign_dispatch():
    class HostileCode:
        def __hash__(self):
            raise AssertionError("private hash detail")

        def __eq__(self, _other):
            raise AssertionError("private equality detail")

        def __str__(self):
            raise AssertionError("private string detail")

        def __repr__(self):
            raise AssertionError("private repr detail")

    error = MemoryOwnerEligibilityError(HostileCode())
    assert error.code == "memory_eligibility_capture_failed"
