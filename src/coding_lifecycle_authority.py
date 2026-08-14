"""Planning authority and resumable production coding lifecycle contracts.

The contract is a pure state reducer.  It validates typed authority supplied by
callers but never looks authority up, executes tools, mutates git or worktrees,
writes memory, or dispatches work.  The existing
``odysseus.coding_lifecycle.v1`` projection is only linked by a digest.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from src.coding_lifecycle import CODING_LIFECYCLE_SCHEMA, CodingLifecycleState
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash


CODING_LIFECYCLE_AUTHORITY_SCHEMA = "odysseus.coding_lifecycle_authority.v1"
MAX_AUTHORITY_ID_LENGTH = 180
MAX_AUTHORITY_SCOPE_ENTRIES = 64

PRODUCTION_CODING_LIFECYCLE_STATES = (
    "clarifying",
    "planning",
    "ready_for_claim",
    "claimed",
    "context_building",
    "context_ready",
    "worktree_ready",
    "acting",
    "verifying",
    "repair_planning",
    "review_ready",
    "memory_review",
    "publish_ready",
    "done",
)
HOLDING_STATES = ("waiting", "blocked")

_ALLOWED_TRANSITIONS = {
    "clarifying": frozenset({"planning"}),
    "planning": frozenset({"ready_for_claim"}),
    "ready_for_claim": frozenset({"claimed"}),
    "claimed": frozenset({"context_building"}),
    "context_building": frozenset({"context_ready"}),
    "context_ready": frozenset({"worktree_ready"}),
    "worktree_ready": frozenset({"acting"}),
    "acting": frozenset({"verifying"}),
    "verifying": frozenset({"repair_planning", "review_ready"}),
    "repair_planning": frozenset({"acting"}),
    "review_ready": frozenset({"memory_review"}),
    "memory_review": frozenset({"publish_ready"}),
    "publish_ready": frozenset({"done"}),
    "done": frozenset(),
}
_STATE_INDEX = {state: index for index, state in enumerate(PRODUCTION_CODING_LIFECYCLE_STATES)}

_SCALAR_AUTHORITY_FIELDS = (
    "planning_item_id",
    "planning_revision",
    "acceptance_criteria_id",
    "claim_id",
    "claim_owner",
    "claim_scope_digest",
    "input_revision",
    "input_diff_digest",
    "acceptance_decision_id",
    "evidence_id",
)
_SCOPE_AUTHORITY_FIELDS = ("allowed_scope", "blocked_scope", "claim_scope")
_PLANNING_FIELDS = (
    "planning_item_id",
    "planning_revision",
    "acceptance_criteria_id",
    "allowed_scope",
)
_CLAIM_FIELDS = ("claim_id", "claim_owner", "claim_scope", "claim_scope_digest")
_INPUT_FIELDS = ("input_revision", "input_diff_digest")
_CLOSURE_FIELDS = ("acceptance_decision_id", "evidence_id")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-=]{1,180}$")
_STRICT_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_STRICT_SCOPE_RE = re.compile(r"^[A-Za-z0-9_.@+\-=/]{1,240}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id|credential)\b\s*[:=]?\s*\S*"
)
_HOST_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)


class CodingLifecycleAuthorityError(ValueError):
    """Raised when an authority lifecycle request is structurally invalid."""


@dataclass(frozen=True, slots=True)
class CodingLifecycleAuthority:
    """Typed, monotonically enriched authority for one Planning item revision."""

    planning_item_id: str = ""
    planning_revision: str = ""
    acceptance_criteria_id: str = ""
    allowed_scope: tuple[str, ...] = ()
    blocked_scope: tuple[str, ...] = ()
    claim_id: str = ""
    claim_owner: str = ""
    claim_scope: tuple[str, ...] = ()
    claim_scope_digest: str = ""
    input_revision: str = ""
    input_diff_digest: str = ""
    acceptance_decision_id: str = ""
    evidence_id: str = ""

    def __post_init__(self) -> None:
        for field_name in _SCALAR_AUTHORITY_FIELDS:
            _strict_identity(getattr(self, field_name), field=field_name, allow_empty=True)
        for field_name in _SCOPE_AUTHORITY_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise CodingLifecycleAuthorityError(f"{field_name} must be a tuple")
            if _normalize_scope(value) != value:
                raise CodingLifecycleAuthorityError(f"{field_name} must be normalized")

    @classmethod
    def create(cls, **values: Any) -> "CodingLifecycleAuthority":
        claim_scope = _normalize_scope(values.get("claim_scope", ()))
        supplied_digest = _strict_identity(
            values.get("claim_scope_digest", ""), field="claim_scope_digest", allow_empty=True
        )
        return cls(
            planning_item_id=_strict_identity(
                values.get("planning_item_id", ""), field="planning_item_id", allow_empty=True
            ),
            planning_revision=_strict_identity(
                values.get("planning_revision", ""), field="planning_revision", allow_empty=True
            ),
            acceptance_criteria_id=_strict_identity(
                values.get("acceptance_criteria_id", ""), field="acceptance_criteria_id", allow_empty=True
            ),
            allowed_scope=_normalize_scope(values.get("allowed_scope", ())),
            blocked_scope=_normalize_scope(values.get("blocked_scope", ())),
            claim_id=_strict_identity(values.get("claim_id", ""), field="claim_id", allow_empty=True),
            claim_owner=_strict_identity(
                values.get("claim_owner", ""), field="claim_owner", allow_empty=True
            ),
            claim_scope=claim_scope,
            claim_scope_digest=supplied_digest or (_scope_digest(claim_scope) if claim_scope else ""),
            input_revision=_strict_identity(
                values.get("input_revision", ""), field="input_revision", allow_empty=True
            ),
            input_diff_digest=_strict_identity(
                values.get("input_diff_digest", ""), field="input_diff_digest", allow_empty=True
            ),
            acceptance_decision_id=_strict_identity(
                values.get("acceptance_decision_id", ""),
                field="acceptance_decision_id",
                allow_empty=True,
            ),
            evidence_id=_strict_identity(
                values.get("evidence_id", ""), field="evidence_id", allow_empty=True
            ),
        )

    @classmethod
    def from_value(
        cls, value: "CodingLifecycleAuthority | Mapping[str, Any] | None"
    ) -> "CodingLifecycleAuthority":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return cls.create(
                **{
                    field_name: getattr(value, field_name)
                    for field_name in (*_SCALAR_AUTHORITY_FIELDS, *_SCOPE_AUTHORITY_FIELDS)
                }
            )
        if not isinstance(value, Mapping):
            raise CodingLifecycleAuthorityError("authority must be a mapping")
        return cls.create(**value)

    def missing_for_state(self, state: str) -> tuple[str, ...]:
        required = list(_PLANNING_FIELDS)
        index = _STATE_INDEX[state]
        if index >= _STATE_INDEX["claimed"]:
            required.extend(_CLAIM_FIELDS)
        if index >= _STATE_INDEX["worktree_ready"]:
            required.extend(_INPUT_FIELDS)
        if index >= _STATE_INDEX["review_ready"]:
            required.extend(_CLOSURE_FIELDS)
        return tuple(field for field in required if not getattr(self, field))

    def mismatch_fields(self, observed: "CodingLifecycleAuthority") -> tuple[str, ...]:
        fields = (*_SCALAR_AUTHORITY_FIELDS, *_SCOPE_AUTHORITY_FIELDS)
        return tuple(
            field
            for field in fields
            if getattr(self, field) and getattr(self, field) != getattr(observed, field)
        )

    def validation_errors_for_state(self, state: str) -> tuple[str, ...]:
        errors: list[str] = []
        index = _STATE_INDEX[state]
        if index >= _STATE_INDEX["claimed"] and self.claim_scope:
            expected_digest = _scope_digest(self.claim_scope)
            if self.claim_scope_digest != expected_digest:
                errors.append("claim_scope_digest_mismatch")
            for path in self.claim_scope:
                if any(_scope_contains(blocked, path) for blocked in self.blocked_scope):
                    errors.append(f"claim_scope_blocked:{stable_payload_hash(path)}")
                elif not any(_scope_contains(allowed, path) for allowed in self.allowed_scope):
                    errors.append(f"claim_scope_outside_allowed:{stable_payload_hash(path)}")
        if index >= _STATE_INDEX["review_ready"]:
            if self.acceptance_decision_id == self.acceptance_criteria_id:
                errors.append("acceptance_decision_not_distinct")
        return tuple(errors)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": "odysseus.coding_lifecycle_authority_binding.v1",
            "planning": {
                "item_id": self.planning_item_id,
                "revision": self.planning_revision,
                "acceptance_criteria_id": self.acceptance_criteria_id,
                "allowed_scope": self.allowed_scope,
                "blocked_scope": self.blocked_scope,
            },
            "claim": {
                "claim_id": self.claim_id,
                "owner": self.claim_owner,
                "scope": self.claim_scope,
                "scope_digest": self.claim_scope_digest,
            },
            "input": {
                "revision": self.input_revision,
                "diff_digest": self.input_diff_digest,
            },
            "acceptance": {
                "criteria_id": self.acceptance_criteria_id,
                "decision_id": self.acceptance_decision_id,
            },
            "evidence": {"evidence_id": self.evidence_id},
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


@dataclass(frozen=True, slots=True)
class CodingLifecycleCompletionProof:
    acceptance_decision_id: str
    evidence_id: str
    reviewer_id: str
    all_required_gates_closed: bool
    independent_review: bool

    def __post_init__(self) -> None:
        _strict_identity(
            self.acceptance_decision_id, field="completion.acceptance_decision_id"
        )
        _strict_identity(self.evidence_id, field="completion.evidence_id")
        _strict_identity(self.reviewer_id, field="completion.reviewer_id")
        if type(self.all_required_gates_closed) is not bool or type(self.independent_review) is not bool:
            raise CodingLifecycleAuthorityError("completion gate flags must be booleans")

    @classmethod
    def create(
        cls,
        *,
        acceptance_decision_id: Any,
        evidence_id: Any,
        reviewer_id: Any,
        all_required_gates_closed: Any,
        independent_review: Any,
    ) -> "CodingLifecycleCompletionProof":
        return cls(
            acceptance_decision_id=_strict_identity(
                acceptance_decision_id, field="completion.acceptance_decision_id"
            ),
            evidence_id=_strict_identity(evidence_id, field="completion.evidence_id"),
            reviewer_id=_strict_identity(reviewer_id, field="completion.reviewer_id"),
            all_required_gates_closed=all_required_gates_closed is True,
            independent_review=independent_review is True,
        )

    @classmethod
    def from_value(
        cls, value: "CodingLifecycleCompletionProof | Mapping[str, Any] | None"
    ) -> "CodingLifecycleCompletionProof | None":
        if value is None:
            return value
        if isinstance(value, cls):
            return cls.create(
                acceptance_decision_id=value.acceptance_decision_id,
                evidence_id=value.evidence_id,
                reviewer_id=value.reviewer_id,
                all_required_gates_closed=value.all_required_gates_closed,
                independent_review=value.independent_review,
            )
        if not isinstance(value, Mapping):
            raise CodingLifecycleAuthorityError("completion proof must be a mapping")
        return cls.create(**value)


@dataclass(frozen=True, slots=True)
class AuthorizedCodingLifecycleState:
    coding_task_id: str
    repo_id: str
    state: str
    last_accepted_state: str
    authority: CodingLifecycleAuthority
    transition_ordinal: int = 0
    resume_condition: str = ""
    resume_checkpoint: str = ""
    waiting_reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    legacy_schema: str = CODING_LIFECYCLE_SCHEMA
    legacy_payload_digest: str = ""
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": CODING_LIFECYCLE_AUTHORITY_SCHEMA,
            "coding_task_id": self.coding_task_id,
            "repo_id": self.repo_id,
            "state": self.state,
            "last_accepted_state": self.last_accepted_state,
            "authority": self.authority.to_dict(),
            "transition_ordinal": self.transition_ordinal,
            "resume_condition": self.resume_condition,
            "resume_checkpoint": self.resume_checkpoint,
            "waiting_reasons": self.waiting_reasons,
            "blockers": self.blockers,
            "legacy_compatibility": {
                "schema": self.legacy_schema,
                "payload_digest": self.legacy_payload_digest,
                "payload_embedded": False,
            },
            "side_effects": ("none",),
            "raw_content_visible": False,
            "runtime_event": self.runtime_event(),
        }
        _reject_unsafe_payload(payload)
        return payload

    def runtime_event(self) -> dict[str, Any]:
        status = "success" if self.state == "done" else "blocked" if self.state in HOLDING_STATES else "running"
        return build_runtime_event(
            surface="coding_agent",
            component="coding_lifecycle_authority",
            event_type="lifecycle_authority_state",
            status=status,
            severity="warning" if self.state in HOLDING_STATES else "info",
            owner_scope=f"repo:{self.repo_id}",
            correlation_id=self.coding_task_id,
            task_id=self.coding_task_id,
            side_effects=("none",),
            metadata={
                "authority_schema": CODING_LIFECYCLE_AUTHORITY_SCHEMA,
                "lifecycle_state": self.state,
                "last_accepted_state": self.last_accepted_state,
                "transition_ordinal": self.transition_ordinal,
                "waiting_reason_count": len(self.waiting_reasons),
                "blocker_count": len(self.blockers),
            },
        )


def start_authorized_coding_lifecycle(
    *,
    task_id: Any,
    repo_id: Any,
    authority: CodingLifecycleAuthority | Mapping[str, Any] | None,
    legacy_state: CodingLifecycleState | None = None,
) -> AuthorizedCodingLifecycleState:
    """Start at clarifying, failing safe to waiting on missing Planning authority."""

    binding = CodingLifecycleAuthority.from_value(authority)
    legacy_digest = stable_payload_hash(legacy_state.to_dict()) if legacy_state is not None else ""
    missing = binding.missing_for_state("clarifying")
    if missing:
        return _new_hold(
            task_id=_safe_identity(task_id),
            repo_id=_safe_identity(repo_id),
            authority=binding,
            state="waiting",
            last_accepted_state="",
            condition="supply_authority_for:clarifying",
            waiting_reasons=tuple(f"missing_authority:{field}" for field in missing),
            legacy_payload_digest=legacy_digest,
        )
    return AuthorizedCodingLifecycleState(
        coding_task_id=_safe_identity(task_id),
        repo_id=_safe_identity(repo_id),
        state="clarifying",
        last_accepted_state="clarifying",
        authority=binding,
        legacy_payload_digest=legacy_digest,
    )


def transition_authorized_coding_lifecycle(
    current: AuthorizedCodingLifecycleState,
    *,
    target_state: Any,
    observed_authority: CodingLifecycleAuthority | Mapping[str, Any] | None = None,
    resume_condition: Any = "",
    completion_proof: CodingLifecycleCompletionProof | Mapping[str, Any] | None = None,
) -> AuthorizedCodingLifecycleState:
    """Apply one DAG-validated transition without any external side effect."""

    target = _state_token(target_state)
    if current.state in HOLDING_STATES:
        raise CodingLifecycleAuthorityError("holding lifecycle must be resumed before transition")
    candidate = current.authority if observed_authority is None else CodingLifecycleAuthority.from_value(observed_authority)
    mismatches = current.authority.mismatch_fields(candidate)
    if mismatches:
        return _hold(
            current,
            state="blocked",
            condition="provide_matching_authority",
            blockers=tuple(f"authority_mismatch:{field}" for field in mismatches),
        )
    if target in HOLDING_STATES:
        condition = _safe_identity(resume_condition)
        if not condition:
            raise CodingLifecycleAuthorityError("waiting or blocked requires a resume condition")
        return _hold(
            current,
            state=target,
            condition=condition,
            waiting_reasons=(condition,) if target == "waiting" else (),
            blockers=(condition,) if target == "blocked" else (),
            authority=candidate,
        )
    if target not in _ALLOWED_TRANSITIONS[current.state]:
        raise CodingLifecycleAuthorityError(f"invalid lifecycle transition: {current.state} -> {target}")
    missing = candidate.missing_for_state(target)
    if missing:
        return _hold(
            current,
            state="waiting",
            condition=f"supply_authority_for:{target}",
            waiting_reasons=tuple(f"missing_authority:{field}" for field in missing),
            authority=candidate,
        )
    validation_errors = candidate.validation_errors_for_state(target)
    if validation_errors:
        return _hold(
            current,
            state="blocked",
            condition="correct_authority_contract",
            blockers=validation_errors,
            authority=candidate,
        )
    if target == "done":
        proof_error = _completion_proof_error(
            candidate, CodingLifecycleCompletionProof.from_value(completion_proof)
        )
        if proof_error:
            return _hold(
                current,
                state="blocked",
                condition="provide_independent_completion_proof",
                blockers=(proof_error,),
                authority=candidate,
            )
    return AuthorizedCodingLifecycleState(
        coding_task_id=current.coding_task_id,
        repo_id=current.repo_id,
        state=target,
        last_accepted_state=target,
        authority=candidate,
        transition_ordinal=current.transition_ordinal + 1,
        legacy_schema=current.legacy_schema,
        legacy_payload_digest=current.legacy_payload_digest,
    )


def resume_authorized_coding_lifecycle(
    current: AuthorizedCodingLifecycleState,
    *,
    observed_authority: CodingLifecycleAuthority | Mapping[str, Any] | None,
    satisfied_condition: Any,
    resume_checkpoint: Any,
) -> AuthorizedCodingLifecycleState:
    """Resume a hold only from its revision-bound checkpoint and condition."""

    if current.state not in HOLDING_STATES:
        raise CodingLifecycleAuthorityError("only waiting or blocked lifecycle can resume")
    if _safe_identity(resume_checkpoint) != current.resume_checkpoint:
        return _hold(
            current,
            state="blocked",
            condition=current.resume_condition or "satisfy_resume_condition",
            blockers=("resume_checkpoint_mismatch",),
        )
    if _safe_identity(satisfied_condition) != current.resume_condition:
        return _hold(
            current,
            state="blocked",
            condition=current.resume_condition or "satisfy_resume_condition",
            blockers=("resume_condition_mismatch",),
        )
    candidate = current.authority if observed_authority is None else CodingLifecycleAuthority.from_value(observed_authority)
    mismatches = current.authority.mismatch_fields(candidate)
    if mismatches:
        return _hold(
            current,
            state="blocked",
            condition="provide_matching_authority",
            blockers=tuple(f"authority_mismatch:{field}" for field in mismatches),
        )
    resumed_state = current.last_accepted_state or "clarifying"
    missing = candidate.missing_for_state(resumed_state)
    if missing:
        return _hold(
            current,
            state="waiting",
            condition=f"supply_authority_for:{resumed_state}",
            waiting_reasons=tuple(f"missing_authority:{field}" for field in missing),
            authority=candidate,
        )
    return AuthorizedCodingLifecycleState(
        coding_task_id=current.coding_task_id,
        repo_id=current.repo_id,
        state=resumed_state,
        last_accepted_state=resumed_state,
        authority=candidate,
        transition_ordinal=current.transition_ordinal + 1,
        legacy_schema=current.legacy_schema,
        legacy_payload_digest=current.legacy_payload_digest,
    )


def _new_hold(
    *,
    task_id: str,
    repo_id: str,
    authority: CodingLifecycleAuthority,
    state: str,
    last_accepted_state: str,
    condition: str,
    waiting_reasons: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
    ordinal: int = 0,
    legacy_schema: str = CODING_LIFECYCLE_SCHEMA,
    legacy_payload_digest: str = "",
) -> AuthorizedCodingLifecycleState:
    safe_condition = _safe_identity(condition)
    checkpoint = _resume_checkpoint(
        task_id=task_id,
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        last_accepted_state=last_accepted_state,
        condition=safe_condition,
        ordinal=ordinal,
    )
    return AuthorizedCodingLifecycleState(
        coding_task_id=task_id,
        repo_id=repo_id,
        state=state,
        last_accepted_state=last_accepted_state,
        authority=authority,
        transition_ordinal=ordinal,
        resume_condition=safe_condition,
        resume_checkpoint=checkpoint,
        waiting_reasons=tuple(_safe_identity(item) for item in waiting_reasons),
        blockers=tuple(_safe_identity(item) for item in blockers),
        legacy_schema=legacy_schema,
        legacy_payload_digest=legacy_payload_digest,
    )


def _hold(
    current: AuthorizedCodingLifecycleState,
    *,
    state: str,
    condition: str,
    waiting_reasons: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
    authority: CodingLifecycleAuthority | None = None,
) -> AuthorizedCodingLifecycleState:
    return _new_hold(
        task_id=current.coding_task_id,
        repo_id=current.repo_id,
        authority=authority or current.authority,
        state=state,
        last_accepted_state=current.last_accepted_state,
        condition=condition,
        waiting_reasons=waiting_reasons,
        blockers=blockers,
        ordinal=current.transition_ordinal + 1,
        legacy_schema=current.legacy_schema,
        legacy_payload_digest=current.legacy_payload_digest,
    )


def _completion_proof_error(
    authority: CodingLifecycleAuthority,
    proof: CodingLifecycleCompletionProof | None,
) -> str:
    if proof is None:
        return "completion_proof_missing"
    if proof.acceptance_decision_id != authority.acceptance_decision_id:
        return "completion_acceptance_mismatch"
    if proof.evidence_id != authority.evidence_id:
        return "completion_evidence_mismatch"
    if not proof.all_required_gates_closed:
        return "completion_gates_open"
    if not proof.independent_review:
        return "completion_independent_review_missing"
    if not proof.reviewer_id or proof.reviewer_id == authority.claim_owner:
        return "completion_reviewer_not_independent"
    return ""


def _resume_checkpoint(
    *,
    task_id: str,
    planning_item_id: str,
    planning_revision: str,
    last_accepted_state: str,
    condition: str,
    ordinal: int,
) -> str:
    return stable_payload_hash(
        {
            "task_id": task_id,
            "planning_item_id": planning_item_id,
            "planning_revision": planning_revision,
            "last_accepted_state": last_accepted_state,
            "condition": condition,
            "ordinal": ordinal,
        }
    )


def _scope_digest(scope: tuple[str, ...]) -> str:
    return stable_payload_hash({"normalized_claim_scope": scope})


def _normalize_scope(values: Iterable[Any] | Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        if not str(values).strip():
            raise CodingLifecycleAuthorityError("scope path cannot be empty")
        values = (values,)
    normalized: list[str] = []
    for index, value in enumerate(values):
        if index >= MAX_AUTHORITY_SCOPE_ENTRIES:
            raise CodingLifecycleAuthorityError(
                f"authority scope exceeds {MAX_AUTHORITY_SCOPE_ENTRIES} entries"
            )
        if not isinstance(value, str):
            raise CodingLifecycleAuthorityError("scope path must be a string")
        if value != value.strip():
            raise CodingLifecycleAuthorityError("scope path must not contain boundary whitespace")
        text = value.replace("\\", "/")
        if not text:
            raise CodingLifecycleAuthorityError("scope path cannot be empty")
        if (
            text.startswith("/")
            or text == "~"
            or text.startswith("~/")
            or text.split("/", 1)[0].startswith("~")
            or _DRIVE_RE.match(text)
        ):
            raise CodingLifecycleAuthorityError("scope path must be repository-relative")
        if not _STRICT_SCOPE_RE.fullmatch(text) or _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
            raise CodingLifecycleAuthorityError("scope path is malformed or private")
        raw_components = text.split("/")
        if any(component == "" for component in raw_components):
            raise CodingLifecycleAuthorityError("scope path contains an empty component")
        components: list[str] = []
        for component in raw_components:
            if component == ".":
                continue
            if component == "..":
                raise CodingLifecycleAuthorityError("scope path cannot contain parent traversal")
            components.append(component)
        safe = "/".join(components)
        if not safe:
            raise CodingLifecycleAuthorityError("scope path cannot name the repository root")
        normalized.append(safe)
    return tuple(dict.fromkeys(normalized))


def _scope_contains(parent: str, child: str) -> bool:
    parent_parts = tuple(parent.split("/"))
    child_parts = tuple(child.split("/"))
    return len(parent_parts) <= len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def _state_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in {*PRODUCTION_CODING_LIFECYCLE_STATES, *HOLDING_STATES}:
        raise CodingLifecycleAuthorityError("unsupported production lifecycle state")
    return text


def _safe_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if (
        len(text) > 180
        or not _SAFE_ID_RE.fullmatch(text)
        or _SECRET_RE.search(text)
        or _HOST_PATH_RE.search(text)
        or any(component == ".." for component in text.replace("\\", "/").split("/"))
    ):
        return stable_payload_hash(text)
    return text


def _strict_identity(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if value is None:
        if allow_empty:
            return ""
        raise CodingLifecycleAuthorityError(f"{field} is required")
    if not isinstance(value, str):
        raise CodingLifecycleAuthorityError(f"{field} is malformed or private")
    if value != value.strip():
        raise CodingLifecycleAuthorityError(f"{field} is malformed or private")
    text = value
    if not text:
        if allow_empty:
            return ""
        raise CodingLifecycleAuthorityError(f"{field} is required")
    if (
        len(text) > MAX_AUTHORITY_ID_LENGTH
        or
        not _STRICT_ID_RE.fullmatch(text)
        or _SECRET_RE.search(text)
        or _HOST_PATH_RE.search(text)
        or any(component == ".." for component in text.replace("\\", "/").split("/"))
    ):
        raise CodingLifecycleAuthorityError(f"{field} is malformed or private")
    return text


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    if key.lower() in {
        "authorization", "content", "credential", "diff", "env", "output", "password",
        "patch", "raw", "raw_content", "raw_log", "raw_output", "secret", "stderr",
        "stdout", "token",
    } and value not in (False, None, ""):
        raise CodingLifecycleAuthorityError("authority lifecycle contains a raw field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _reject_unsafe_payload(nested_value, key=str(nested_key))
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_unsafe_payload(item, key=key)
        return
    if isinstance(value, str) and (_SECRET_RE.search(value) or _HOST_PATH_RE.search(value)):
        raise CodingLifecycleAuthorityError("authority lifecycle contains private material")
