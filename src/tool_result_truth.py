"""Small backend contract for machine-readable tool result truth."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable


_MAX_SUMMARY_CHARS = 240
_MAX_EVIDENCE_ITEM_CHARS = 160
_MAX_COMMIT_LENGTH = 40


class ResultTruthError(ValueError):
    """Raised when a tool result truth payload is invalid or unsafe."""


class ResultStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ResultTruthError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_repo_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ResultTruthError("changed_file path must not be empty")
    if "\\" in raw:
        raise ResultTruthError("changed_file path must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise ResultTruthError("changed_file path must be relative to the repo root")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ResultTruthError("changed_file path must not contain traversal segments")
    return "/".join(parts)


def _normalize_path_list(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return tuple(sorted(normalized))


def _normalize_text_list(values: Iterable[Any], *, field_name: str, limit: int) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True, limit=limit)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_commit(commit: str) -> str:
    text = str(commit or "").strip().lower()
    if not text:
        return ""
    if len(text) > _MAX_COMMIT_LENGTH or not _COMMIT_RE.fullmatch(text):
        raise ResultTruthError("commit must be empty or a git sha-like hex id")
    return text


def _is_verified_done(status: ResultStatus, evidence: tuple[str, ...], errors: tuple[str, ...]) -> bool:
    return status == ResultStatus.SUCCESS and bool(evidence) and not errors


@dataclass(frozen=True, slots=True)
class ToolResultTruth:
    status: ResultStatus
    summary: str
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    exit_code: int | None
    commit: str
    changed_files: tuple[str, ...]
    tests: tuple[str, ...]
    capsule_id: str
    verified_done: bool

    @classmethod
    def create(
        cls,
        *,
        status: ResultStatus | str,
        summary: str,
        evidence: Iterable[Any],
        warnings: Iterable[Any],
        errors: Iterable[Any],
        exit_code: int | None,
        commit: str,
        changed_files: Iterable[str],
        tests: Iterable[Any],
        capsule_id: str,
    ) -> "ToolResultTruth":
        normalized_status = status if isinstance(status, ResultStatus) else ResultStatus(str(status))
        normalized_summary = _normalize_text(
            summary,
            field_name="summary",
            allow_empty=False,
            limit=_MAX_SUMMARY_CHARS,
        )
        normalized_evidence = _normalize_text_list(
            evidence,
            field_name="evidence",
            limit=_MAX_EVIDENCE_ITEM_CHARS,
        )
        normalized_warnings = _normalize_text_list(
            warnings,
            field_name="warnings",
            limit=_MAX_EVIDENCE_ITEM_CHARS,
        )
        normalized_errors = _normalize_text_list(
            errors,
            field_name="errors",
            limit=_MAX_EVIDENCE_ITEM_CHARS,
        )
        if exit_code is not None and not isinstance(exit_code, int):
            raise ResultTruthError("exit_code must be an int or None")
        if normalized_status == ResultStatus.SUCCESS and not normalized_evidence:
            verified = False
        else:
            verified = _is_verified_done(normalized_status, normalized_evidence, normalized_errors)
        if normalized_status in {ResultStatus.FAILED, ResultStatus.BLOCKED} and not normalized_errors:
            raise ResultTruthError("failed or blocked results require at least one error or blocker hint")
        if normalized_status == ResultStatus.SUCCESS and normalized_errors:
            raise ResultTruthError("success results must not carry errors")
        return cls(
            status=normalized_status,
            summary=normalized_summary,
            evidence=normalized_evidence,
            warnings=normalized_warnings,
            errors=normalized_errors,
            exit_code=exit_code,
            commit=_normalize_commit(commit),
            changed_files=_normalize_path_list(changed_files),
            tests=_normalize_text_list(tests, field_name="tests", limit=_MAX_EVIDENCE_ITEM_CHARS),
            capsule_id=_normalize_text(capsule_id, field_name="capsule_id", allow_empty=False, limit=80),
            verified_done=verified,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "verified_done": self.verified_done,
            "capsule_id": self.capsule_id,
            "commit": self.commit,
            "changed_file_count": len(self.changed_files),
            "test_count": len(self.tests),
            "evidence_count": len(self.evidence),
            "warning_count": len(self.warnings),
            "error_count": len(self.errors),
            "exit_code": self.exit_code,
            "tests": self.tests,
            "changed_files": self.changed_files,
        }
