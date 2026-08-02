"""Resolve coding project requests into bounded, auditable work scopes."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from src.coding_agent_backend import CodingAgentBackendError, CodingCheckCommand
from src.repo_registry import RepoRecord, RepoRegistry


_DEFAULT_ALLOWED_PATHS_BY_KIND: dict[str, tuple[str, ...]] = {
    "odysseus": ("src", "routes", "tests", "plugins", "mcp_servers", "scripts", "docs/plans"),
    "project": ("src", "tests", "docs", "scripts"),
    "user": ("src", "tests", "docs", "scripts"),
    "external": ("src", "tests", "docs", "scripts"),
}
_DEFAULT_BLOCKED_PATHS = (
    ".git",
    ".env",
    ".venv",
    "venv",
    "data",
    "node_modules",
    "output",
    "secrets",
)
_MAX_PATHS = 40
_TEXT_RE = re.compile(r"^[A-Za-z0-9._/ -]{1,180}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class CodingProjectScopeError(ValueError):
    """Raised when a project scope request is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class CodingPlanningBinding:
    """A read-only projection of one already-validated Planning item.

    This record deliberately owns no lifecycle, claim, or gate state.  It is
    only the small, content-free Planning projection that a scope resolver can
    verify before it considers a project, path, or edit envelope.
    """

    planning_item_id: str
    canonical_plan_revision: str
    acceptance_contract: str
    allowed_paths: tuple[str, ...]
    gate_requirements: tuple[str, ...]
    binding_digest: str

    @classmethod
    def from_value(cls, value: "CodingPlanningBinding | Mapping[str, Any]") -> "CodingPlanningBinding":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise CodingProjectScopeError("Planning binding must be an object")

        status = str(value.get("status") or "validated").strip().lower().replace("-", "_")
        if status in {"missing", "stale", "ambiguous", "conflicting"}:
            raise CodingProjectScopeError(f"Planning binding is {status}")
        if status not in {"validated", "accepted", "current"}:
            raise CodingProjectScopeError("Planning binding must be validated")

        planning_item_id = _planning_identity(value.get("planning_item_id"), "planning_item_id")
        canonical_revision = _canonical_plan_revision(value)
        acceptance_contract = _planning_identity(
            value.get("acceptance_contract") or value.get("acceptance_criteria_id"),
            "acceptance_contract",
        )
        if "allowed_paths" not in value and "allowed_scope" not in value:
            raise CodingProjectScopeError("Planning binding is missing allowed_paths")
        raw_allowed = value.get("allowed_paths", value.get("allowed_scope"))
        if isinstance(raw_allowed, (str, bytes)):
            raise CodingProjectScopeError("Planning allowed_paths must be a bounded list")
        normalized_allowed = _normalize_paths(
            raw_allowed or (), field_name="planning_allowed_path", allow_empty=False
        )
        if "gate_requirements" not in value:
            raise CodingProjectScopeError("Planning binding is missing gate_requirements")
        raw_gates = value.get("gate_requirements")
        if isinstance(raw_gates, (str, bytes)) or not isinstance(raw_gates, Iterable):
            raise CodingProjectScopeError("Planning gate_requirements must be a bounded list")
        gates = tuple(sorted(dict.fromkeys(_planning_identity(item, "gate_requirement") for item in raw_gates)))
        if not gates or len(gates) > 32:
            raise CodingProjectScopeError("Planning gate_requirements must be a non-empty bounded list")
        core = {
            "planning_item_id": planning_item_id,
            "canonical_plan_revision": canonical_revision,
            "acceptance_contract": acceptance_contract,
            "allowed_paths": normalized_allowed,
            "gate_requirements": gates,
        }
        return cls(
            planning_item_id=planning_item_id,
            canonical_plan_revision=canonical_revision,
            acceptance_contract=acceptance_contract,
            allowed_paths=normalized_allowed,
            gate_requirements=gates,
            binding_digest=_hash_canonical(core),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_item_id": self.planning_item_id,
            "canonical_plan_revision": self.canonical_plan_revision,
            "acceptance_contract": self.acceptance_contract,
            "allowed_paths": list(self.allowed_paths),
            "gate_requirements": list(self.gate_requirements),
            "binding_digest": self.binding_digest,
            "authoritative": True,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class CodingProjectCandidate:
    repo_id: str
    title: str
    repo_kind: str
    owner: str
    linked_project_slug: str

    @classmethod
    def from_record(cls, record: RepoRecord) -> "CodingProjectCandidate":
        return cls(
            repo_id=record.repo_id,
            title=record.title,
            repo_kind=record.repo_kind,
            owner=record.owner,
            linked_project_slug=record.linked_project_slug,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "title": self.title,
            "repo_kind": self.repo_kind,
            "owner": self.owner,
            "linked_project_slug": self.linked_project_slug,
        }


@dataclass(frozen=True, slots=True)
class CodingProjectScopeResolution:
    status: str
    repo_id: str
    repo_title: str
    repo_kind: str
    owner: str
    project_query_hash: str
    slice_id: str
    target_ref: str
    allowed_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    checks: tuple[CodingCheckCommand, ...]
    branch_policy: dict[str, Any]
    sandbox_policy: dict[str, Any]
    candidates: tuple[CodingProjectCandidate, ...]
    blockers: tuple[str, ...]
    next_human_decision: str
    planning_binding: CodingPlanningBinding | None = None
    scope_digest: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "repo_id": self.repo_id,
            "repo_title": self.repo_title,
            "repo_kind": self.repo_kind,
            "owner": self.owner,
            "project_query_hash": self.project_query_hash,
            "slice_id": self.slice_id,
            "target_ref": self.target_ref,
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "checks": [check.to_dict() for check in self.checks],
            "branch_policy": dict(self.branch_policy),
            "sandbox_policy": dict(self.sandbox_policy),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "blockers": list(self.blockers),
            "next_human_decision": self.next_human_decision,
            "planning": (
                self.planning_binding.to_dict()
                if self.planning_binding is not None
                else {"authoritative": False, "raw_content_visible": False}
            ),
            "scope_digest": self.scope_digest,
            "raw_content_visible": False,
        }


def resolve_coding_project_scope(
    *,
    registry: RepoRegistry,
    project: Any,
    owner: Any = "",
    slice_id: Any = "",
    allowed_paths: Iterable[Any] = (),
    blocked_paths: Iterable[Any] = (),
    checks: Iterable[CodingCheckCommand] = (),
    sandbox_live_enabled: bool = False,
    planning_binding: CodingPlanningBinding | Mapping[str, Any] | None = None,
) -> CodingProjectScopeResolution:
    """Resolve a project only inside an already-validated Planning envelope."""

    if not isinstance(registry, RepoRegistry):
        raise CodingProjectScopeError("registry must be a RepoRegistry")
    query = _normalize_query(project, field_name="project")
    owner_filter = _normalize_query(owner, field_name="owner", allow_empty=True)
    candidates = _find_candidates(registry, query=query, owner_filter=owner_filter)
    query_hash = _hash_text(query)
    requested_slice_id = (
        _normalize_safe_id(slice_id, fallback="") if str(slice_id or "").strip() else ""
    )
    try:
        binding = _coerce_planning_binding(planning_binding)
    except CodingProjectScopeError as exc:
        return _blocked_resolution(
            query_hash=query_hash,
            slice_id=requested_slice_id or _stable_id(query),
            blockers=(f"Planning authority blocked scope: {_planning_blocker(exc)}",),
            next_human_decision="Provide one current, unambiguous validated Planning item and revision before scope resolution.",
            candidates=(),
        )
    canonical_slice_id = _normalize_safe_id(binding.planning_item_id, fallback="")
    if requested_slice_id and requested_slice_id != canonical_slice_id:
        return _blocked_resolution(
            query_hash=query_hash,
            slice_id=canonical_slice_id,
            blockers=("requested slice_id conflicts with the validated Planning item",),
            next_human_decision="Use the validated Planning item identity; a request cannot replace Planning scope.",
            candidates=(),
            planning_binding=binding,
        )
    safe_slice_id = canonical_slice_id
    try:
        requested_allowed = _normalize_paths(
            allowed_paths, field_name="allowed_path", allow_empty=True
        )
    except CodingProjectScopeError:
        raise
    if requested_allowed and requested_allowed != binding.allowed_paths:
        return _blocked_resolution(
            query_hash=query_hash,
            slice_id=safe_slice_id,
            blockers=("requested allowed_paths conflict with validated Planning scope",),
            next_human_decision="Update Planning through its canonical authority before requesting a different scope.",
            candidates=(),
            planning_binding=binding,
        )

    if not candidates:
        return _blocked_resolution(
            query_hash=query_hash,
            slice_id=safe_slice_id,
            blockers=("project reference did not match a registered repo",),
            next_human_decision="Register the repo or resend the request with an exact repo_id.",
            candidates=(),
            planning_binding=binding,
        )

    if len(candidates) > 1:
        return _blocked_resolution(
            query_hash=query_hash,
            slice_id=safe_slice_id,
            blockers=("project reference is ambiguous",),
            next_human_decision="Choose one repo_id from candidates before the coding runner starts.",
            candidates=tuple(CodingProjectCandidate.from_record(record) for record in candidates),
            planning_binding=binding,
        )

    record = candidates[0]
    normalized_allowed = binding.allowed_paths
    normalized_blocked = tuple(
        sorted(
            set(
                _normalize_paths(_DEFAULT_BLOCKED_PATHS, field_name="blocked_path", allow_empty=False, allow_blocked=True)
                + _normalize_paths(blocked_paths, field_name="blocked_path", allow_empty=True, allow_blocked=True)
            )
        )
    )
    normalized_checks = tuple(checks) or (CodingCheckCommand.create(argv=("git", "status", "--short", "--branch")),)
    for check in normalized_checks:
        if not isinstance(check, CodingCheckCommand):
            raise CodingProjectScopeError("checks must contain CodingCheckCommand records")

    branch_policy = {
        "base_ref": record.current_branch or record.default_branch or "HEAD",
        "worker_branch_prefix": f"codex/{record.repo_id}/{safe_slice_id}",
        "remote_name": _preferred_push_remote(record),
        "default_branch": record.default_branch,
        "branch_action_allowed": "branch" in record.allowed_actions,
    }
    sandbox_policy = {
        "mode": "live" if sandbox_live_enabled else "dry_run",
        "network_allowed": False,
        "operator_go_required": True,
        "live_enabled": bool(sandbox_live_enabled),
        "write_paths": list(normalized_allowed),
        "blocked_paths": list(normalized_blocked),
        "allowed_check_commands": [list(check.argv) for check in normalized_checks],
    }

    blockers: list[str] = []
    if "branch" not in record.allowed_actions:
        blockers.append("repo registry does not allow branch/worktree actions")
    if sandbox_live_enabled:
        blockers.append("sandbox live execution still requires explicit operator Go")

    return CodingProjectScopeResolution(
        status="blocked" if blockers else "resolved",
        repo_id=record.repo_id,
        repo_title=record.title,
        repo_kind=record.repo_kind,
        owner=record.owner,
        project_query_hash=query_hash,
        slice_id=safe_slice_id,
        target_ref=f"repo:{record.repo_id}",
        allowed_paths=normalized_allowed,
        blocked_paths=normalized_blocked,
        checks=normalized_checks,
        branch_policy=branch_policy,
        sandbox_policy=sandbox_policy,
        candidates=(),
        blockers=tuple(blockers),
        next_human_decision=(
            "Scope is Planning-bound; use the canonical lifecycle and fresh exact reads before any edit."
            if not blockers
            else "Update repo policy or operator gates before the runner creates a worktree."
        ),
        planning_binding=binding,
        scope_digest=_scope_digest(binding, repo_id=record.repo_id, blocked_paths=normalized_blocked),
    )


def _find_candidates(registry: RepoRegistry, *, query: str, owner_filter: str = "") -> tuple[RepoRecord, ...]:
    query_key = _search_key(query)
    records = tuple(record for record in registry.repos.values() if not owner_filter or _search_key(record.owner) == _search_key(owner_filter))
    exact = tuple(
        record
        for record in records
        if query_key
        in {
            _search_key(record.repo_id),
            _search_key(record.title),
            _search_key(record.linked_project_slug),
            _search_key(posixpath.basename(record.project_root)),
        }
    )
    if exact:
        return _sort_records(exact)

    fuzzy = tuple(
        record
        for record in records
        if query_key
        and any(
            query_key in value
            for value in (
                _search_key(record.repo_id),
                _search_key(record.title),
                _search_key(record.linked_project_slug),
                _search_key(posixpath.basename(record.project_root)),
            )
        )
    )
    return _sort_records(fuzzy)


def _blocked_resolution(
    *,
    query_hash: str,
    slice_id: str,
    blockers: tuple[str, ...],
    next_human_decision: str,
    candidates: tuple[CodingProjectCandidate, ...],
    planning_binding: CodingPlanningBinding | None = None,
) -> CodingProjectScopeResolution:
    return CodingProjectScopeResolution(
        status="blocked",
        repo_id="",
        repo_title="",
        repo_kind="",
        owner="",
        project_query_hash=query_hash,
        slice_id=slice_id,
        target_ref="",
        allowed_paths=(),
        blocked_paths=(),
        checks=(),
        branch_policy={},
        sandbox_policy={"mode": "dry_run", "network_allowed": False, "operator_go_required": True},
        candidates=candidates,
        blockers=blockers,
        next_human_decision=next_human_decision,
        planning_binding=planning_binding,
    )


def _normalize_query(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        if allow_empty:
            return ""
        raise CodingProjectScopeError(f"{field_name} must not be empty")
    if len(text) > 180:
        raise CodingProjectScopeError(f"{field_name} exceeds max length 180")
    if not _TEXT_RE.fullmatch(text) or _SECRET_RE.search(text) or _WINDOWS_ABSOLUTE_RE.search(text) or text.startswith(("/", "\\")):
        raise CodingProjectScopeError(f"{field_name} contains unsupported or sensitive text")
    return text


def _normalize_safe_id(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    safe = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        safe = fallback
    if not _SAFE_ID_RE.fullmatch(safe):
        raise CodingProjectScopeError("slice_id contains unsupported characters")
    return safe[:80]


def _canonical_plan_revision(value: Mapping[str, Any]) -> str:
    canonical = _planning_identity_or_empty(value.get("canonical_plan_revision"), "canonical_plan_revision")
    legacy = _planning_identity_or_empty(value.get("planning_revision"), "planning_revision")
    if canonical and legacy and canonical != legacy:
        raise CodingProjectScopeError("Planning binding has ambiguous canonical_plan_revision")
    revision = canonical or legacy
    if not revision:
        raise CodingProjectScopeError("Planning binding is missing canonical_plan_revision")
    return revision


def _planning_identity(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CodingProjectScopeError(f"Planning binding is missing {field_name}")
    if _SECRET_RE.search(text) or not _SAFE_ID_RE.fullmatch(text):
        raise CodingProjectScopeError(f"Planning {field_name} is invalid")
    return text


def _planning_identity_or_empty(value: Any, field_name: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    return _planning_identity(value, field_name)


def _coerce_planning_binding(
    value: CodingPlanningBinding | Mapping[str, Any] | None,
) -> CodingPlanningBinding:
    if value is None:
        raise CodingProjectScopeError("Planning binding is missing")
    return CodingPlanningBinding.from_value(value)


def _planning_blocker(error: CodingProjectScopeError) -> str:
    text = str(error).strip().lower()
    if "stale" in text:
        return "planning_data_stale"
    if "ambiguous" in text or "conflicting" in text:
        return "planning_data_ambiguous"
    if "missing" in text:
        return "planning_data_missing"
    return "planning_data_invalid"


def _normalize_paths(
    values: Iterable[Any],
    *,
    field_name: str,
    allow_empty: bool,
    allow_blocked: bool = False,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip().replace("\\", "/")
        if not raw:
            raise CodingProjectScopeError(f"{field_name} must not be empty")
        if raw.startswith(("/", "~")) or _WINDOWS_ABSOLUTE_RE.match(raw):
            raise CodingProjectScopeError(f"{field_name} must be repo-relative")
        parts = PurePosixPath(raw).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise CodingProjectScopeError(f"{field_name} must not contain traversal segments")
        normalized = "/".join(parts)
        lowered = normalized.lower()
        if not allow_blocked and any(lowered == root or lowered.startswith(f"{root}/") for root in _DEFAULT_BLOCKED_PATHS):
            raise CodingProjectScopeError(f"{field_name} targets a blocked project path")
        if _SECRET_RE.search(normalized):
            raise CodingProjectScopeError(f"{field_name} appears to contain secret material")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if len(result) > _MAX_PATHS:
        raise CodingProjectScopeError(f"{field_name} exceeds max item count {_MAX_PATHS}")
    if not allow_empty and not result:
        raise CodingProjectScopeError(f"{field_name} must not be empty")
    return tuple(sorted(result))


def _preferred_push_remote(record: RepoRecord) -> str:
    for remote in record.remotes:
        if remote.push_policy == "push_allowed":
            return remote.name
    return "fuzzy"


def _search_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _hash_canonical(value: Mapping[str, Any]) -> str:
    import json

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scope_digest(
    binding: CodingPlanningBinding,
    *,
    repo_id: str,
    blocked_paths: tuple[str, ...],
) -> str:
    return _hash_canonical(
        {
            "planning_binding_digest": binding.binding_digest,
            "repo_id": repo_id,
            "allowed_paths": binding.allowed_paths,
            "blocked_paths": blocked_paths,
        }
    )


def _sort_records(records: Iterable[RepoRecord]) -> tuple[RepoRecord, ...]:
    return tuple(sorted(records, key=lambda record: record.repo_id))
