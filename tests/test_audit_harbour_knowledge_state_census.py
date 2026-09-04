from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_harbour_knowledge_state_census.py"
SPEC = importlib.util.spec_from_file_location("h1r04_census", SCRIPT)
assert SPEC and SPEC.loader
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


def architecture():
    return census.load_effective_architecture(ROOT)


def without_control_plane(arch):
    arch = copy.deepcopy(arch)
    arch["census_contract"]["control_plane_record_boundary"]["control_plane_records_exact"] = []
    arch["manifest_contract"]["control_plane_exclusions_exact"] = []
    return arch
def without_semantic_pins(arch):
    arch = copy.deepcopy(arch)
    arch["semantic_positive_ground_truth"] = []
    arch["census_contract"]["semantic_python_discovery_boundary"][
        "semantic_anchors"
    ]["entrypoints_exact"] = []
    return arch




def discovered_seeds(arch=None):
    arch = arch or architecture()
    return sorted(
        [{"path": item["path"], "raw_blob": item["raw_blob"]} for item in arch["current_inventory_seed"]],
        key=lambda item: item["path"].encode("utf-8"),
    )


def semantic_candidates(files, seed_paths=()):
    boundary = copy.deepcopy(
        architecture()["census_contract"]["semantic_python_discovery_boundary"]
    )
    boundary["semantic_anchors"]["entrypoints_exact"] = []
    paths = sorted(files, key=lambda value: value.encode("utf-8"))
    sources = {
        path: source.encode("utf-8")
        for path, source in files.items()
    }
    return census._semantic_candidates(paths, sources, boundary, set(seed_paths))


def valid_manifest(arch=None, extra=None):
    arch = arch or architecture()
    mappings = {item["path"]: item for item in arch["manifest_contract"]["seed_disposition_mapping"]}
    entries = []
    for item in discovered_seeds(arch):
        entries.append({**item, "classification": "state_bearing", **mappings[item["path"]]})
    for item in extra or []:
        entries.append({
            **item,
            "classification": "unclassified",
            "owner": "unclassified",
            "writer_authority": "unclassified_no_writer",
            "role": "unclassified_candidate",
            "disposition": "requires_separate_reviewed_classification",
            "reason": "Requires a separate reviewed disposition decision.",
        })
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    contract = arch["manifest_contract"]
    return {
        "schema_version": 2,
        "kind": "harbour_one.h1r04_state_census_manifest",
        "mode": "discovery_snapshot",
        "scan_contract_digest": census.sha256_json(arch["census_contract"], sort_keys=True),
        "infrastructure_exclusions": list(contract["infrastructure_exclusions_exact"]),
        "control_plane_exclusions": list(contract["control_plane_exclusions_exact"]),
        "entries": entries,
    }


def assert_failure(code, func, *args, **kwargs):
    with pytest.raises(census.AuditFailure) as caught:
        func(*args, **kwargs)
    assert caught.value.codes == [code]


def stat_proxy(info, **overrides):
    values = {
        "st_mode": info.st_mode,
        "st_dev": info.st_dev,
        "st_ino": info.st_ino,
        "st_size": info.st_size,
        "st_mtime_ns": info.st_mtime_ns,
        "st_file_attributes": getattr(info, "st_file_attributes", 0),
        "st_ctime_ns": getattr(info, "st_ctime_ns", 0),
    }
    values.update(overrides)
    return type("StatProxy", (), values)()


def invoke_main(monkeypatch, capsysbinary, argv, manifest, found, files=99):
    base_raw = (ROOT / census.BASE_ARCHITECTURE).read_bytes()
    correction_raw = (ROOT / census.CORRECTION_ARCHITECTURE).read_bytes()
    portable_raw = (ROOT / census.PORTABLE_ARCHITECTURE).read_bytes()
    ast_raws = {
        relative: (ROOT / relative).read_bytes()
        for relative in (
            census.AST_ARCHITECTURE_R1, census.AST_ARCHITECTURE_R2,
            census.AST_ARCHITECTURE_R3, census.AST_ARCHITECTURE_R4,
        )
    }
    manifest_raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    def reader(_root, relative, _limit):
        if relative == census.BASE_ARCHITECTURE:
            return base_raw
        if relative == census.CORRECTION_ARCHITECTURE:
            return correction_raw
        if relative == census.PORTABLE_ARCHITECTURE:
            return portable_raw
        if relative in ast_raws:
            return ast_raws[relative]
        return manifest_raw
    monkeypatch.setattr(census, "read_safe_file", reader)
    monkeypatch.setattr(census, "discover", lambda _root, _architecture: (files, found))
    rc = census.main(argv)
    captured = capsysbinary.readouterr()
    return rc, json.loads(captured.out), captured.out, captured.err


def test_census_rejects_new_changed_removed_and_duplicate_candidates():
    arch = architecture()
    found = discovered_seeds(arch)
    manifest = valid_manifest(arch)
    census.validate_manifest(manifest, arch, found)
    assert_failure("manifest_mismatch", census.validate_manifest, manifest, arch, found + [{"path": "src/new_state.py", "raw_blob": "0" * 40}])
    stale = copy.deepcopy(manifest)
    stale["entries"].append(copy.deepcopy(stale["entries"][-1]))
    assert_failure("manifest_mismatch", census.validate_manifest, stale, arch, found)
    assert_failure("manifest_mismatch", census.validate_manifest, manifest, arch, found[:-1])
    misordered = copy.deepcopy(manifest)
    misordered["entries"][0], misordered["entries"][1] = misordered["entries"][1], misordered["entries"][0]
    assert_failure("manifest_mismatch", census.validate_manifest, misordered, arch, found)
    changed = copy.deepcopy(found)
    changed[0] = {**changed[0], "raw_blob": "0" * 40}
    assert_failure("identity_mismatch", census.validate_manifest, manifest, arch, changed)


def test_census_rejects_unclassified_without_explicit_discovery_mode(monkeypatch, capsysbinary):
    arch = architecture()
    extra = {"path": "src/new_state.py", "raw_blob": "0" * 40}
    found = sorted(discovered_seeds(arch) + [extra], key=lambda item: item["path"].encode())
    rc, report, raw, err = invoke_main(
        monkeypatch, capsysbinary,
        ["--manifest", "manifest.json", "--format", "json"],
        valid_manifest(arch, [extra]), found,
    )
    assert rc == 2 and err == b""
    assert report == census.validation_report(
        status="unclassified", files=99, candidates=18, classified=17,
        unclassified=1, digest=census.member_digest(found),
        errors=["unclassified_candidates"],
    )
    assert raw == census.canonical_bytes(report, sort_keys=True) + b"\n"


def test_census_rejects_casefold_unicode_collision(tmp_path):
    contract = copy.deepcopy(architecture()["census_contract"])
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    (tmp_path / "X").mkdir()
    def runner(_root, argv):
        if argv == contract["git_tracked_argv"]:
            return "X/Å.py\0X/Å.py\0".encode("utf-8")
        return b""
    assert_failure("path_collision", census.inventory_paths, tmp_path, contract, runner=runner)


def test_census_rejects_symlink_reparse_junction_and_outside_paths(tmp_path, monkeypatch):
    assert_failure("path_invalid", census._normalized_posix, ".", 4096)
    assert_failure("path_invalid", census._normalized_posix, "../escape.py", 4096)
    assert_failure("path_invalid", census._normalized_posix, "C:/escape.py", 4096)
    directory = tmp_path / "directory"
    directory.mkdir()
    assert_failure("not_regular", census.read_safe_file, tmp_path, "directory", 100)
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "state.py"
    target.write_text("x", encoding="utf-8")
    original_lstat = census.os.lstat

    with monkeypatch.context() as patcher:
        def final_symlink(path):
            info = original_lstat(path)
            return stat_proxy(info, st_mode=stat.S_IFLNK | 0o777) if Path(path) == target else info
        patcher.setattr(census.os, "lstat", final_symlink)
        assert_failure("reparse_or_symlink", census.read_safe_file, tmp_path, "parent/state.py", 100)

    with monkeypatch.context() as patcher:
        def intermediate_reparse(path):
            info = original_lstat(path)
            return stat_proxy(info, st_file_attributes=0x400) if Path(path) == parent else info
        patcher.setattr(census.os, "lstat", intermediate_reparse)
        assert_failure("reparse_or_symlink", census.read_safe_file, tmp_path, "parent/state.py", 100)

    original_resolve = Path.resolve
    outside = tmp_path.parent / "outside.py"
    with monkeypatch.context() as patcher:
        def outside_resolve(self, strict=False):
            if self == tmp_path:
                return tmp_path
            if self == target:
                return outside
            return original_resolve(self, strict=strict)
        patcher.setattr(Path, "resolve", outside_resolve)
        assert_failure("outside_repository", census.read_safe_file, tmp_path, "parent/state.py", 100)


def test_census_rejects_unreadable_non_utf8_binary_and_limit_failures(tmp_path):
    assert_failure("binary_candidate", census._decode_candidate, b"a\x00b")
    assert_failure("non_utf8", census._decode_candidate, b"\xff")
    path = tmp_path / "state.py"
    path.write_bytes(b"12")
    assert census.read_safe_file(tmp_path, "state.py", 2) == b"12"
    assert_failure("limit_exceeded", census.read_safe_file, tmp_path, "state.py", 1)
    assert_failure("unreadable", census.read_safe_file, tmp_path, "missing.py", 10)
    assert census._normalized_posix("é", 2) == "é"
    assert_failure("limit_exceeded", census._normalized_posix, "é", 1)

    arch = without_semantic_pins(without_control_plane(architecture()))
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    contract["limits"]["max_files"] = 1
    def too_many(_root, argv):
        return b"a.py\0b.py\0" if argv == contract["git_tracked_argv"] else b""
    assert_failure("limit_exceeded", census.inventory_paths, tmp_path, contract, runner=too_many)

    (tmp_path / "a.py").write_bytes(b"json.dump")
    (tmp_path / "b.py").write_bytes(b"x")
    contract["limits"]["max_files"] = 2
    contract["limits"]["max_total_text_bytes"] = 9
    def exact_total(_root, argv):
        return b"a.py\0" if argv == contract["git_tracked_argv"] else b""
    assert census.discover(tmp_path, arch, runner=exact_total)[0] == 1
    def over_total(_root, argv):
        return b"a.py\0b.py\0" if argv == contract["git_tracked_argv"] else b""
    assert_failure("limit_exceeded", census.discover, tmp_path, arch, runner=over_total)


def test_census_uses_raw_blob_identity_for_crlf_bytes():
    data = b"a\r\nb\r\n"
    expected = hashlib.sha1(b"blob 6\x00" + data).hexdigest()
    assert census.raw_blob(data) == expected
    assert census.raw_blob(data) != census.raw_blob(data.replace(b"\r\n", b"\n"))


def test_census_rejects_toctou_replacement_via_injected_reader(tmp_path, monkeypatch):
    path = tmp_path / "state.py"
    path.write_text("safe", encoding="utf-8")
    real = census._component_snapshot
    first = real(tmp_path, "state.py")
    calls = 0
    def snapshots(root, relative):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        changed = list(first)
        final_path, identity = changed[-1]
        identity = (*identity[:-2], identity[-2] + 1, identity[-1])
        changed[-1] = (final_path, identity)
        return changed
    monkeypatch.setattr(census, "_component_snapshot", snapshots)
    assert_failure("toctou_changed", census.read_safe_file, tmp_path, "state.py", 100)


def test_census_rejects_preopen_fstat_mismatch_and_opens_once(tmp_path, monkeypatch):
    path = tmp_path / "state.py"
    path.write_bytes(b"safe")
    opens = []
    reads = []
    def opening(target, flags):
        opens.append((Path(target), flags))
        return os.open(target, flags)
    def reading(fd, size):
        reads.append(fd)
        return os.read(fd, size)
    assert census.read_safe_file(tmp_path, "state.py", 10, open_file=opening, read_chunk=reading) == b"safe"
    assert len(opens) == 1 and len(set(reads)) == 1
    required_flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOINHERIT", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    assert opens[0][1] == required_flags
    assert opens[0][1] & os.O_WRONLY == 0 and opens[0][1] & os.O_RDWR == 0

    real_fstat = census.os.fstat
    with monkeypatch.context() as patcher:
        calls = 0
        def initial_mismatch(fd):
            nonlocal calls
            calls += 1
            info = real_fstat(fd)
            return stat_proxy(info, st_size=info.st_size + 1) if calls == 1 else info
        patcher.setattr(census.os, "fstat", initial_mismatch)
        assert_failure("toctou_changed", census.read_safe_file, tmp_path, "state.py", 10)
    with monkeypatch.context() as patcher:
        calls = 0
        def post_read_mismatch(fd):
            nonlocal calls
            calls += 1
            info = real_fstat(fd)
            return stat_proxy(info, st_mtime_ns=info.st_mtime_ns + 1) if calls == 2 else info
        patcher.setattr(census.os, "fstat", post_read_mismatch)
        assert_failure("toctou_changed", census.read_safe_file, tmp_path, "state.py", 10)
    with monkeypatch.context() as patcher:
        calls = 0
        def post_read_ctime_mismatch(fd):
            nonlocal calls
            calls += 1
            info = real_fstat(fd)
            return stat_proxy(info, st_ctime_ns=info.st_ctime_ns + 1) if calls == 2 else info
        patcher.setattr(census.os, "fstat", post_read_ctime_mismatch)
        assert_failure("toctou_changed", census.read_safe_file, tmp_path, "state.py", 10)


def test_census_uses_portable_cross_view_ctime_rule(tmp_path, monkeypatch):
    path = tmp_path / "state.py"
    path.write_bytes(b"safe")
    path_snapshot = census._component_snapshot(tmp_path, "state.py")[-1][1]
    descriptor = os.stat(path)

    with monkeypatch.context() as patcher:
        patcher.setattr(census.os, "name", "nt")
        divergent = stat_proxy(descriptor, st_ctime_ns=path_snapshot[-1] + 1)
        assert census._same_initial_file(path_snapshot, divergent)

    with monkeypatch.context() as patcher:
        patcher.setattr(census.os, "name", "posix")
        divergent = stat_proxy(descriptor, st_ctime_ns=path_snapshot[-1] + 1)
        assert not census._same_initial_file(path_snapshot, divergent)

    for field, changed in (
        ("st_mode", descriptor.st_mode ^ stat.S_IWUSR),
        ("st_size", descriptor.st_size + 1),
        ("st_mtime_ns", descriptor.st_mtime_ns + 1),
    ):
        with monkeypatch.context() as patcher:
            patcher.setattr(census.os, "name", "nt")
            assert not census._same_initial_file(path_snapshot, stat_proxy(descriptor, **{field: changed}))


@pytest.mark.parametrize(("component_index", "identity_index"), [(-2, 1), (-1, 0), (-1, 2), (-1, 5), (-1, 6)])
def test_census_rejects_parent_and_identity_dimension_postclose_drifts(
    tmp_path, monkeypatch, component_index, identity_index
):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "state.py").write_bytes(b"safe")
    real = census._component_snapshot
    first = real(tmp_path, "parent/state.py")
    calls = 0
    def snapshots(root, relative):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        changed = list(first)
        path, identity = changed[component_index]
        identity = list(identity)
        identity[identity_index] += 1
        changed[component_index] = (path, tuple(identity))
        return changed
    monkeypatch.setattr(census, "_component_snapshot", snapshots)
    assert_failure("toctou_changed", census.read_safe_file, tmp_path, "parent/state.py", 10)


def test_census_excludes_exactly_three_self_referential_infrastructure_paths():
    arch = architecture()
    exclusions = arch["manifest_contract"]["infrastructure_exclusions_exact"]
    assert exclusions == [
        "docs/plans/harbour-one-h1r-04-state-census-manifest.json",
        "scripts/audit_harbour_knowledge_state_census.py",
        "tests/test_audit_harbour_knowledge_state_census.py",
    ]
    assert len(set(exclusions)) == 3
    assert arch["manifest_contract"]["control_plane_exclusions_exact"] == [
        "docs/plans/handoffs/abc-go-open-work-10h-run-20260904.json"
    ]


def test_control_plane_record_is_exact_safe_and_identity_independent(tmp_path):
    arch = without_semantic_pins(architecture())
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    record_path = contract["control_plane_record_boundary"]["control_plane_records_exact"][0]
    record = json.loads((ROOT / record_path).read_text(encoding="utf-8"))
    target = tmp_path / record_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    candidate = tmp_path / "src" / "new_state.py"
    candidate.parent.mkdir()
    candidate.write_text("value = 1", encoding="utf-8")
    sibling = tmp_path / "docs" / "plans" / "handoffs" / "product-state.json"
    sibling.write_text('{"write_text": true}', encoding="utf-8")
    tracked = b"\0".join(path.encode() for path in (
        record_path, "src/new_state.py", "docs/plans/handoffs/product-state.json"
    )) + b"\0"
    def runner(_root, argv):
        return tracked if argv == contract["git_tracked_argv"] else b""
    files, first = census.discover(tmp_path, arch, runner=runner)
    assert files == 2
    assert {item["path"] for item in first} == {
        "src/new_state.py", "docs/plans/handoffs/product-state.json",
    }
    record["revision"] += 1
    record["state"] = "paused"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    files, second = census.discover(tmp_path, arch, runner=runner)
    assert files == 2 and second == first
    record["state"] = "unknown"
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert_failure("control_plane_record_invalid", census.discover, tmp_path, arch, runner=runner)


def test_control_plane_record_rejects_payload_schema_and_executable_mode(tmp_path, monkeypatch):
    arch = architecture()
    boundary = arch["census_contract"]["control_plane_record_boundary"]
    record_path = boundary["control_plane_records_exact"][0]
    target = tmp_path / record_path
    target.parent.mkdir(parents=True)
    valid = (ROOT / record_path).read_bytes()
    for payload in (b"{}", b'{"x":1,"x":2}', b"#!run\n{}", b"\xff", b"x\x00y"):
        target.write_bytes(payload)
        assert_failure(
            "control_plane_record_invalid",
            census._validate_control_plane_record,
            tmp_path, record_path, boundary,
        )
    boolean_schema = json.loads(valid)
    boolean_schema["schema_version"] = True
    target.write_text(json.dumps(boolean_schema, ensure_ascii=False), encoding="utf-8")
    assert_failure(
        "control_plane_record_invalid",
        census._validate_control_plane_record,
        tmp_path, record_path, boundary,
    )
    target.write_bytes(valid)
    real_snapshot = census._component_snapshot
    first = real_snapshot(tmp_path, record_path)
    changed = list(first)
    path, identity = changed[-1]
    identity = list(identity)
    identity[0] |= 0o111
    changed[-1] = (path, tuple(identity))
    for platform_name in ("posix", "nt"):
        with monkeypatch.context() as patcher:
            patcher.setattr(census.os, "name", platform_name)
            patcher.setattr(census, "_component_snapshot", lambda *_args, **_kwargs: changed)
            assert_failure(
                "control_plane_record_invalid",
                census._validate_control_plane_record,
                tmp_path, record_path, boundary,
            )


def test_census_rejects_open_schema_enum_or_empty_reason():
    arch = architecture()
    found = discovered_seeds(arch)
    variants = []
    def variant(change):
        value = valid_manifest(arch)
        change(value)
        variants.append(value)
    variant(lambda value: value.__setitem__("extra", True))
    variant(lambda value: value.pop("mode"))
    variant(lambda value: value.__setitem__("schema_version", True))
    variant(lambda value: value.__setitem__("kind", "wrong"))
    variant(lambda value: value.__setitem__("mode", "wrong"))
    variant(lambda value: value.__setitem__("scan_contract_digest", value["scan_contract_digest"].upper()))
    variant(lambda value: value.__setitem__("infrastructure_exclusions", list(reversed(value["infrastructure_exclusions"]))))
    variant(lambda value: value.__setitem__("control_plane_exclusions", []))
    variant(lambda value: value["control_plane_exclusions"].append("docs/plans/handoffs/other.json"))
    variant(lambda value: value["entries"][0].__setitem__("extra", "x"))
    variant(lambda value: value["entries"][0].pop("reason"))
    variant(lambda value: value["entries"].__setitem__(0, "not-an-object"))
    variant(lambda value: value["entries"][0].__setitem__("raw_blob", 1))
    variant(lambda value: value["entries"][0].__setitem__("raw_blob", "A" * 40))
    for field in ("classification", "owner", "writer_authority", "role", "disposition"):
        variant(lambda value, field=field: value["entries"][0].__setitem__(field, "invented"))
    variant(lambda value: value["entries"][0].__setitem__("reason", ""))
    for index, manifest in enumerate(variants):
        try:
            census.validate_manifest(manifest, arch, found)
        except census.AuditFailure as caught:
            assert caught.codes == ["manifest_mismatch"], index
        else:
            pytest.fail(f"variant {index} was accepted")

    extra = {"path": "src/new_state.py", "raw_blob": "0" * 40}
    extra_found = sorted(found + [extra], key=lambda item: item["path"].encode())
    stateless = valid_manifest(arch, [extra])
    entry = next(item for item in stateless["entries"] if item["path"] == extra["path"])
    entry["classification"] = "stateless_allowlist"
    assert_failure("manifest_mismatch", census.validate_manifest, stateless, arch, extra_found)
    assert_failure("input_invalid", census.load_json, b'{"x":1,"x":2}')


def test_census_rejects_projection_fixture_worker_writeback_authority():
    arch = architecture()
    found = discovered_seeds(arch)
    for role in ("canonical_or_legacy", "projection", "fixture_or_ephemeral", "worker_execution_separate"):
        manifest = valid_manifest(arch)
        entry = next(item for item in manifest["entries"] if item["role"] == role)
        entry["writer_authority"] = "harbour_target_no_current_writer"
        assert_failure("manifest_mismatch", census.validate_manifest, manifest, arch, found)


def test_census_report_is_deterministic_content_free_and_read_only():
    entries = [{"path": "src/state.py", "raw_blob": "a" * 40}]
    report = census.discovery_report(status="complete", files=1, entries=entries, errors=[])
    first = census.canonical_bytes(report, sort_keys=True) + b"\n"
    second = census.canonical_bytes(report, sort_keys=True) + b"\n"
    assert first == second
    assert b"complete" in first and b"C:\\" not in first
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert imported.isdisjoint({"socket", "urllib", "requests", "httpx"})
    filesystem_mutators = {"write_text", "write_bytes", "unlink", "remove", "rename", "replace", "mkdir", "rmdir"}
    assert not [
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in filesystem_mutators
    ]
    assert "DATA_DIR" not in SCRIPT.read_text(encoding="utf-8")


def test_census_rejects_git_exit_zero_with_stderr_warning(monkeypatch, tmp_path):
    class Completed:
        returncode, stdout, stderr = 0, b"x\0", b"warning"
    monkeypatch.setattr(census.subprocess, "run", lambda *a, **k: Completed())
    assert_failure("git_inventory_failed", census._run_git, tmp_path, ["git"])
    Completed.returncode, Completed.stderr = 1, b""
    assert_failure("git_inventory_failed", census._run_git, tmp_path, ["git"])


def test_census_digest_format_and_golden_vector():
    assert census.sha256_json([], sort_keys=False) == census.EMPTY_DIGEST
    assert re.fullmatch(r"[0-9a-f]{64}", census.EMPTY_DIGEST)
    arch = architecture()
    independent_contract = hashlib.sha256(json.dumps(
        arch["census_contract"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert census.sha256_json(arch["census_contract"], sort_keys=True) == independent_contract
    entries = [
        {"path": "a", "raw_blob": "0" * 40},
        {"path": "b", "raw_blob": "1" * 40},
    ]
    independent_members = hashlib.sha256(
        b'[["a","0000000000000000000000000000000000000000"],["b","1111111111111111111111111111111111111111"]]'
    ).hexdigest()
    assert census.member_digest(entries) == independent_members
    assert census.member_digest(entries) != census.member_digest(list(reversed(entries)))


def test_census_requires_all_ten_mandatory_candidates_and_exact_blobs(tmp_path):
    arch = without_semantic_pins(without_control_plane(architecture()))
    contract = arch["census_contract"]
    mandatory = arch["mandatory_post_r3_discovery_candidates"]
    arch["current_inventory_seed"] = []
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    for item in mandatory:
        target = tmp_path / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"safe")
        item["raw_blob"] = census.raw_blob(b"safe")
    tracked = b"\0".join(item["path"].encode() for item in mandatory) + b"\0"
    def runner(_root, argv):
        return tracked if argv == contract["git_tracked_argv"] else b""
    files, found = census.discover(tmp_path, arch, runner=runner)
    assert files == 10 and {item["path"] for item in found} == {item["path"] for item in mandatory}
    for index in range(10):
        drifted = copy.deepcopy(arch)
        drifted["mandatory_post_r3_discovery_candidates"][index]["raw_blob"] = "0" * 40
        assert_failure("identity_mismatch", census.discover, tmp_path, drifted, runner=runner)
        missing_path = mandatory[index]["path"]
        missing_inventory = b"\0".join(
            item["path"].encode() for item in mandatory if item["path"] != missing_path
        ) + b"\0"
        def missing_runner(_root, argv, payload=missing_inventory):
            return payload if argv == contract["git_tracked_argv"] else b""
        assert_failure("identity_mismatch", census.discover, tmp_path, arch, runner=missing_runner)
    missing_file = tmp_path / mandatory[0]["path"]
    missing_file.unlink()
    assert_failure("identity_mismatch", census.discover, tmp_path, arch, runner=runner)


def test_census_enforces_seed_presence_and_exact_raw_identity(tmp_path):
    arch = without_semantic_pins(without_control_plane(architecture()))
    payload = b"state"
    arch["current_inventory_seed"] = [{
        "path": "seed_state.py", "raw_blob": census.raw_blob(payload),
        "group": "canonical_or_legacy", "raw_identity_changed_from_r3": False,
    }]
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    (tmp_path / "seed_state.py").write_bytes(payload)
    def runner(_root, argv):
        return b"seed_state.py\0" if argv == contract["git_tracked_argv"] else b""
    assert census.discover(tmp_path, arch, runner=runner)[1][0]["raw_blob"] == census.raw_blob(payload)
    drifted = copy.deepcopy(arch)
    drifted["current_inventory_seed"][0]["raw_blob"] = "0" * 40
    assert_failure("identity_mismatch", census.discover, tmp_path, drifted, runner=runner)
    assert_failure("identity_mismatch", census.discover, tmp_path, arch, runner=lambda *_args: b"")


def test_census_uses_exact_tracked_summary_and_configured_untracked_inventory_union(tmp_path):
    contract = copy.deepcopy(architecture()["census_contract"])
    contract["configured_untracked_roots_exact"] = ["backend"]
    (tmp_path / ".abc-test-tmp-x").mkdir()
    (tmp_path / "backend").mkdir()
    def runner(_root, argv):
        if argv == contract["git_tracked_argv"]:
            return b"src/memory.py\0"
        if argv == contract["git_untracked_summary_argv"]:
            return b"root_state.py\0backend/\0.abc-test-tmp-x/\0"
        if argv == contract["git_untracked_argv"]:
            return b"backend/new_state_store.py\0"
        raise AssertionError(argv)
    assert census.inventory_paths(tmp_path, contract, runner=runner) == [
        "backend/new_state_store.py", "root_state.py", "src/memory.py"
    ]


def test_census_validates_configured_roots_before_recursive_git_and_all_denominator_paths(tmp_path, monkeypatch):
    contract = copy.deepcopy(architecture()["census_contract"])
    contract["configured_untracked_roots_exact"] = ["backend"]
    calls = []
    def runner(_root, argv):
        calls.append(argv)
        return b""
    original = census._validate_directory
    def reject_backend(root, relative=None):
        if relative == "backend":
            raise census.AuditFailure("reparse_or_symlink")
        return original(root, relative)
    monkeypatch.setattr(census, "_validate_directory", reject_backend)
    assert_failure("reparse_or_symlink", census.inventory_paths, tmp_path, contract, runner=runner)
    assert contract["git_untracked_argv"] not in calls
    monkeypatch.setattr(census, "_validate_directory", original)

    arch = without_semantic_pins(without_control_plane(architecture()))
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    (tmp_path / "plain.bin").mkdir()
    def directory_runner(_root, argv):
        return b"plain.bin\0" if argv == contract["git_tracked_argv"] else b""
    assert_failure("not_regular", census.discover, tmp_path, arch, runner=directory_runner)


def test_census_rejects_unknown_untracked_root_without_descending_operational_prefixes(tmp_path):
    contract = copy.deepcopy(architecture()["census_contract"])
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = [".abc-test-tmp"]
    def runner(_root, argv):
        return b"new_domain/\0" if argv == contract["git_untracked_summary_argv"] else b""
    assert_failure("unknown_untracked_root", census.inventory_paths, tmp_path, contract, runner=runner)
    operational = tmp_path / ".abc-test-tmp-safe"
    operational.mkdir()
    calls = []
    def safe_runner(_root, argv):
        calls.append(argv)
        return b".abc-test-tmp-safe/\0" if argv == contract["git_untracked_summary_argv"] else b""
    assert census.inventory_paths(tmp_path, contract, runner=safe_runner) == []
    assert contract["git_untracked_argv"] in calls
    near = tmp_path / ".abc-test"
    near.mkdir()
    def near_runner(_root, argv):
        return b".abc-test/\0" if argv == contract["git_untracked_summary_argv"] else b""
    assert_failure("unknown_untracked_root", census.inventory_paths, tmp_path, contract, runner=near_runner)
    bad = tmp_path / ".abc-test-tmp-file"
    bad.write_text("not a directory", encoding="utf-8")
    def bad_runner(_root, argv):
        return b".abc-test-tmp-file/\0" if argv == contract["git_untracked_summary_argv"] else b""
    assert_failure("unknown_untracked_root", census.inventory_paths, tmp_path, contract, runner=bad_runner)


def test_census_binary_predicate_is_exactly_nul_and_vendor_control_bundles_remain_scannable():
    assert census._decode_candidate(b"\x01\x02\x1f\x7f") == "\x01\x02\x1f\x7f"
    for relative in (
        "static/lib/mammoth.browser.min.js", "static/lib/html2pdf.bundle.min.js",
        "static/lib/docx.umd.min.js", "static/lib/xlsx.full.min.js",
    ):
        data = (ROOT / relative).read_bytes()
        assert b"\x00" not in data
        census._decode_candidate(data)


def test_census_report_modes_types_status_errors_and_failure_sentinels_are_exact(monkeypatch, capsysbinary):
    invalid = census.invalid_report()
    assert list(sorted(invalid)) == sorted([
        "schema_version", "kind", "status", "files_enumerated",
        "candidates_detected", "member_set_digest", "errors",
    ])
    assert invalid["member_set_digest"] == census.EMPTY_DIGEST
    assert type(invalid["files_enumerated"]) is int
    failure = census.validation_report(
        status="invalid", files=0, candidates=0, classified=0,
        unclassified=0, digest=census.EMPTY_DIGEST, errors=["manifest_mismatch"],
    )
    assert failure["errors"] == ["manifest_mismatch"]
    arch = architecture()
    found = discovered_seeds(arch)
    rc, report, raw, err = invoke_main(
        monkeypatch, capsysbinary, ["--discover", "--format", "json"],
        valid_manifest(arch), found,
    )
    expected = census.discovery_report(status="complete", files=99, entries=found, errors=[])
    assert (rc, report, raw, err) == (0, expected, census.canonical_bytes(expected, sort_keys=True) + b"\n", b"")
    rc, report, _, err = invoke_main(
        monkeypatch, capsysbinary,
        ["--manifest", "manifest.json", "--format", "json"],
        valid_manifest(arch), found,
    )
    assert rc == 0 and err == b"" and report["status"] == "complete"
    assert report["classified_count"] == 17 and report["unclassified_count"] == 0

    base_raw = (ROOT / census.BASE_ARCHITECTURE).read_bytes()
    correction_raw = (ROOT / census.CORRECTION_ARCHITECTURE).read_bytes()
    portable_raw = (ROOT / census.PORTABLE_ARCHITECTURE).read_bytes()
    ast_raws = {
        relative: (ROOT / relative).read_bytes()
        for relative in (
            census.AST_ARCHITECTURE_R1, census.AST_ARCHITECTURE_R2,
            census.AST_ARCHITECTURE_R3, census.AST_ARCHITECTURE_R4,
        )
    }
    def architecture_reader(_root, relative, _limit):
        return {
            census.BASE_ARCHITECTURE: base_raw,
            census.CORRECTION_ARCHITECTURE: correction_raw,
            census.PORTABLE_ARCHITECTURE: portable_raw,
            **ast_raws,
        }[relative]
    monkeypatch.setattr(census, "read_safe_file", architecture_reader)
    monkeypatch.setattr(census, "discover", lambda *_args: (_ for _ in ()).throw(census.AuditFailure("git_inventory_failed")))
    rc = census.main(["--discover", "--format", "json"])
    captured = capsysbinary.readouterr()
    failed = json.loads(captured.out)
    assert rc == 1 and captured.err == b""
    assert failed == census.discovery_report(status="invalid", files=0, entries=[], errors=["git_inventory_failed"])


@pytest.mark.parametrize("argv", [
    [], ["--discover"], ["--discover", "--format", "json", "--expect-unclassified"],
    ["--manifest", "x", "--format", "xml"],
    ["--discover", "--manifest", "x", "--format", "json"],
    ["--discover", "--discover", "--format", "json"],
    ["--manifest", "x", "--manifest", "y", "--format", "json"],
    ["--manifest", "x", "--format", "json", "--format", "json"],
    ["--manifest", "x", "--format", "json", "--expect-unclassified", "--expect-unclassified"],
    ["--manifest", "--format", "json", "x"],
    ["--unknown"],
])
def test_census_cli_invocation_matrix_and_invalid_invocation_schema_are_exact(argv, capsysbinary):
    assert census._invocation(argv) is None
    rc = census.main(argv)
    captured = capsysbinary.readouterr()
    expected = census.invalid_report()
    assert rc == 1 and captured.err == b""
    assert captured.out == census.canonical_bytes(expected, sort_keys=True) + b"\n"
    assert census._invocation(["--discover", "--format", "json"]) == ("discovery", None, False)
    assert census._invocation(["--manifest", "x", "--format", "json", "--expect-unclassified"]) == ("validation", "x", True)


def test_census_expect_unclassified_rejects_zero_unclassified_manifest(monkeypatch, capsysbinary):
    arch = architecture()
    found = discovered_seeds(arch)
    rc, report, raw, err = invoke_main(
        monkeypatch, capsysbinary,
        ["--manifest", "manifest.json", "--format", "json", "--expect-unclassified"],
        valid_manifest(arch), found,
    )
    expected = census.validation_report(
        status="invalid", files=0, candidates=0, classified=0,
        unclassified=0, digest=census.EMPTY_DIGEST, errors=["manifest_mismatch"],
    )
    assert (rc, report, raw, err) == (1, expected, census.canonical_bytes(expected, sort_keys=True) + b"\n", b"")


def test_cli_invalid_invocation_is_exact_and_silent_on_stderr():
    result = subprocess.run([str(ROOT / "venv" / "Scripts" / "python.exe"), "-B", str(SCRIPT)], capture_output=True)
    assert result.returncode == 1 and result.stderr == b""
    assert json.loads(result.stdout) == census.invalid_report()


def test_main_safely_reads_and_binds_the_accepted_architecture(monkeypatch, capsysbinary):
    reads = []
    def wrong_architecture(_root, relative, limit):
        reads.append((relative, limit))
        return b"{}"
    monkeypatch.setattr(census, "read_safe_file", wrong_architecture)
    rc = census.main(["--discover", "--format", "json"])
    captured = capsysbinary.readouterr()
    expected = census.discovery_report(
        status="invalid", files=0, entries=[], errors=["identity_mismatch"]
    )
    assert rc == 1 and captured.err == b""
    assert json.loads(captured.out) == expected
    assert reads == [(census.BASE_ARCHITECTURE, 4_194_304)]


@pytest.mark.parametrize("tampered", [
    census.BASE_ARCHITECTURE,
    census.CORRECTION_ARCHITECTURE,
    census.PORTABLE_ARCHITECTURE,
    census.AST_ARCHITECTURE_R1,
    census.AST_ARCHITECTURE_R2,
    census.AST_ARCHITECTURE_R3,
    census.AST_ARCHITECTURE_R4,
])
def test_effective_architecture_raw_binds_all_seven_layers(tampered):
    reads = []
    raw_by_path = {
        relative: (ROOT / relative).read_bytes()
        for relative in (
            census.BASE_ARCHITECTURE,
            census.CORRECTION_ARCHITECTURE,
            census.PORTABLE_ARCHITECTURE,
            census.AST_ARCHITECTURE_R1,
            census.AST_ARCHITECTURE_R2,
            census.AST_ARCHITECTURE_R3,
            census.AST_ARCHITECTURE_R4,
        )
    }
    def reader(_root, relative, limit):
        reads.append((relative, limit))
        raw = raw_by_path[relative]
        return raw + b" " if relative == tampered else raw
    assert_failure("identity_mismatch", census.load_effective_architecture, ROOT, reader=reader)
    expected_prefix = [
        (census.BASE_ARCHITECTURE, 4_194_304),
        (census.CORRECTION_ARCHITECTURE, 4_194_304),
        (census.PORTABLE_ARCHITECTURE, 4_194_304),
        (census.AST_ARCHITECTURE_R1, 4_194_304),
        (census.AST_ARCHITECTURE_R2, 4_194_304),
        (census.AST_ARCHITECTURE_R3, 4_194_304),
        (census.AST_ARCHITECTURE_R4, 4_194_304),
    ]
    assert reads == expected_prefix[:expected_prefix.index((tampered, 4_194_304)) + 1]


def test_r4_patch_preconditions_and_exact_two_replace_one_add_merge():
    paths = (
        census.BASE_ARCHITECTURE,
        census.CORRECTION_ARCHITECTURE,
        census.PORTABLE_ARCHITECTURE,
        census.AST_ARCHITECTURE_R1,
        census.AST_ARCHITECTURE_R2,
        census.AST_ARCHITECTURE_R3,
        census.AST_ARCHITECTURE_R4,
    )
    layers = [
        census.load_json((ROOT / relative).read_bytes())
        for relative in paths
    ]
    r2_semantic = layers[4]["corrected_semantic_contract"]
    values = layers[6]["field_values"]
    expected = copy.deepcopy(r2_semantic)
    expected["module_graph"]["relative_rule"] = values[
        "module_graph.relative_rule"
    ]
    expected["binding_grammar"]["trusted_alias_rule"] = values[
        "binding_grammar.trusted_alias_rule"
    ]
    expected["module_graph"]["package_initializer_execution_rule"] = values[
        "module_graph.package_initializer_execution_rule"
    ]
    effective = census.effective_architecture(*layers)
    assert (
        effective["census_contract"]["semantic_python_discovery_boundary"]
        == expected
    )

    for dotted in (
        "module_graph.relative_rule",
        "binding_grammar.trusted_alias_rule",
    ):
        section, field = dotted.split(".")
        for hostile in ("missing", True):
            changed = copy.deepcopy(layers)
            if hostile == "missing":
                changed[4]["corrected_semantic_contract"][section].pop(field)
            else:
                changed[4]["corrected_semantic_contract"][section][field] = hostile
            assert_failure(
                "identity_mismatch", census.effective_architecture, *changed
            )

    changed = copy.deepcopy(layers)
    changed[4]["corrected_semantic_contract"]["module_graph"][
        "package_initializer_execution_rule"
    ] = "already present"
    assert_failure(
        "identity_mismatch", census.effective_architecture, *changed
    )


def test_semantic_static_import_forms_and_namespace_no_descendant_edge():
    files = {
        "pkg/mod.py": "",
        "direct.py": "import pkg.mod",
        "from_pkg.py": "from pkg import mod",
        "from_symbol.py": "from pkg.mod import symbol",
        "star.py": "from pkg.mod import *",
        "alias.py": "import pkg.mod as M",
        "local.py": "def load():\n    import pkg.mod",
    }
    assert semantic_candidates(files, {"pkg/mod.py"}) == set(files)

    namespace = {
        "ns/mod.py": "",
        "caller.py": "import ns",
    }
    assert semantic_candidates(namespace, {"ns/mod.py"}) == {"ns/mod.py"}


def test_semantic_relative_import_exact_level_matrix():
    ordinary = {
        "core/target.py": "",
        "core/caller.py": "from . import target",
    }
    assert semantic_candidates(ordinary, {"core/target.py"}) == set(ordinary)

    initializer = {
        "core/__init__.py": "from . import target",
        "core/target.py": "",
    }
    assert semantic_candidates(initializer, {"core/target.py"}) == set(initializer)

    level_two = {
        "pkg/target.py": "",
        "pkg/sub/caller.py": "from .. import target",
    }
    assert semantic_candidates(level_two, {"pkg/target.py"}) == set(level_two)

    for files in (
        {
            "core/target.py": "",
            "core/caller.py": "from .. import target",
        },
        {
            "pkg/target.py": "",
            "pkg/sub/caller.py": "from ... import target",
        },
        {
            "pkg/target.py": "",
            "pkg/sub/caller.py": "from .... import target",
        },
    ):
        assert_failure(
            "identity_mismatch", semantic_candidates, files
        )


def test_semantic_inventory_and_module_identities_are_exact():
    boundary = architecture()["census_contract"]["semantic_python_discovery_boundary"]
    assert census._python_module_record("src/example.py") == (
        "src.example", "src", False,
    )
    assert census._python_module_record("src/pkg/__init__.py") == (
        "src.pkg", "src.pkg", True,
    )
    assert census._python_module_record("src/example.PY") is None
    assert census._python_module_record("src/class.py") is None
    assert census._python_module_record(".hidden.py") is None
    assert census._semantic_source_paths(
        ["root.py", "root.PY", "src/a.py", "tests/a.py"], boundary
    ) == ["root.py", "src/a.py"]
    assert semantic_candidates({".hidden.py": "import target"}) == {".hidden.py"}


def test_semantic_static_relative_fixpoint_and_initializer_edges():
    files = {
        "target.py": "",
        "a.py": "import target",
        "b.py": "from a import value",
        "target_extra.py": "",
        "prefix.py": "import target_extra",
    }
    found = semantic_candidates(files, {"target.py"})
    assert {"target.py", "a.py", "b.py"}.issubset(found)
    assert "prefix.py" not in found

    relative = {
        "pkg/target.py": "",
        "pkg/sub/caller.py": "from .. import target",
    }
    assert semantic_candidates(relative, {"pkg/target.py"}) == {
        "pkg/target.py", "pkg/sub/caller.py",
    }
    with pytest.raises(census.AuditFailure) as caught:
        semantic_candidates({"root.py": "from . import target"})
    assert caught.value.codes == ["identity_mismatch"]

    package = {
        "pkg/__init__.py": "",
        "pkg/mod.py": "",
        "app.py": "import pkg.mod",
    }
    package_found = semantic_candidates(package, {"pkg/__init__.py"})
    assert "app.py" in package_found
    assert "pkg/mod.py" not in package_found


def test_semantic_type_checking_guards_and_ambiguity_are_closed():
    files = {
        "target.py": "",
        "closed.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if C:\n    import target\n"
        ),
        "negative.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if not C:\n    import target\n"
        ),
        "compound.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if C and flag:\n    import target\n"
        ),
        "module_alias.py": (
            "import typing as T\n"
            "if T.TYPE_CHECKING:\n    import target\n"
        ),
        "outer.py": (
            "from typing import TYPE_CHECKING as C\n"
            "def f():\n    if C:\n        import target\n"
        ),
    }
    found = semantic_candidates(files, {"target.py"})
    assert "closed.py" not in found
    assert "module_alias.py" not in found
    assert {"negative.py", "compound.py", "outer.py"}.issubset(found)


def test_semantic_dynamic_loaders_escapes_shadowing_and_literals():
    files = {
        "target.py": "",
        "supported.py": (
            "from importlib import import_module as F\n"
            "F('target')\n"
        ),
        "external.py": (
            "from importlib import import_module as F\n"
            "F('outside.valid')\n"
        ),
        "invalid.py": (
            "from importlib import import_module as F\n"
            "F('outside..invalid')\n"
        ),
        "escape.py": (
            "from importlib import import_module as F\n"
            "saved = F\n"
        ),
        "shadowed.py": (
            "from importlib import import_module as F\n"
            "try:\n    pass\n"
            "except Exception as F:\n    pass\n"
            "F('outside.valid')\n"
        ),
        "outer.py": (
            "import importlib as I\n"
            "def load():\n    return I.import_module('target')\n"
        ),
        "type_only_loader.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if C:\n    from importlib import import_module as F\n"
            "F('outside.valid')\n"
        ),
        "type_only_guard_alias.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if C:\n    from typing import TYPE_CHECKING as D\n"
            "if D:\n    import target\n"
        ),
        "type_only_guard_reverse.py": (
            "from typing import TYPE_CHECKING as C\n"
            "if D:\n    import target\n"
            "if C:\n    from typing import TYPE_CHECKING as D\n"
        ),
        "walrus.py": (
            "from importlib import import_module as F\n"
            "[(F := value) for value in ()]\n"
            "F('outside.valid')\n"
        ),
        "deleted.py": (
            "from importlib import import_module as F\n"
            "del F\n"
            "F('outside.valid')\n"
        ),
        "global_outer.py": (
            "from importlib import import_module as F\n"
            "def load():\n"
            "    global F\n"
            "    return None\n"
        ),
    }
    found = semantic_candidates(files, {"target.py"})
    assert {
        "supported.py", "invalid.py", "escape.py", "shadowed.py", "outer.py",
        "type_only_loader.py", "type_only_guard_alias.py", "type_only_guard_reverse.py", "walrus.py", "global_outer.py",
    }.issubset(found)
    assert "external.py" not in found
    assert "deleted.py" not in found



@pytest.mark.parametrize("source", [
    "__import__('target')",
    "import importlib as I\nI.import_module('target')",
])
def test_semantic_direct_and_module_alias_loaders_are_supported(source):
    assert semantic_candidates(
        {"target.py": "", "caller.py": source}, {"target.py"}
    ) == {"target.py", "caller.py"}


@pytest.mark.parametrize("source", [
    "from importlib import import_module as F\nF('outside.valid', None)",
    "from importlib import import_module as F\nF(name='outside.valid')",
    "from importlib import import_module as F\nF('.target')",
    (
        "from importlib import import_module as F\n"
        "name = 'outside.valid'\n"
        "F(name)"
    ),
    (
        "def outer():\n"
        "    from importlib import import_module as F\n"
        "    def inner():\n"
        "        nonlocal F\n"
        "        return None"
    ),
    (
        "from importlib import import_module as F\n"
        "from somewhere import value as F\n"
        "F('outside.valid')"
    ),
    (
        "from importlib import import_module as F\n"
        "match value:\n"
        "    case F:\n"
        "        pass\n"
        "F('outside.valid')"
    ),
    (
        "from importlib import import_module as F\n"
        "match value:\n"
        "    case [*F]:\n"
        "        pass\n"
        "F('outside.valid')"
    ),
    (
        "from importlib import import_module as F\n"
        "match value:\n"
        "    case {'x': _, **F}:\n"
        "        pass\n"
        "F('outside.valid')"
    ),
])
def test_semantic_remaining_loader_and_binding_ambiguity_is_uncertain(source):
    assert semantic_candidates({"caller.py": source}) == {"caller.py"}


def test_semantic_plain_text_is_negative_and_uncertainty_reaches_two_importers():
    assert semantic_candidates({
        "plain.py": (
            "# manage_memory\n"
            '"""manage_memory"""\n'
            'value = "manage_memory"'
        )
    }) == set()

    for uncertain_source in (
        "if True print",
        "from typing import TYPE_CHECKING as C\nif C and flag:\n    pass",
        "from importlib import import_module as F\nF(name='outside.valid')",
    ):
        files = {
            "uncertain.py": uncertain_source,
            "importer.py": "import uncertain",
            "upstream.py": "import importer",
        }
        assert semantic_candidates(files) == set(files)

def test_semantic_resource_failure_is_an_uncertainty_anchor(monkeypatch):
    original_walk = census.ast.walk
    calls = 0
    def failing_walk(node):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RecursionError
        return original_walk(node)
    monkeypatch.setattr(census.ast, "walk", failing_walk)
    assert semantic_candidates({"uncertain.py": ""}) == {"uncertain.py"}


def test_terminal_inventory_compares_each_git_sequence(tmp_path):
    arch = without_semantic_pins(without_control_plane(architecture()))
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []
    (tmp_path / "a.txt").write_text("json.dump", encoding="utf-8")
    tracked_calls = 0
    def runner(_root, argv):
        nonlocal tracked_calls
        if argv == contract["git_tracked_argv"]:
            tracked_calls += 1
            return b"a.txt\0" if tracked_calls == 1 else b""
        if argv == contract["git_untracked_summary_argv"]:
            return b"" if tracked_calls == 1 else b"a.txt\0"
        if argv == contract["git_untracked_argv"]:
            return b"" if tracked_calls == 1 else b"a.txt\0"
        raise AssertionError(argv)
    assert_failure("toctou_changed", census.discover, tmp_path, arch, runner=runner)


@pytest.mark.parametrize("drift_key", (
    "git_tracked_argv",
    "git_untracked_summary_argv",
    "git_untracked_argv",
))
def test_terminal_inventory_compares_each_git_sequence_independently(
    tmp_path, drift_key,
):
    arch = without_semantic_pins(without_control_plane(architecture()))
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []

    for name in ("a.txt", "b.txt", "u.txt", "v.txt"):
        (tmp_path / name).write_text("plain", encoding="utf-8")

    stable = {
        "git_tracked_argv": b"a.txt\0b.txt\0",
        "git_untracked_summary_argv": b"u.txt\0v.txt\0",
        "git_untracked_argv": b"u.txt\0v.txt\0",
    }
    changed = {
        "git_tracked_argv": b"b.txt\0a.txt\0",
        "git_untracked_summary_argv": b"v.txt\0u.txt\0",
        "git_untracked_argv": b"v.txt\0u.txt\0",
    }
    calls = {key: 0 for key in stable}

    def runner(_root, argv):
        key = next(
            key for key in stable
            if list(argv) == contract[key]
        )
        calls[key] += 1
        if key == drift_key and calls[key] == 2:
            return changed[key]
        return stable[key]

    assert_failure(
        "toctou_changed",
        census.discover,
        tmp_path,
        arch,
        runner=runner,
    )
    assert calls == {key: 2 for key in stable}


def test_terminal_semantic_raw_blob_drift_reads_a_b_a_b(tmp_path):
    arch = without_semantic_pins(without_control_plane(architecture()))
    arch["current_inventory_seed"] = []
    arch["mandatory_post_r3_discovery_candidates"] = []
    arch["manifest_contract"]["infrastructure_exclusions_exact"] = []
    contract = arch["census_contract"]
    contract["configured_untracked_roots_exact"] = []
    contract["operational_untracked_directory_prefixes_exact"] = []

    (tmp_path / "a.py").write_bytes(b"placeholder-a")
    (tmp_path / "b.py").write_bytes(b"placeholder-b")

    def runner(_root, argv):
        if list(argv) == contract["git_tracked_argv"]:
            return b"a.py\0b.py\0"
        if list(argv) in (
            contract["git_untracked_summary_argv"],
            contract["git_untracked_argv"],
        ):
            return b""
        raise AssertionError(argv)

    versions = {
        "a.py": [b"import b\n", b"import b\n"],
        "b.py": [b"", b"# terminal drift\n"],
    }
    seen = {"a.py": 0, "b.py": 0}
    reads = []

    def reader(_root, relative, _limit):
        reads.append(relative)
        index = seen[relative]
        seen[relative] += 1
        return versions[relative][index]

    assert_failure(
        "toctou_changed",
        census.discover,
        tmp_path,
        arch,
        runner=runner,
        reader=reader,
    )
    assert reads == ["a.py", "b.py", "a.py", "b.py"]
    assert seen == {"a.py": 2, "b.py": 2}


def test_main_preloads_manifest_before_discover_and_reads_nothing_after(
    monkeypatch, capsysbinary,
):
    arch = architecture()
    found = discovered_seeds(arch)
    manifest = valid_manifest(arch)
    manifest_arg = "docs/plans/harbour-one-h1r-04-state-census-manifest.json"
    manifest_raw = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    events = []
    phase = {"discover_started": False}

    def reader(_root, relative, _limit):
        events.append(("read", relative))
        assert not phase["discover_started"]
        assert relative == manifest_arg
        return manifest_raw

    def discover(_root, effective):
        assert effective is arch
        events.append(("discover", None))
        phase["discover_started"] = True
        return 99, found

    monkeypatch.setattr(census, "load_effective_architecture", lambda _root: arch)
    monkeypatch.setattr(census, "read_safe_file", reader)
    monkeypatch.setattr(census, "discover", discover)
    rc = census.main(["--manifest", manifest_arg, "--format", "json"])
    captured = capsysbinary.readouterr()
    assert rc == 0 and captured.err == b""
    assert json.loads(captured.out)["status"] == "complete"
    assert events == [("read", manifest_arg), ("discover", None)]


def test_real_discovery_manifest_is_complete_content_free_and_current():
    manifest_path = ROOT / "docs" / "plans" / "harbour-one-h1r-04-state-census-manifest.json"
    manifest = census.load_json(manifest_path.read_bytes())
    assert list(manifest) == [
        "schema_version", "kind", "mode", "scan_contract_digest",
        "infrastructure_exclusions", "control_plane_exclusions", "entries",
    ]
    assert len(manifest["entries"]) == 1859
    assert sum(item["classification"] == "state_bearing" for item in manifest["entries"]) == 17
    assert sum(item["classification"] == "unclassified" for item in manifest["entries"]) == 1842
    manifest_paths = {item["path"] for item in manifest["entries"]}
    positive_paths = {
        pin.rsplit("@", 1)[0]
        for pin in architecture()["semantic_positive_ground_truth"]
    }
    assert positive_paths.issubset(manifest_paths)
    result = subprocess.run([
        str(ROOT / "venv" / "Scripts" / "python.exe"), "-B", str(SCRIPT),
        "--manifest", "docs/plans/harbour-one-h1r-04-state-census-manifest.json",
        "--format", "json", "--expect-unclassified",
    ], cwd=ROOT, capture_output=True)
    assert result.returncode == 0 and result.stderr == b""
    report = json.loads(result.stdout)
    assert report == {
        "schema_version": 1,
        "kind": census.VALIDATION_KIND,
        "status": "unclassified",
        "files_enumerated": 4053,
        "candidates_detected": 1859,
        "classified_count": 17,
        "unclassified_count": 1842,
        "member_set_digest": "548840efd5f0090a11897284f0df349f3928a37921690e971caa98cab8a6ce5e",
        "errors": [],
    }
    assert result.stdout == census.canonical_bytes(report, sort_keys=True) + b"\n"
    default_result = subprocess.run([
        str(ROOT / "venv" / "Scripts" / "python.exe"), "-B", str(SCRIPT),
        "--manifest", "docs/plans/harbour-one-h1r-04-state-census-manifest.json",
        "--format", "json",
    ], cwd=ROOT, capture_output=True)
    assert default_result.returncode == 2 and default_result.stderr == b""
    default_report = json.loads(default_result.stdout)
    assert default_report == {
        **report,
        "errors": ["unclassified_candidates"],
    }
    assert default_result.stdout == (
        census.canonical_bytes(default_report, sort_keys=True) + b"\n"
    )
