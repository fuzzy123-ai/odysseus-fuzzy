"""Deterministic, content-free verification receipts for maintenance work.

Receipts contain only fixed machine metadata, check identifiers and outcomes.
Raw check output, exception text, environment values and repository paths are
never accepted into the receipt. Dirty bindings are computed only after a
conservative sensitive-path and credential-assignment scan succeeds. Public
receipt metadata proves no non-forgeable origin; completion gates must re-run
validation against current repository state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence


RECEIPT_SCHEMA = "odysseus.agent_maintenance_verification_receipt.v1"
RECEIPT_PRODUCER = "odysseus.scripts.verify"
RECEIPT_PRODUCER_VERSION = 1
MAX_DIRTY_BINDING_BYTES = 32 * 1024 * 1024

_RESULT_STATES = frozenset({"passed", "failed", "unavailable", "not_verified"})
_CHECK_STATES = frozenset({"passed", "failed", "skipped", "unavailable"})
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_HEX_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_PATH_PARTS = frozenset(
    {
        ".env",
        "confidential",
        "credential",
        "credentials",
        "private",
        "private_key",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx", ".pem"})
_SENSITIVE_PATH_PREFIXES = (
    "confidential",
    "credential",
    "private",
    "secret",
    "token",
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?im)(?:api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
    rb"[\x20\t]*[:=][\x20\t]*[^\x20\t\r\n]{4,}"
)
_PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "producer",
        "binding",
        "lane",
        "result",
        "strongest_evidence_level",
        "required_checks",
        "checks",
        "passed_checks",
        "failed_checks",
        "skipped_checks",
        "unavailable_checks",
        "not_verified",
        "content_free",
        "receipt_digest",
    }
)
_PRODUCER_KEYS = frozenset({"id", "version"})
_BINDING_KEYS = frozenset(
    {"workspace_state", "head_revision", "dirty_diff_digest"}
)
_CHECK_KEYS = frozenset({"check_id", "required", "status", "evidence_level"})
_CHECK_EVIDENCE_RANK = {
    "static": 1,
    "fast": 2,
    "ui_contract": 3,
    "visual": 4,
    "full": 5,
}
_RECEIPT_EVIDENCE_LEVELS = frozenset(
    {
        "none",
        *_CHECK_EVIDENCE_RANK,
        "ui_contract_plus_visual_artifact",
    }
)


class ReceiptError(ValueError):
    """Raised when a receipt cannot be safely generated or validated."""


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    workspace_state: str
    head_revision: str
    dirty_diff_digest: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_state": self.workspace_state,
            "head_revision": self.head_revision,
            "dirty_diff_digest": self.dirty_diff_digest,
        }


def repository_binding(root: Path) -> RepositoryBinding:
    """Bind to exact HEAD or a safe deterministic dirty-tree digest."""

    head = _git_stdout(root, ("git", "rev-parse", "--verify", "HEAD")).decode(
        "ascii", errors="strict"
    ).strip()
    if _HEX_REVISION.fullmatch(head) is None:
        raise ReceiptError("repository revision is unavailable")

    status = _git_stdout(
        root,
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    if not status:
        return RepositoryBinding("clean_head", head, None)

    paths = _changed_paths_from_status(status)
    _reject_sensitive_paths(paths)
    diff = _git_stdout(
        root,
        (
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ),
        max_bytes=MAX_DIRTY_BINDING_BYTES,
    )
    chunks = [b"tracked\0", diff]
    total = len(diff)
    tracked = set(
        line.replace("\\", "/")
        for line in _git_stdout(
            root,
            ("git", "ls-files", "-z"),
            max_bytes=MAX_DIRTY_BINDING_BYTES,
        )
        .decode("utf-8", errors="strict")
        .split("\0")
        if line
    )
    for relative in paths:
        if relative in tracked:
            continue
        candidate = _safe_repo_path(root, relative)
        if not candidate.is_file():
            raise ReceiptError("untracked repository path is not a regular file")
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise ReceiptError("untracked repository path is unavailable") from exc
        total += len(relative.encode("utf-8")) + size
        if total > MAX_DIRTY_BINDING_BYTES:
            raise ReceiptError("dirty binding exceeds the safe size limit")
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise ReceiptError("untracked repository path is unavailable") from exc
        _reject_credential_material(data)
        chunks.extend((b"untracked\0", relative.encode("utf-8"), b"\0", data, b"\0"))

    _reject_credential_material(diff)
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return RepositoryBinding("dirty_diff", head, digest.hexdigest())


def build_verification_receipt(
    report: Mapping[str, Any],
    *,
    binding_before: RepositoryBinding,
    binding_after: RepositoryBinding,
) -> dict[str, Any]:
    """Build a deterministic receipt when the repository stayed unchanged."""

    if type(report) is not dict:
        raise ReceiptError("verification report must be a plain object")
    if binding_before != binding_after:
        raise ReceiptError("repository changed during verification")
    lane = _safe_token(report.get("lane"), field="lane")
    raw_checks = report.get("checks")
    if type(raw_checks) is not list or not 1 <= len(raw_checks) <= 128:
        raise ReceiptError("verification report checks are invalid")

    checks: list[dict[str, Any]] = []
    check_ids: set[str] = set()
    for raw in raw_checks:
        if type(raw) is not dict:
            raise ReceiptError("verification check must be a plain object")
        check_id = _safe_token(raw.get("check_id"), field="check_id")
        if check_id in check_ids:
            raise ReceiptError("verification check ids must be unique")
        check_ids.add(check_id)
        raw_status = raw.get("status")
        status = "skipped" if raw_status == "planned" else raw_status
        if status not in _CHECK_STATES:
            raise ReceiptError("verification check has an unknown state")
        required = raw.get("required")
        if type(required) is not bool:
            raise ReceiptError("verification check required flag is invalid")
        evidence_level = _safe_token(
            raw.get("evidence_level"),
            field="evidence_level",
        )
        if evidence_level not in _CHECK_EVIDENCE_RANK:
            raise ReceiptError("verification check evidence level is unknown")
        checks.append(
            {
                "check_id": check_id,
                "required": required,
                "status": status,
                "evidence_level": evidence_level,
            }
        )

    required_checks = [item["check_id"] for item in checks if item["required"]]
    grouped = {
        state: [item["check_id"] for item in checks if item["status"] == state]
        for state in ("passed", "failed", "skipped", "unavailable")
    }
    result = _receipt_result(checks)
    strongest = _derive_strongest_evidence(checks)
    limits = _safe_token_list(report.get("verification_limits"), field="not_verified")
    if result != "passed" and "lane_not_fully_verified" not in limits:
        limits.append("lane_not_fully_verified")

    payload: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "producer": {
            "id": RECEIPT_PRODUCER,
            "version": RECEIPT_PRODUCER_VERSION,
        },
        "binding": binding_after.to_dict(),
        "lane": lane,
        "result": result,
        "strongest_evidence_level": strongest,
        "required_checks": required_checks,
        "checks": checks,
        "passed_checks": grouped["passed"],
        "failed_checks": grouped["failed"],
        "skipped_checks": grouped["skipped"],
        "unavailable_checks": grouped["unavailable"],
        "not_verified": limits,
        "content_free": True,
    }
    payload["receipt_digest"] = _payload_digest(payload)
    return payload


def validate_verification_receipt(
    receipt: Mapping[str, Any],
    *,
    root: Path,
    expected_lane: str | None = None,
) -> None:
    """Reject malformed, stale or tampered receipts against current state."""

    if type(receipt) is not dict or frozenset(receipt) != _TOP_LEVEL_KEYS:
        raise ReceiptError("receipt fields are invalid")
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("content_free") is not True:
        raise ReceiptError("receipt contract is invalid")
    producer = receipt.get("producer")
    if type(producer) is not dict or frozenset(producer) != _PRODUCER_KEYS:
        raise ReceiptError("receipt producer is invalid")
    if producer != {
        "id": RECEIPT_PRODUCER,
        "version": RECEIPT_PRODUCER_VERSION,
    }:
        raise ReceiptError("receipt producer is invalid")

    lane = _safe_token(receipt.get("lane"), field="lane")
    if expected_lane is not None and lane != expected_lane:
        raise ReceiptError("receipt lane does not match")
    result = receipt.get("result")
    if result not in _RESULT_STATES:
        raise ReceiptError("receipt result state is unknown")
    strongest = _safe_token(
        receipt.get("strongest_evidence_level"),
        field="strongest_evidence_level",
    )
    if strongest not in _RECEIPT_EVIDENCE_LEVELS:
        raise ReceiptError("receipt evidence level is unknown")

    checks = receipt.get("checks")
    if type(checks) is not list or not 1 <= len(checks) <= 128:
        raise ReceiptError("receipt checks are invalid")
    ids: list[str] = []
    status_ids = {state: [] for state in _CHECK_STATES}
    derived_required: list[str] = []
    for check in checks:
        if type(check) is not dict or frozenset(check) != _CHECK_KEYS:
            raise ReceiptError("receipt check fields are invalid")
        check_id = _safe_token(check.get("check_id"), field="check_id")
        if check_id in ids:
            raise ReceiptError("receipt check ids must be unique")
        ids.append(check_id)
        status = check.get("status")
        if status not in _CHECK_STATES:
            raise ReceiptError("receipt check state is unknown")
        if type(check.get("required")) is not bool:
            raise ReceiptError("receipt check required flag is invalid")
        evidence_level = _safe_token(
            check.get("evidence_level"), field="evidence_level"
        )
        if evidence_level not in _CHECK_EVIDENCE_RANK:
            raise ReceiptError("receipt check evidence level is unknown")
        status_ids[status].append(check_id)
        if check["required"]:
            derived_required.append(check_id)

    required = _exact_token_list(receipt.get("required_checks"), "required_checks")
    passed = _exact_token_list(receipt.get("passed_checks"), "passed_checks")
    failed = _exact_token_list(receipt.get("failed_checks"), "failed_checks")
    skipped = _exact_token_list(receipt.get("skipped_checks"), "skipped_checks")
    unavailable = _exact_token_list(
        receipt.get("unavailable_checks"), "unavailable_checks"
    )
    if required != derived_required:
        raise ReceiptError("required check projection is inconsistent")
    if passed != status_ids["passed"] or failed != status_ids["failed"]:
        raise ReceiptError("check result projection is inconsistent")
    if skipped != status_ids["skipped"] or unavailable != status_ids["unavailable"]:
        raise ReceiptError("check limit projection is inconsistent")
    if result != _receipt_result(checks):
        raise ReceiptError("receipt result is inconsistent")
    if strongest != _derive_strongest_evidence(checks):
        raise ReceiptError("receipt evidence level is inconsistent")
    _safe_token_list(receipt.get("not_verified"), field="not_verified")

    binding = receipt.get("binding")
    if type(binding) is not dict or frozenset(binding) != _BINDING_KEYS:
        raise ReceiptError("receipt binding is invalid")
    _validate_binding_shape(binding)
    if binding != repository_binding(root).to_dict():
        raise ReceiptError("receipt binding is stale")

    digest = receipt.get("receipt_digest")
    if type(digest) is not str or _HEX_DIGEST.fullmatch(digest) is None:
        raise ReceiptError("receipt digest is invalid")
    unsigned = dict(receipt)
    del unsigned["receipt_digest"]
    if digest != _payload_digest(unsigned):
        raise ReceiptError("receipt was tampered with")


def _receipt_result(checks: Sequence[Mapping[str, Any]]) -> str:
    required_states = [item["status"] for item in checks if item["required"]]
    if not required_states or any(state == "skipped" for state in required_states):
        return "not_verified"
    if any(state == "failed" for state in required_states):
        return "failed"
    if any(state == "unavailable" for state in required_states):
        return "unavailable"
    return "passed" if all(state == "passed" for state in required_states) else "not_verified"


def _derive_strongest_evidence(checks: Sequence[Mapping[str, Any]]) -> str:
    if _receipt_result(checks) != "passed":
        return "none"
    levels = [
        item["evidence_level"]
        for item in checks
        if item["required"] and item["status"] == "passed"
    ]
    if "ui_contract" in levels and "visual" in levels:
        return "ui_contract_plus_visual_artifact"
    return max(levels, key=_CHECK_EVIDENCE_RANK.__getitem__)


def _payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _git_stdout(
    root: Path,
    command: Sequence[str],
    *,
    max_bytes: int = 1024 * 1024,
) -> bytes:
    try:
        completed = subprocess.run(
            list(command),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReceiptError("repository binding is unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > max_bytes:
        raise ReceiptError("repository binding is unavailable")
    return completed.stdout


def _changed_paths_from_status(status: bytes) -> tuple[str, ...]:
    try:
        fields = status.decode("utf-8", errors="strict").split("\0")
    except UnicodeError as exc:
        raise ReceiptError("repository paths are unavailable") from exc
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise ReceiptError("repository status is invalid")
        code = entry[:2]
        path = entry[3:].replace("\\", "/")
        _validate_relative_path(path)
        paths.append(path)
        if "R" in code or "C" in code:
            if index >= len(fields) or not fields[index]:
                raise ReceiptError("repository status is invalid")
            path = fields[index].replace("\\", "/")
            index += 1
            _validate_relative_path(path)
            paths.append(path)
    return tuple(sorted(set(paths)))


def _validate_relative_path(value: str) -> None:
    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts or "\x00" in value:
        raise ReceiptError("repository path is unsafe")


def _safe_repo_path(root: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    lexical = root / relative
    if lexical.is_symlink():
        raise ReceiptError("untracked repository links cannot be bound")
    try:
        candidate = lexical.resolve(strict=True)
    except OSError as exc:
        raise ReceiptError("untracked repository path is unavailable") from exc
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ReceiptError("repository path is unsafe") from exc
    return candidate


def _reject_sensitive_paths(paths: Sequence[str]) -> None:
    for value in paths:
        lowered = value.lower().replace("\\", "/")
        path = Path(lowered)
        parts = frozenset(path.parts)
        confidential_part = any(
            part.lstrip(".").startswith(_SENSITIVE_PATH_PREFIXES) for part in parts
        )
        if (
            parts & _SENSITIVE_PATH_PARTS
            or confidential_part
            or path.suffix in _SENSITIVE_SUFFIXES
        ):
            raise ReceiptError("sensitive paths cannot be bound into a receipt")
        if any(part.startswith(".env") for part in parts):
            raise ReceiptError("sensitive paths cannot be bound into a receipt")


def _reject_credential_material(data: bytes) -> None:
    if _CREDENTIAL_ASSIGNMENT.search(data) or _PRIVATE_KEY_HEADER.search(data):
        raise ReceiptError("credential-bearing changes cannot be bound into a receipt")


def _safe_token(value: object, *, field: str) -> str:
    if type(value) is not str or _SAFE_TOKEN.fullmatch(value) is None:
        raise ReceiptError(f"{field} is invalid")
    return value


def _safe_token_list(value: object, *, field: str) -> list[str]:
    if type(value) is not list or len(value) > 128:
        raise ReceiptError(f"{field} is invalid")
    result = [_safe_token(item, field=field) for item in value]
    if len(set(result)) != len(result):
        raise ReceiptError(f"{field} must be unique")
    return result


def _exact_token_list(value: object, field: str) -> list[str]:
    return _safe_token_list(value, field=field)


def _validate_binding_shape(binding: Mapping[str, Any]) -> None:
    state = binding.get("workspace_state")
    head = binding.get("head_revision")
    digest = binding.get("dirty_diff_digest")
    if state not in {"clean_head", "dirty_diff"}:
        raise ReceiptError("receipt workspace state is invalid")
    if type(head) is not str or _HEX_REVISION.fullmatch(head) is None:
        raise ReceiptError("receipt revision is invalid")
    if state == "clean_head" and digest is not None:
        raise ReceiptError("clean receipt cannot have a dirty digest")
    if state == "dirty_diff" and (
        type(digest) is not str or _HEX_DIGEST.fullmatch(digest) is None
    ):
        raise ReceiptError("dirty receipt digest is invalid")


__all__ = [
    "RECEIPT_SCHEMA",
    "ReceiptError",
    "RepositoryBinding",
    "build_verification_receipt",
    "repository_binding",
    "validate_verification_receipt",
]
