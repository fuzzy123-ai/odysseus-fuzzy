#!/usr/bin/env python3
"""Fail-closed, content-free H1R-04 repository state census."""

from __future__ import annotations

import ast
import copy
import hashlib
import keyword
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import unicodedata
from typing import Any, Callable, Iterable, Sequence


BASE_ARCHITECTURE = "docs/plans/evidence/abc-h1r04-currentness-acceptance-architecture-20260904.json"
BASE_ARCHITECTURE_BLOB = "0d8a29f451b0d971b059851a704ec302b77a44f4"
CORRECTION_ARCHITECTURE = "docs/plans/evidence/abc-h1r04-currentness-acceptance-architecture-r2-20260904.json"
CORRECTION_ARCHITECTURE_BLOB = "dc322b1e46e1d1db50a0fd9afba7f9ec443c5289"
PORTABLE_ARCHITECTURE = "docs/plans/evidence/abc-h1r04-currentness-acceptance-architecture-r3-20260904.json"
PORTABLE_ARCHITECTURE_BLOB = "c722ba51605396aab1f7ccc63b8fc66d3b619f5b"
AST_ARCHITECTURE_R1 = "docs/plans/evidence/abc-h1r04-ast-caller-discovery-architecture-20260904.json"
AST_ARCHITECTURE_R1_BLOB = "d9f8638735c9af7b832c89fccb786d3556d6857e"
AST_ARCHITECTURE_R2 = "docs/plans/evidence/abc-h1r04-ast-caller-discovery-architecture-r2-20260904.json"
AST_ARCHITECTURE_R2_BLOB = "59c6f1dc3934b77032658ae1664cf6fa2b71de74"
AST_ARCHITECTURE_R3 = "docs/plans/evidence/abc-h1r04-ast-caller-discovery-architecture-r3-20260904.json"
AST_ARCHITECTURE_R3_BLOB = "9a853ac835a8fd8529a9b9ce57c23c04c52823ce"
AST_ARCHITECTURE_R4 = "docs/plans/evidence/abc-h1r04-ast-caller-discovery-architecture-r4-20260904.json"
AST_ARCHITECTURE_R4_BLOB = "ab1008ce5ec0892da34696eb7f7bfaee2965c688"
DISCOVERY_KIND = "harbour_one.h1r04_state_census_discovery_report"
VALIDATION_KIND = "harbour_one.h1r04_state_census_validation_report"
INVALID_KIND = "harbour_one.h1r04_state_census_invalid_invocation_report"
EMPTY_DIGEST = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
REPARSE_POINT = 0x400

ERROR_CODES = {
    "input_invalid", "limit_exceeded", "git_inventory_failed",
    "unknown_untracked_root", "path_invalid", "path_collision",
    "reparse_or_symlink", "not_regular", "outside_repository", "unreadable",
    "non_utf8", "binary_candidate", "toctou_changed", "manifest_mismatch",
    "identity_mismatch", "control_plane_record_invalid", "unclassified_candidates",
}


class AuditFailure(Exception):
    def __init__(self, *codes: str):
        clean = sorted({code for code in codes if code in ERROR_CODES})
        super().__init__(clean[0] if clean else "input_invalid")
        self.codes = clean or ["input_invalid"]


class DuplicateKey(ValueError):
    pass


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(raw: bytes) -> Any:
    if b"\x00" in raw:
        raise AuditFailure("binary_candidate")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditFailure("non_utf8") from exc
    try:
        return json.loads(text, object_pairs_hook=_pairs_no_duplicates)
    except (json.JSONDecodeError, DuplicateKey) as exc:
        raise AuditFailure("input_invalid") from exc


def canonical_bytes(value: Any, *, sort_keys: bool) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=sort_keys, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: Any, *, sort_keys: bool) -> str:
    return hashlib.sha256(canonical_bytes(value, sort_keys=sort_keys)).hexdigest()


def raw_blob(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\x00"
    return hashlib.sha1(header + data).hexdigest()


def member_digest(entries: Sequence[dict[str, str]]) -> str:
    members = [[entry["path"], entry["raw_blob"]] for entry in entries]
    return sha256_json(members, sort_keys=False)


def _ascii_fold(value: str) -> str:
    return value.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _normalized_posix(value: str, max_bytes: int) -> str:
    if not value or value in (".", "..") or "\\" in value or ":" in value:
        raise AuditFailure("path_invalid")
    if len(value.encode("utf-8")) > max_bytes:
        raise AuditFailure("limit_exceeded")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise AuditFailure("path_invalid")
    normalized = pure.as_posix()
    if normalized != value:
        raise AuditFailure("path_invalid")
    return normalized


def _git_records(raw: bytes, max_path_bytes: int, *, directory_markers: bool = False) -> list[str]:
    pieces = raw.split(b"\x00")
    if pieces and pieces[-1] == b"":
        pieces.pop()
    if any(piece == b"" for piece in pieces):
        raise AuditFailure("git_inventory_failed")
    result: list[str] = []
    for piece in pieces:
        try:
            value = piece.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuditFailure("git_inventory_failed") from exc
        marker = directory_markers and value.endswith("/")
        body = value[:-1] if marker else value
        normalized = _normalized_posix(body, max_path_bytes)
        result.append(normalized + ("/" if marker else ""))
    return result


def _run_git(root: Path, argv: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            list(argv), cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AuditFailure("git_inventory_failed") from exc
    if completed.returncode != 0 or completed.stderr != b"":
        raise AuditFailure("git_inventory_failed")
    return completed.stdout


def _is_reparse_or_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        int(getattr(info, "st_file_attributes", 0)) & REPARSE_POINT
    )


def _cross_view_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(info.st_mode), int(info.st_dev),
        int(info.st_ino) if int(info.st_ino) else 0, int(info.st_size),
        int(info.st_mtime_ns), int(getattr(info, "st_file_attributes", 0)),
    )


def _snapshot_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (*_cross_view_identity(info), int(getattr(info, "st_ctime_ns", 0)))


def _same_initial_file(path_snapshot: tuple[int, ...], descriptor: os.stat_result) -> bool:
    descriptor_snapshot = _snapshot_identity(descriptor)
    if descriptor_snapshot[:-1] != path_snapshot[:-1]:
        return False
    return os.name == "nt" or descriptor_snapshot[-1] == path_snapshot[-1]


def _component_snapshot(
    root: Path, relative: str, *, missing_code: str = "unreadable"
) -> list[tuple[Path, tuple[int, ...]]]:
    current = root
    snapshots: list[tuple[Path, tuple[int, ...]]] = []
    for component in ("", *PurePosixPath(relative).parts):
        if component:
            current = current / component
        try:
            info = os.lstat(current)
        except FileNotFoundError as exc:
            raise AuditFailure(missing_code) from exc
        except OSError as exc:
            raise AuditFailure("unreadable") from exc
        if _is_reparse_or_link(info):
            raise AuditFailure("reparse_or_symlink")
        snapshots.append((current, _snapshot_identity(info)))
    return snapshots


def read_safe_file(
    root: Path,
    relative: str,
    max_bytes: int,
    *,
    open_file: Callable[[os.PathLike[str] | str, int], int] = os.open,
    read_chunk: Callable[[int, int], bytes] = os.read,
) -> bytes:
    before = _component_snapshot(root, relative)
    final = before[-1][0]
    if not stat.S_ISREG(before[-1][1][0]):
        raise AuditFailure("not_regular")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_final = final.resolve(strict=True)
        if os.path.commonpath((str(resolved_root), str(resolved_final))) != str(resolved_root):
            raise AuditFailure("outside_repository")
    except AuditFailure:
        raise
    except OSError as exc:
        raise AuditFailure("unreadable") from exc
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOINHERIT", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = open_file(final, flags)
    except OSError as exc:
        raise AuditFailure("unreadable") from exc
    data = bytearray()
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise AuditFailure("not_regular")
        if not _same_initial_file(before[-1][1], opened):
            raise AuditFailure("toctou_changed")
        opened_identity = _snapshot_identity(opened)
        while True:
            chunk = read_chunk(fd, min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise AuditFailure("limit_exceeded")
        if _snapshot_identity(os.fstat(fd)) != opened_identity:
            raise AuditFailure("toctou_changed")
    except AuditFailure:
        raise
    except OSError as exc:
        raise AuditFailure("unreadable") from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    after = _component_snapshot(root, relative)
    if [(str(path), ident) for path, ident in after] != [
        (str(path), ident) for path, ident in before
    ]:
        raise AuditFailure("toctou_changed")
    return bytes(data)


def effective_architecture(
    base: Any, correction: Any, portable: Any,
    ast_r1: Any, ast_r2: Any, ast_r3: Any, ast_r4: Any,
) -> dict[str, Any]:
    try:
        layers = (base, correction, portable, ast_r1, ast_r2, ast_r3, ast_r4)
        if any(type(layer) is not dict for layer in layers):
            raise AuditFailure("identity_mismatch")
        supersedes = correction["supersedes_acceptance_of"]
        boundary = correction["boundary_contract"]
        patch = correction["effective_contract_patch"]
        if supersedes.get("path") != BASE_ARCHITECTURE:
            raise AuditFailure("identity_mismatch")
        if supersedes.get("raw_blob") != BASE_ARCHITECTURE_BLOB:
            raise AuditFailure("identity_mismatch")
        if boundary["existing_self_exclusions_exact"] != base["manifest_contract"]["infrastructure_exclusions_exact"]:
            raise AuditFailure("identity_mismatch")
        if patch["manifest_infrastructure_exclusions_exact"] != boundary["existing_self_exclusions_exact"]:
            raise AuditFailure("identity_mismatch")
        if patch["manifest_control_plane_exclusions_exact"] != boundary["control_plane_records_exact"]:
            raise AuditFailure("identity_mismatch")
        portable_supersedes = portable["supersedes"]
        portable_patch = portable["effective_contract_patch"]
        portable_boundary = portable["portable_identity_contract"]
        if portable_supersedes.get("path") != CORRECTION_ARCHITECTURE:
            raise AuditFailure("identity_mismatch")
        if portable_supersedes.get("raw_blob") != CORRECTION_ARCHITECTURE_BLOB:
            raise AuditFailure("identity_mismatch")
        if portable_patch.get("census_contract_addition_key") != "portable_identity_boundary":
            raise AuditFailure("identity_mismatch")
        if portable_patch.get("census_contract_addition_value_source") != "portable_identity_contract":
            raise AuditFailure("identity_mismatch")
        census_contract = dict(base["census_contract"])
        if ast_r2["supersedes"].get("path") != AST_ARCHITECTURE_R1:
            raise AuditFailure("identity_mismatch")
        if ast_r2["supersedes"].get("raw_blob") != AST_ARCHITECTURE_R1_BLOB:
            raise AuditFailure("identity_mismatch")
        if ast_r3["supersedes"].get("path") != AST_ARCHITECTURE_R2:
            raise AuditFailure("identity_mismatch")
        if ast_r3["supersedes"].get("raw_blob") != AST_ARCHITECTURE_R2_BLOB:
            raise AuditFailure("identity_mismatch")
        if ast_r4["supersedes"].get("path") != AST_ARCHITECTURE_R3:
            raise AuditFailure("identity_mismatch")
        if ast_r4["supersedes"].get("raw_blob") != AST_ARCHITECTURE_R3_BLOB:
            raise AuditFailure("identity_mismatch")
        semantic = copy.deepcopy(ast_r2["corrected_semantic_contract"])
        r2_patch = ast_r2["effective_contract_patch"]
        r4_patch = ast_r4["effective_contract_patch"]
        if r4_patch.get("base_value_source") != "R2.corrected_semantic_contract":
            raise AuditFailure("identity_mismatch")
        if r4_patch.get("base_value_raw_blob") != AST_ARCHITECTURE_R2_BLOB:
            raise AuditFailure("identity_mismatch")
        if r4_patch.get("value_source") != "field_values":
            raise AuditFailure("identity_mismatch")
        if set(r4_patch) != {"base_value_source", "base_value_raw_blob", "replacement_paths_exact", "addition_paths_exact", "value_source", "construction_rule"}:
            raise AuditFailure("identity_mismatch")
        if r2_patch.get("census_contract_addition_key") != "semantic_python_discovery_boundary":
            raise AuditFailure("identity_mismatch")
        if r2_patch.get("census_contract_addition_value_source") != "corrected_semantic_contract":
            raise AuditFailure("identity_mismatch")
        if list(semantic) != r2_patch["value_top_level_keys_exact"]:
            raise AuditFailure("identity_mismatch")
        replacements = r4_patch["replacement_paths_exact"]
        additions = r4_patch["addition_paths_exact"]
        values = ast_r4["field_values"]
        if replacements != [
            "module_graph.relative_rule", "binding_grammar.trusted_alias_rule"
        ] or additions != ["module_graph.package_initializer_execution_rule"]:
            raise AuditFailure("identity_mismatch")
        if set(values) != set(replacements) | set(additions):
            raise AuditFailure("identity_mismatch")
        for dotted in replacements:
            section, field = dotted.split(".")
            if type(semantic.get(section, {}).get(field)) is not str:
                raise AuditFailure("identity_mismatch")
            semantic[section][field] = values[dotted]
        for dotted in additions:
            section, field = dotted.split(".")
            if field in semantic.get(section, {}):
                raise AuditFailure("identity_mismatch")
            semantic[section][field] = values[dotted]
        census_contract[patch["census_contract_addition_key"]] = boundary
        census_contract[portable_patch["census_contract_addition_key"]] = portable_boundary
        manifest_contract = dict(base["manifest_contract"])
        manifest_contract["top_level_fields_exact"] = patch["manifest_top_level_fields_exact"]
        census_contract[r2_patch["census_contract_addition_key"]] = semantic
        manifest_contract["fixed_values"] = patch["manifest_fixed_values"]
        manifest_contract["infrastructure_exclusions_exact"] = patch["manifest_infrastructure_exclusions_exact"]
        manifest_contract["control_plane_exclusions_exact"] = patch["manifest_control_plane_exclusions_exact"]
        result = dict(base)
        result["census_contract"] = census_contract
        result["manifest_contract"] = manifest_contract
        result["semantic_positive_ground_truth"] = list(ast_r2["current_positive_ground_truth"])
        return result
    except AuditFailure:
        raise
    except (KeyError, TypeError, AttributeError):
        raise AuditFailure("identity_mismatch") from None


def load_effective_architecture(
    root: Path,
    *,
    reader: Callable[[Path, str, int], bytes] | None = None,
) -> dict[str, Any]:
    reader = reader or read_safe_file
    base_raw = reader(root, BASE_ARCHITECTURE, 4_194_304)
    if raw_blob(base_raw) != BASE_ARCHITECTURE_BLOB:
        raise AuditFailure("identity_mismatch")
    correction_raw = reader(root, CORRECTION_ARCHITECTURE, 4_194_304)
    if raw_blob(correction_raw) != CORRECTION_ARCHITECTURE_BLOB:
        raise AuditFailure("identity_mismatch")
    portable_raw = reader(root, PORTABLE_ARCHITECTURE, 4_194_304)
    if raw_blob(portable_raw) != PORTABLE_ARCHITECTURE_BLOB:
        raise AuditFailure("identity_mismatch")
    ast_raws: list[bytes] = []
    for relative, expected in (
        (AST_ARCHITECTURE_R1, AST_ARCHITECTURE_R1_BLOB),
        (AST_ARCHITECTURE_R2, AST_ARCHITECTURE_R2_BLOB),
        (AST_ARCHITECTURE_R3, AST_ARCHITECTURE_R3_BLOB),
        (AST_ARCHITECTURE_R4, AST_ARCHITECTURE_R4_BLOB),
    ):
        raw = reader(root, relative, 4_194_304)
        if raw_blob(raw) != expected:
            raise AuditFailure("identity_mismatch")
        ast_raws.append(raw)
    return effective_architecture(
        load_json(base_raw),
        load_json(correction_raw),
        load_json(portable_raw),
        *(load_json(raw) for raw in ast_raws),
    )


def _validate_control_plane_record(
    root: Path,
    relative: str,
    boundary: dict[str, Any],
    *,
    reader: Callable[[Path, str, int], bytes] = read_safe_file,
) -> None:
    before = _component_snapshot(root, relative, missing_code="identity_mismatch")
    mode = before[-1][1][0]
    if not stat.S_ISREG(mode):
        raise AuditFailure("not_regular")
    if mode & 0o111:
        raise AuditFailure("control_plane_record_invalid")
    data = reader(root, relative, 4_194_304)
    after = _component_snapshot(root, relative, missing_code="identity_mismatch")
    if [(str(path), ident) for path, ident in before] != [
        (str(path), ident) for path, ident in after
    ]:
        raise AuditFailure("toctou_changed")
    if data.startswith(b"#!"):
        raise AuditFailure("control_plane_record_invalid")
    try:
        value = load_json(data)
    except AuditFailure as exc:
        if set(exc.codes).issubset({"input_invalid", "binary_candidate", "non_utf8"}):
            raise AuditFailure("control_plane_record_invalid") from None
        raise
    schema = boundary["control_plane_schema"]
    if not _exact_keys(value, schema["top_level_fields_exact"]):
        raise AuditFailure("control_plane_record_invalid")
    if any(
        type(value.get(key)) is not type(expected) or value[key] != expected
        for key, expected in schema["fixed_values"].items()
    ):
        raise AuditFailure("control_plane_record_invalid")
    integers = ("revision", "max_parallel_workers", "heartbeat_interval_minutes")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in integers):
        raise AuditFailure("control_plane_record_invalid")
    arrays = ("active_claims", "agent_leases", "evidence_reviews", "reflect_assessments", "correction_history")
    if any(type(value.get(key)) is not list for key in arrays):
        raise AuditFailure("control_plane_record_invalid")
    if any(type(value.get(key)) is not dict for key in ("readiness", "route", "initial_packet")):
        raise AuditFailure("control_plane_record_invalid")
    strings = (
        "scope", "authorization", "started_at", "deadline_at", "heartbeat_policy",
        "resume_cursor", "last_durable_progress_at",
    )
    if any(type(value.get(key)) is not str or not value[key].strip() for key in strings):
        raise AuditFailure("control_plane_record_invalid")
    if value.get("state") not in schema["state_enum"]:
        raise AuditFailure("control_plane_record_invalid")


def _operational_directory(root: Path, name: str) -> None:
    try:
        info = os.lstat(root / name)
    except OSError as exc:
        raise AuditFailure("unknown_untracked_root") from exc
    if _is_reparse_or_link(info):
        raise AuditFailure("reparse_or_symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise AuditFailure("unknown_untracked_root")


def _validate_directory(root: Path, relative: str | None = None) -> None:
    snapshots = _component_snapshot(root, relative or "")
    if not stat.S_ISDIR(snapshots[-1][1][0]):
        raise AuditFailure("not_regular")


def _validate_regular_path(
    root: Path, relative: str, *, missing_code: str = "unreadable"
) -> None:
    snapshots = _component_snapshot(root, relative, missing_code=missing_code)
    if not stat.S_ISREG(snapshots[-1][1][0]):
        raise AuditFailure("not_regular")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_final = snapshots[-1][0].resolve(strict=True)
    except OSError as exc:
        raise AuditFailure("unreadable") from exc
    if os.path.commonpath((str(resolved_root), str(resolved_final))) != str(resolved_root):
        raise AuditFailure("outside_repository")


def inventory_paths(
    root: Path, contract: dict[str, Any],
    *, runner: Callable[[Path, Sequence[str]], bytes] = _run_git,
) -> list[str]:
    return list(_inventory_snapshot(root, contract, runner=runner)["paths"])


def _inventory_snapshot(
    root: Path, contract: dict[str, Any],
    *, runner: Callable[[Path, Sequence[str]], bytes] = _run_git,
) -> dict[str, list[str]]:
    limit = contract["limits"]["max_path_utf8_bytes"]
    _validate_directory(root)
    tracked = _git_records(runner(root, contract["git_tracked_argv"]), limit)
    summary = _git_records(
        runner(root, contract["git_untracked_summary_argv"]), limit,
        directory_markers=True,
    )
    configured = set(contract["configured_untracked_roots_exact"])
    operational = tuple(contract["operational_untracked_directory_prefixes_exact"])
    root_files: list[str] = []
    for record in summary:
        directory = record.endswith("/")
        value = record[:-1] if directory else record
        first = value.split("/", 1)[0]
        if not directory and "/" not in value:
            root_files.append(value)
        elif first in configured:
            continue
        elif directory and any(first.startswith(prefix) for prefix in operational):
            _operational_directory(root, first)
        else:
            raise AuditFailure("unknown_untracked_root")
    for configured_root in sorted(configured, key=lambda item: item.encode("utf-8")):
        _validate_directory(root, configured_root)
    expanded = _git_records(runner(root, contract["git_untracked_argv"]), limit)
    paths = sorted(set(tracked) | set(expanded) | set(root_files), key=lambda p: p.encode("utf-8"))
    if len(paths) > contract["limits"]["max_files"]:
        raise AuditFailure("limit_exceeded")
    identities: dict[str, str] = {}
    for path in paths:
        folded = unicodedata.normalize("NFKC", path).casefold()
        previous = identities.setdefault(folded, path)
        if previous != path:
            raise AuditFailure("path_collision")
    return {
        "tracked": tracked,
        "untracked_summary": summary,
        "untracked_expanded": expanded,
        "paths": paths,
    }


def _decode_candidate(data: bytes) -> str:
    if b"\x00" in data:
        raise AuditFailure("binary_candidate")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditFailure("non_utf8") from exc





_SCOPE_TYPES = (
    ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


def _python_module_record(path: str) -> tuple[str, str, bool] | None:
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    parts = list(pure.parts)
    stem = pure.stem
    is_init = stem == "__init__"
    module_parts = parts[:-1] if is_init else [*parts[:-1], stem]
    if not module_parts or any(
        not part.isidentifier() or keyword.iskeyword(part)
        for part in module_parts
    ):
        return None
    module = ".".join(module_parts)
    package = module if is_init else ".".join(module_parts[:-1])
    return module, package, is_init


def _semantic_source_paths(paths: Sequence[str], boundary: dict[str, Any]) -> list[str]:
    roots = set(boundary["python_inventory"]["source_roots_exact"])
    selected = []
    for path in paths:
        pure = PurePosixPath(path)
        if pure.suffix != boundary["python_inventory"]["source_suffix_exact"]:
            continue
        if len(pure.parts) == 1 or pure.parts[0] in roots:
            selected.append(path)
    return sorted(selected, key=lambda value: value.encode("utf-8"))


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _target_names(item)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, ast.MatchAs) and target.name:
        return [target.name]
    if isinstance(target, ast.MatchStar) and target.name:
        return [target.name]
    return []


class _ScopeIndex(ast.NodeVisitor):
    def __init__(self, tree: ast.Module):
        self.stack: list[ast.AST] = [tree]
        self.node_scope: dict[int, ast.AST] = {}
        self.parent_scope: dict[int, ast.AST | None] = {id(tree): None}
        self.scopes: dict[int, ast.AST] = {id(tree): tree}
        self.parents: dict[int, ast.AST] = {}
        self.binders: dict[int, dict[str, int]] = {id(tree): {}}
        self.imports: dict[int, dict[str, tuple[str, str]]] = {id(tree): {}}
        self.import_kinds: dict[int, dict[str, set[str]]] = {id(tree): {}}
        self.global_nonlocal: dict[int, set[str]] = {id(tree): set()}
        self.visit(tree)

    def _bind(self, scope: ast.AST, name: str) -> None:
        table = self.binders.setdefault(id(scope), {})
        table[name] = table.get(name, 0) + 1

    def _child(self, parent: ast.AST, child: ast.AST) -> None:
        self.parents[id(child)] = parent
        self.visit(child)

    def generic_visit(self, node: ast.AST) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        for child in ast.iter_child_nodes(node):
            self._child(node, child)

    def _enter_scope(self, node: ast.AST, name: str | None = None) -> None:
        outer = self.stack[-1]
        self.node_scope[id(node)] = outer
        if name:
            self._bind(outer, name)
        self.parent_scope[id(node)] = outer
        self.scopes[id(node)] = node
        self.binders.setdefault(id(node), {})
        self.imports.setdefault(id(node), {})
        self.import_kinds.setdefault(id(node), {})
        self.global_nonlocal.setdefault(id(node), set())
        self.stack.append(node)

    def _bind_arguments(self, scope: ast.AST, arguments: ast.arguments) -> None:
        for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
            self._bind(scope, arg.arg)
        if arguments.vararg:
            self._bind(scope, arguments.vararg.arg)
        if arguments.kwarg:
            self._bind(scope, arguments.kwarg.arg)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        outer_values: list[ast.AST] = [
            *node.decorator_list,
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ]
        for arg in (
            *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
            node.args.vararg, node.args.kwarg,
        ):
            if arg is not None and arg.annotation is not None:
                outer_values.append(arg.annotation)
        if node.returns is not None:
            outer_values.append(node.returns)
        outer_values.extend(getattr(node, "type_params", ()))
        self.node_scope[id(node)] = self.stack[-1]
        self._bind(self.stack[-1], node.name)
        for value in outer_values:
            self._child(node, value)
        self._enter_scope(node)
        self._bind_arguments(node, node.args)
        for statement in node.body:
            self._child(node, statement)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        self._bind(self.stack[-1], node.name)
        for value in (
            *node.decorator_list, *node.bases,
            *(keyword_node.value for keyword_node in node.keywords),
            *getattr(node, "type_params", ()),
        ):
            self._child(node, value)
        self._enter_scope(node)
        for statement in node.body:
            self._child(node, statement)
        self.stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        for value in (
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ):
            self._child(node, value)
        self._enter_scope(node)
        self._bind_arguments(node, node.args)
        self._child(node, node.body)
        self.stack.pop()

    def _visit_comprehension(self, node: ast.AST, values: Sequence[ast.AST]) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        generators = node.generators
        first = generators[0]
        self.parents[id(first)] = node
        self.node_scope[id(first)] = self.stack[-1]
        self._child(first, first.iter)
        self._enter_scope(node)
        for position, generator in enumerate(generators):
            self.parents[id(generator)] = node
            self.node_scope[id(generator)] = node
            if position:
                self._child(generator, generator.iter)
            self._child(generator, generator.target)
            for condition in generator.ifs:
                self._child(generator, condition)
        for value in values:
            self._child(node, value)
        self.stack.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, (node.elt,))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        target_scope = self.stack[-1]
        while isinstance(
            target_scope,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            parent = self.parent_scope.get(id(target_scope))
            if parent is None:
                break
            target_scope = parent
        self.parents[id(node.target)] = node
        self.node_scope[id(node.target)] = target_scope
        self._bind(target_scope, node.target.id)
        self._child(node, node.value)

    def visit_Name(self, node: ast.Name) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        if isinstance(node.ctx, ast.Store):
            self._bind(self.stack[-1], node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.node_scope[id(node)] = self.stack[-1]

    def visit_Import(self, node: ast.Import) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        scope = self.stack[-1]
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            self._bind(scope, bound)
            kind = (
                "importlib_module" if alias.name == "importlib"
                else "typing_module" if alias.name == "typing"
                else "other"
            )
            self.imports[id(scope)][bound] = (kind, alias.name)
            self.import_kinds[id(scope)].setdefault(bound, set()).add(kind)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        scope = self.stack[-1]
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            self._bind(scope, bound)
            kind = (
                "import_module_function"
                if node.level == 0 and node.module == "importlib" and alias.name == "import_module"
                else "type_checking"
                if node.level == 0 and node.module == "typing" and alias.name == "TYPE_CHECKING"
                else "other"
            )
            self.imports[id(scope)][bound] = (kind, f"{node.module or ''}.{alias.name}")
            self.import_kinds[id(scope)].setdefault(bound, set()).add(kind)

    def visit_Global(self, node: ast.Global) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        self.global_nonlocal[id(self.stack[-1])].update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        self.global_nonlocal[id(self.stack[-1])].update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        if node.name:
            self._bind(self.stack[-1], node.name)
        if node.type is not None:
            self._child(node, node.type)
        for statement in node.body:
            self._child(node, statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        if node.name:
            self._bind(self.stack[-1], node.name)
        if node.pattern is not None:
            self._child(node, node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self.node_scope[id(node)] = self.stack[-1]
        if node.name:
            self._bind(self.stack[-1], node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self.generic_visit(node)
        if node.rest:
            self._bind(self.stack[-1], node.rest)

    def trusted(self, scope: ast.AST, name: str, kind: str) -> bool:
        return (
            self.binders.get(id(scope), {}).get(name) == 1
            and kind in self.import_kinds.get(id(scope), {}).get(name, set())
            and name not in self.global_nonlocal.get(id(scope), set())
        )

    def outer_import(self, scope: ast.AST, name: str, kind: str) -> bool:
        parent = self.parent_scope.get(id(scope))
        while parent is not None:
            if kind in self.import_kinds.get(id(parent), {}).get(name, set()):
                return True
            parent = self.parent_scope.get(id(parent))
        return False

    def builtin_unshadowed(self, scope: ast.AST, name: str) -> bool:
        current: ast.AST | None = scope
        while current is not None:
            if self.binders.get(id(current), {}).get(name, 0):
                return False
            if name in self.global_nonlocal.get(id(current), set()):
                return False
            current = self.parent_scope.get(id(current))
        return True


def _semantic_candidates(
    paths: Sequence[str],
    sources: dict[str, bytes],
    boundary: dict[str, Any],
    seed_paths: set[str],
) -> set[str]:
    records = {path: _python_module_record(path) for path in sources}
    module_paths: dict[str, str] = {}
    packages: set[str] = set()
    file_packages: set[str] = set()
    nonimportable = {path for path, record in records.items() if record is None}
    for path, record in records.items():
        if record is None:
            continue
        module, _package, is_init = record
        if module in module_paths and module_paths[module] != path:
            raise AuditFailure("identity_mismatch")
        module_paths[module] = path
        if is_init:
            file_packages.add(module)
        components = module.split(".")
        packages.update(".".join(components[:index]) for index in range(1, len(components)))
    nodes = set(module_paths) | packages
    trees: dict[str, ast.Module] = {}
    uncertain: set[str] = set()
    for path, record in records.items():
        try:
            tree = ast.parse(_decode_candidate(sources[path]), filename=path)
        except (SyntaxError, RecursionError, MemoryError):
            if record is not None:
                uncertain.add(record[0])
            continue
        trees[path] = tree

    edges: set[tuple[str, str]] = set()

    def add_target(importer: str, target: str, sink: set[tuple[str, str]]) -> None:
        if target not in nodes:
            return
        sink.add((importer, target))
        components = target.split(".")
        for index in range(1, len(components)):
            ancestor = ".".join(components[:index])
            if ancestor in file_packages:
                sink.add((importer, ancestor))

    for path, tree in trees.items():
        record = records[path]
        if record is None:
            continue
        importer, package_context, _is_init = record
        try:
            index = _ScopeIndex(tree)
            walked = list(ast.walk(tree))
        except (RecursionError, MemoryError):
            uncertain.add(importer)
            continue
        relevant_kinds = {"typing_module", "type_checking", "importlib_module", "import_module_function"}
        for scope_id, names in index.import_kinds.items():
            for name, kinds in names.items():
                if not (kinds & relevant_kinds):
                    continue
                if (
                    index.binders.get(scope_id, {}).get(name) != 1
                    or name in index.global_nonlocal.get(scope_id, set())
                ):
                    uncertain.add(importer)
        for scope_id, names in index.global_nonlocal.items():
            scope_node = index.scopes[scope_id]
            for name in names:
                if any(
                    index.outer_import(scope_node, name, kind)
                    for kind in relevant_kinds
                ):
                    uncertain.add(importer)

        type_only_aliases: set[tuple[int, str, str]] = set()

        def record_type_only_alias(type_node: ast.AST, sink: set[tuple[int, str, str]]) -> None:
            scope = index.node_scope.get(id(type_node), tree)
            if isinstance(type_node, ast.Import):
                for alias in type_node.names:
                    kind = (
                        "importlib_module" if alias.name == "importlib"
                        else "typing_module" if alias.name == "typing"
                        else None
                    )
                    if kind is not None:
                        bound = alias.asname or alias.name.split(".", 1)[0]
                        sink.add((id(scope), bound, kind))
            elif isinstance(type_node, ast.ImportFrom) and type_node.level == 0:
                for alias in type_node.names:
                    kind = (
                        "import_module_function"
                        if type_node.module == "importlib" and alias.name == "import_module"
                        else "type_checking"
                        if type_node.module == "typing" and alias.name == "TYPE_CHECKING"
                        else None
                    )
                    if kind is not None:
                        bound = alias.asname or alias.name
                        sink.add((id(scope), bound, kind))

        def trusted_runtime(scope: ast.AST, name: str, kind: str) -> bool:
            return (
                index.trusted(scope, name, kind)
                and (id(scope), name, kind) not in type_only_aliases
            )

        try:
            while True:
                type_only: set[int] = set()
                next_aliases = set(type_only_aliases)
                pass_uncertain = False
                for candidate in walked:
                    if not isinstance(candidate, ast.If) or id(candidate) in type_only:
                        continue
                    scope = index.node_scope.get(id(candidate), tree)
                    test = candidate.test
                    negative = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    atom = test.operand if negative else test
                    trusted = False
                    if isinstance(atom, ast.Name):
                        trusted = trusted_runtime(scope, atom.id, "type_checking")
                    elif (
                        isinstance(atom, ast.Attribute)
                        and atom.attr == "TYPE_CHECKING"
                        and isinstance(atom.value, ast.Name)
                    ):
                        trusted = trusted_runtime(scope, atom.value.id, "typing_module")
                    test_nodes = list(ast.walk(test))
                    referenced = False
                    for test_node in test_nodes:
                        if isinstance(test_node, ast.Name):
                            local_kinds = index.import_kinds.get(id(scope), {}).get(test_node.id, set())
                            referenced = referenced or test_node.id == "TYPE_CHECKING"
                            referenced = referenced or "type_checking" in local_kinds
                            referenced = referenced or index.outer_import(scope, test_node.id, "type_checking")
                        elif isinstance(test_node, ast.Attribute) and test_node.attr == "TYPE_CHECKING":
                            referenced = True
                    if trusted:
                        branch = candidate.orelse if negative else candidate.body
                        type_only.update(
                            id(descendant)
                            for statement in branch
                            for descendant in ast.walk(statement)
                        )
                        for type_node in walked:
                            if id(type_node) in type_only:
                                record_type_only_alias(type_node, next_aliases)
                    elif referenced:
                        pass_uncertain = True
                if pass_uncertain:
                    uncertain.add(importer)
                if next_aliases == type_only_aliases:
                    break
                type_only_aliases = next_aliases
        except (RecursionError, MemoryError):
            uncertain.add(importer)
            continue

        def is_loader_callee(reference: ast.AST) -> bool:
            parent = index.parents.get(id(reference))
            if isinstance(parent, ast.Call) and parent.func is reference:
                return True
            if (
                isinstance(reference, ast.Name)
                and isinstance(parent, ast.Attribute)
                and parent.value is reference
                and parent.attr == "import_module"
            ):
                grandparent = index.parents.get(id(parent))
                return isinstance(grandparent, ast.Call) and grandparent.func is parent
            return False

        source_edges: set[tuple[str, str]] = set()
        resource_failed = False
        for node in walked:
            try:
                if id(node) in type_only:
                    continue
                scope = index.node_scope.get(id(node), tree)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    local_kinds = index.import_kinds.get(id(scope), {}).get(node.id, set())
                    local_dynamic = bool(
                        local_kinds & {"importlib_module", "import_module_function"}
                    )
                    outer_dynamic = any(
                        index.outer_import(scope, node.id, kind)
                        for kind in ("importlib_module", "import_module_function")
                    )
                    builtin_loader = (
                        node.id == "__import__"
                        and index.builtin_unshadowed(scope, node.id)
                    )
                    trusted_dynamic = (
                        trusted_runtime(scope, node.id, "importlib_module")
                        or trusted_runtime(scope, node.id, "import_module_function")
                        or builtin_loader
                    )
                    if (
                        (local_dynamic or outer_dynamic or builtin_loader)
                        and (not trusted_dynamic or not is_loader_callee(node))
                    ):
                        uncertain.add(importer)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        add_target(importer, alias.name, source_edges)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        if not package_context:
                            raise AuditFailure("identity_mismatch")
                        components = package_context.split(".")
                        ascents = node.level - 1
                        if ascents >= len(components):
                            raise AuditFailure("identity_mismatch")
                        base_parts = components[:len(components) - ascents]
                        if node.module:
                            base_parts.extend(node.module.split("."))
                        base = ".".join(base_parts)
                    else:
                        base = node.module or ""
                    for alias in node.names:
                        target = base
                        if alias.name != "*" and f"{base}.{alias.name}" in nodes:
                            target = f"{base}.{alias.name}"
                        add_target(importer, target, source_edges)
                elif isinstance(node, ast.Call):
                    loader = False
                    ambiguous_loader = False
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                        ambiguous_loader = True
                        if isinstance(node.func.value, ast.Name):
                            name = node.func.value.id
                            loader = trusted_runtime(scope, name, "importlib_module")
                            if index.outer_import(scope, name, "importlib_module"):
                                uncertain.add(importer)
                    elif isinstance(node.func, ast.Name):
                        name = node.func.id
                        loader = (
                            trusted_runtime(scope, name, "import_module_function")
                            or (name == "__import__" and index.builtin_unshadowed(scope, name))
                        )
                        ambiguous_loader = (
                            name == "__import__"
                            or index.outer_import(scope, name, "import_module_function")
                            or "import_module_function" in index.import_kinds.get(id(scope), {}).get(name, set())
                        )
                    if loader:
                        literal = (
                            len(node.args) == 1 and not node.keywords
                            and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            and node.args[0].value
                            and all(
                                part.isidentifier() and not keyword.iskeyword(part)
                                for part in node.args[0].value.split(".")
                            )
                        )
                        if literal:
                            add_target(importer, node.args[0].value, source_edges)
                        else:
                            uncertain.add(importer)
                    elif ambiguous_loader:
                        uncertain.add(importer)
            except (RecursionError, MemoryError):
                resource_failed = True
                break
        if resource_failed:
            uncertain.add(importer)
            continue
        try:
            edges.update(source_edges)
        except (RecursionError, MemoryError):
            uncertain.add(importer)
            continue

    anchors = set(uncertain)
    for path in seed_paths:
        record = records.get(path)
        if record is not None:
            anchors.add(record[0])
    entrypoints = boundary["semantic_anchors"]["entrypoints_exact"]
    for entrypoint in entrypoints:
        module = entrypoint["module"]
        path = module_paths.get(module)
        tree = trees.get(path or "")
        if tree is None:
            raise AuditFailure("identity_mismatch")
        definitions = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == entrypoint["symbol"]
        ]
        if len(definitions) != 1:
            raise AuditFailure("identity_mismatch")
        anchors.add(module)

    reachable = set(anchors)
    changed = True
    while changed:
        changed = False
        for importer, target in sorted(edges, key=lambda edge: (edge[0].encode(), edge[1].encode())):
            if target in reachable and importer not in reachable:
                reachable.add(importer)
                changed = True
    candidates = set(nonimportable)
    candidates.update(module_paths[module] for module in reachable if module in module_paths)
    return candidates

def discover(
    root: Path, architecture: dict[str, Any],
    *, runner: Callable[[Path, Sequence[str]], bytes] = _run_git,
    reader: Callable[[Path, str, int], bytes] = read_safe_file,
) -> tuple[int, list[dict[str, str]]]:
    contract = architecture["census_contract"]
    limits = contract["limits"]
    exclusions = set(architecture["manifest_contract"]["infrastructure_exclusions_exact"])
    boundary = contract["control_plane_record_boundary"]
    control_plane_records = set(boundary["control_plane_records_exact"])
    mandatory = {
        item["path"]: item["raw_blob"]
        for item in architecture["mandatory_post_r3_discovery_candidates"]
    }
    seeds = {
        item["path"]: item["raw_blob"]
        for item in architecture["current_inventory_seed"]
    }
    first_inventory = _inventory_snapshot(root, contract, runner=runner)
    paths = list(first_inventory["paths"])
    required_paths = set(mandatory) | set(seeds)
    if required_paths & (exclusions | control_plane_records):
        raise AuditFailure("control_plane_record_invalid")
    if not required_paths.issubset(paths):
        raise AuditFailure("identity_mismatch")
    if not control_plane_records.issubset(paths):
        raise AuditFailure("identity_mismatch")
    for required_path in sorted(required_paths, key=lambda value: value.encode("utf-8")):
        _validate_regular_path(
            root, required_path, missing_code="identity_mismatch",
        )
    semantic_boundary = contract["semantic_python_discovery_boundary"]
    semantic_paths = _semantic_source_paths(paths, semantic_boundary)
    semantic_sources: dict[str, bytes] = {}
    for path in semantic_paths:
        _validate_regular_path(root, path)
        semantic_sources[path] = reader(root, path, limits["max_file_bytes"])
    total_text = sum(len(data) for data in semantic_sources.values())
    if total_text > limits["max_total_text_bytes"]:
        raise AuditFailure("limit_exceeded")
    semantic_candidates = _semantic_candidates(
        paths, semantic_sources, semantic_boundary, set(seeds)
    )
    positive: dict[str, str] = {}
    for pinned in architecture["semantic_positive_ground_truth"]:
        if type(pinned) is not str or "@" not in pinned:
            raise AuditFailure("identity_mismatch")
        positive_path, positive_blob = pinned.rsplit("@", 1)
        if (
            positive_path in positive
            or re.fullmatch(r"[0-9a-f]{40}", positive_blob) is None
        ):
            raise AuditFailure("identity_mismatch")
        positive[positive_path] = positive_blob
    if not set(positive).issubset(semantic_candidates):
        raise AuditFailure("identity_mismatch")
    if any(raw_blob(semantic_sources.get(path, b"")) != blob for path, blob in positive.items()):
        raise AuditFailure("identity_mismatch")
    entries: list[dict[str, str]] = []
    path_patterns = tuple(_ascii_fold(value) for value in contract["path_patterns_ascii_case_insensitive"])
    content_patterns = tuple(_ascii_fold(value) for value in contract["content_patterns_ascii_case_insensitive"])
    suffixes = set(contract["text_suffixes"])
    seen_mandatory: set[str] = set()
    seen_seeds: set[str] = set()
    seen_control_plane: set[str] = set()
    for path in paths:
        if path in exclusions:
            continue
        if path in control_plane_records:
            _validate_control_plane_record(root, path, boundary, reader=reader)
            seen_control_plane.add(path)
            continue
        _validate_regular_path(
            root, path,
            missing_code="identity_mismatch" if path in required_paths else "unreadable",
        )
        mandatory_match = path in mandatory
        semantic_match = path in semantic_candidates
        path_match = any(pattern in _ascii_fold(path) for pattern in path_patterns)
        text_suffix = PurePosixPath(path).suffix.casefold() in suffixes
        if not (mandatory_match or path_match or text_suffix):
            continue
        data = semantic_sources[path] if path in semantic_sources else reader(root, path, limits["max_file_bytes"])
        if text_suffix and path not in semantic_sources:
            total_text += len(data)
            if total_text > limits["max_total_text_bytes"]:
                raise AuditFailure("limit_exceeded")
        text = _decode_candidate(data)
        content_match = text_suffix and any(
            pattern in _ascii_fold(text) for pattern in content_patterns
        )
        if mandatory_match:
            seen_mandatory.add(path)
            if raw_blob(data) != mandatory[path]:
                raise AuditFailure("identity_mismatch")
        if path in seeds:
            seen_seeds.add(path)
            if raw_blob(data) != seeds[path]:
                raise AuditFailure("identity_mismatch")
        if mandatory_match or path_match or content_match or semantic_match:
            entries.append({"path": path, "raw_blob": raw_blob(data)})
    if seen_mandatory != set(mandatory):
        raise AuditFailure("identity_mismatch")
    if seen_seeds != set(seeds):
        raise AuditFailure("identity_mismatch")
    if seen_control_plane != control_plane_records:
        raise AuditFailure("identity_mismatch")
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    second_inventory = _inventory_snapshot(root, contract, runner=runner)
    if second_inventory != first_inventory:
        raise AuditFailure("toctou_changed")
    for path in semantic_paths:
        second = reader(root, path, limits["max_file_bytes"])
        if raw_blob(second) != raw_blob(semantic_sources[path]):
            raise AuditFailure("toctou_changed")
    if _semantic_source_paths(second_inventory["paths"], semantic_boundary) != semantic_paths:
        raise AuditFailure("toctou_changed")
    return len([
        path for path in paths
        if path not in exclusions and path not in control_plane_records
    ]), entries


def _exact_keys(value: Any, expected: Iterable[str]) -> bool:
    return type(value) is dict and set(value) == set(expected)


def validate_manifest(
    manifest: Any, architecture: dict[str, Any], discovered: list[dict[str, str]]
) -> tuple[int, int]:
    contract = architecture["manifest_contract"]
    if not _exact_keys(manifest, contract["top_level_fields_exact"]):
        raise AuditFailure("manifest_mismatch")
    if type(manifest.get("schema_version")) is not int:
        raise AuditFailure("manifest_mismatch")
    if any(type(manifest.get(field)) is not str for field in ("kind", "mode", "scan_contract_digest")):
        raise AuditFailure("manifest_mismatch")
    if type(manifest.get("infrastructure_exclusions")) is not list or any(
        type(item) is not str for item in manifest["infrastructure_exclusions"]
    ):
        raise AuditFailure("manifest_mismatch")
    if type(manifest.get("control_plane_exclusions")) is not list or any(
        type(item) is not str for item in manifest["control_plane_exclusions"]
    ):
        raise AuditFailure("manifest_mismatch")
    fixed = contract["fixed_values"]
    if any(manifest.get(key) != value for key, value in fixed.items()):
        raise AuditFailure("manifest_mismatch")
    if manifest.get("scan_contract_digest") != sha256_json(
        architecture["census_contract"], sort_keys=True
    ):
        raise AuditFailure("manifest_mismatch")
    if manifest.get("infrastructure_exclusions") != contract["infrastructure_exclusions_exact"]:
        raise AuditFailure("manifest_mismatch")
    if manifest.get("control_plane_exclusions") != contract["control_plane_exclusions_exact"]:
        raise AuditFailure("manifest_mismatch")
    entries = manifest.get("entries")
    if type(entries) is not list:
        raise AuditFailure("manifest_mismatch")
    paths: list[str] = []
    blobs: dict[str, str] = {}
    for entry in entries:
        if not _exact_keys(entry, contract["entry_fields_exact"]):
            raise AuditFailure("manifest_mismatch")
        if any(type(entry.get(field)) is not str for field in contract["entry_fields_exact"]):
            raise AuditFailure("manifest_mismatch")
        if re.fullmatch(r"[0-9a-f]{40}", entry["raw_blob"]) is None:
            raise AuditFailure("manifest_mismatch")
        _normalized_posix(entry["path"], architecture["census_contract"]["limits"]["max_path_utf8_bytes"])
        if entry["path"] in set(contract["infrastructure_exclusions_exact"]) | set(contract["control_plane_exclusions_exact"]):
            raise AuditFailure("manifest_mismatch")
        if not entry["reason"]:
            raise AuditFailure("manifest_mismatch")
        if entry["classification"] not in contract["classification_enum"]:
            raise AuditFailure("manifest_mismatch")
        if entry["owner"] not in contract["owner_enum"] or entry["writer_authority"] not in contract["writer_authority_enum"]:
            raise AuditFailure("manifest_mismatch")
        if entry["role"] not in contract["role_enum"] or entry["disposition"] not in contract["disposition_enum"]:
            raise AuditFailure("manifest_mismatch")
        paths.append(entry["path"])
        if entry["path"] in blobs:
            raise AuditFailure("manifest_mismatch")
        blobs[entry["path"]] = entry["raw_blob"]
    if paths != sorted(paths, key=lambda path: path.encode("utf-8")):
        raise AuditFailure("manifest_mismatch")
    discovered_blobs = {entry["path"]: entry["raw_blob"] for entry in discovered}
    if set(blobs) != set(discovered_blobs):
        raise AuditFailure("manifest_mismatch")
    if blobs != discovered_blobs:
        raise AuditFailure("identity_mismatch")
    seed_mapping = {item["path"]: item for item in contract["seed_disposition_mapping"]}
    unclassified = 0
    for entry in entries:
        mapping = seed_mapping.get(entry["path"])
        if mapping is not None:
            expected = {**mapping, "classification": "state_bearing"}
            if any(entry.get(key) != value for key, value in expected.items()):
                raise AuditFailure("manifest_mismatch")
        else:
            expected = {
                "classification": "unclassified", "owner": "unclassified",
                "writer_authority": "unclassified_no_writer",
                "role": "unclassified_candidate",
                "disposition": "requires_separate_reviewed_classification",
                "reason": "Requires a separate reviewed disposition decision.",
            }
            if any(entry.get(key) != value for key, value in expected.items()):
                raise AuditFailure("manifest_mismatch")
            unclassified += 1
    if set(seed_mapping) - set(blobs):
        raise AuditFailure("manifest_mismatch")
    return len(entries) - unclassified, unclassified


def discovery_report(*, status: str, files: int, entries: list[dict[str, str]], errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1, "kind": DISCOVERY_KIND, "status": status,
        "files_enumerated": files, "candidates_detected": len(entries),
        "member_set_digest": member_digest(entries), "entries": entries,
        "errors": sorted(set(errors)),
    }


def validation_report(
    *, status: str, files: int, candidates: int, classified: int,
    unclassified: int, digest: str, errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1, "kind": VALIDATION_KIND, "status": status,
        "files_enumerated": files, "candidates_detected": candidates,
        "classified_count": classified, "unclassified_count": unclassified,
        "member_set_digest": digest, "errors": sorted(set(errors)),
    }


def invalid_report() -> dict[str, Any]:
    return {
        "schema_version": 1, "kind": INVALID_KIND, "status": "invalid",
        "files_enumerated": 0, "candidates_detected": 0,
        "member_set_digest": EMPTY_DIGEST, "errors": ["input_invalid"],
    }


def _invocation(argv: Sequence[str]) -> tuple[str, str | None, bool] | None:
    if list(argv) == ["--discover", "--format", "json"]:
        return "discovery", None, False
    values = list(argv)
    if len(values) not in (4, 5) or values.count("--manifest") != 1 or values.count("--format") != 1:
        return None
    if "--discover" in values or any(value.startswith("-") and value not in {"--manifest", "--format", "--expect-unclassified"} for value in values):
        return None
    expect = "--expect-unclassified" in values
    if expect and values.count("--expect-unclassified") != 1:
        return None
    if (len(values) == 5) != expect:
        return None
    try:
        manifest = values[values.index("--manifest") + 1]
        output_format = values[values.index("--format") + 1]
    except (IndexError, ValueError):
        return None
    if not manifest or manifest.startswith("-") or output_format != "json":
        return None
    return "validation", manifest, expect


def _emit(report: dict[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_bytes(report, sort_keys=True) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    invocation = _invocation(args)
    if invocation is None:
        _emit(invalid_report())
        return 1
    mode, manifest_arg, expect_unclassified = invocation
    root = Path(os.path.abspath(__file__)).parent.parent
    try:
        architecture = load_effective_architecture(root)
        manifest = None
        if mode == "validation":
            assert manifest_arg is not None
            manifest_path = Path(manifest_arg)
            if manifest_path.is_absolute():
                raise AuditFailure("input_invalid")
            normalized_manifest = _normalized_posix(
                manifest_path.as_posix(),
                architecture["census_contract"]["limits"]["max_path_utf8_bytes"],
            )
            manifest = load_json(read_safe_file(root, normalized_manifest, 4_194_304))
        files, entries = discover(root, architecture)
        if mode == "discovery":
            _emit(discovery_report(status="complete", files=files, entries=entries, errors=[]))
            return 0
        assert manifest is not None
        classified, unclassified = validate_manifest(manifest, architecture, entries)
        digest = member_digest(entries)
        if unclassified:
            errors = [] if expect_unclassified else ["unclassified_candidates"]
            _emit(validation_report(
                status="unclassified", files=files, candidates=len(entries),
                classified=classified, unclassified=unclassified, digest=digest,
                errors=errors,
            ))
            return 0 if expect_unclassified else 2
        if expect_unclassified:
            raise AuditFailure("manifest_mismatch")
        _emit(validation_report(
            status="complete", files=files, candidates=len(entries),
            classified=classified, unclassified=0, digest=digest, errors=[],
        ))
        return 0
    except AuditFailure as exc:
        if mode == "discovery":
            report = discovery_report(status="invalid", files=0, entries=[], errors=exc.codes)
        else:
            report = validation_report(
                status="invalid", files=0, candidates=0, classified=0,
                unclassified=0, digest=EMPTY_DIGEST, errors=exc.codes,
            )
        _emit(report)
        return 1
    except Exception:
        if mode == "discovery":
            report = discovery_report(status="invalid", files=0, entries=[], errors=["input_invalid"])
        else:
            report = validation_report(
                status="invalid", files=0, candidates=0, classified=0,
                unclassified=0, digest=EMPTY_DIGEST, errors=["input_invalid"],
            )
        _emit(report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
