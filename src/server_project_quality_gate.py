"""Project quality-gate planning for universal server projects."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.live_quality_gate_command_runner import (
    LiveQualityGateCommandPlan,
    build_live_quality_gate_command_plan,
)
from src.server_project_registry import ServerProjectRecord


_GATE_TYPES = ("test", "build", "smoke", "evidence")
_DECISIONS = ("plan_ready", "hold", "blocked")
_REDACTED_LOG_POLICY = "command-only-no-secrets"
_EVIDENCE_GATE_TYPES = {"build", "evidence"}
_COMMAND_GATE_TYPES = {"test", "smoke"}
_EVIDENCE_KIND_BY_GATE_TYPE = {
    "test": "test_result",
    "build": "build_artifact",
    "smoke": "smoke_result",
    "evidence": "external_evidence",
}
_EVIDENCE_STATES = {"green", "yellow", "red", "missing", "pending"}
_EVIDENCE_RESULT_LABELS = {"pass", "partial", "fail", "blocked", "missing", "pending"}
_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]?\s*\S*")
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|(?<![A-Za-z0-9._-])/(?:[^\s/`]+/)+)")
_BLOCKED_TEXT = (
    "git reset",
    "git clean",
    "rm -rf",
    "remove-item -recurse",
    "curl ",
    "wget ",
    "invoke-webrequest",
    "systemctl",
    "podman",
    "docker",
    "ssh ",
    "scp ",
    "gh repo create",
)


class ServerProjectQualityGateError(ValueError):
    """Raised when a project quality gate cannot be safely planned."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 260) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectQualityGateError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectQualityGateError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectQualityGateError(f"{field_name} appears to contain secret material")
    if _ABS_PATH_RE.search(text):
        raise ServerProjectQualityGateError(f"{field_name} must not contain host-local absolute paths")
    return text


def _normalize_gate_type(value: Any) -> str:
    gate_type = _normalize_text(value, field_name="gate_type").lower().replace("-", "_")
    if gate_type not in _GATE_TYPES:
        raise ServerProjectQualityGateError(f"unsupported gate_type: {value!r}")
    return gate_type


def _normalize_required(value: Any) -> bool:
    if type(value) is not bool:
        raise ServerProjectQualityGateError("required must be a boolean")
    return value


def _normalize_timeout(value: Any) -> int:
    if type(value) is not int:
        raise ServerProjectQualityGateError("timeout_seconds must be an integer")
    if value < 1 or value > 300:
        raise ServerProjectQualityGateError("timeout_seconds must be between 1 and 300")
    return value


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServerProjectQualityGateError(
            f"{field_name} must be a valid ISO-8601 timestamp"
        ) from exc
    return text


def _normalize_evidence_state(value: Any) -> str:
    state = _normalize_text(value, field_name="state").lower().replace("-", "_")
    if state not in _EVIDENCE_STATES:
        raise ServerProjectQualityGateError(f"unsupported evidence state: {value!r}")
    return state


def _normalize_result_label(value: Any) -> str:
    label = _normalize_text(value, field_name="result_label").lower().replace("-", "_")
    if label not in _EVIDENCE_RESULT_LABELS:
        raise ServerProjectQualityGateError(
            f"unsupported evidence result_label: {value!r}"
        )
    return label


def _is_blocked_command(command_text: str) -> bool:
    lowered = command_text.lower()
    return any(fragment in lowered for fragment in _BLOCKED_TEXT)


def _command_class_for_gate(gate_type: str, command_text: str) -> str:
    lowered = command_text.lower()
    if _is_blocked_command(command_text):
        if any(fragment in lowered for fragment in ("curl ", "wget ", "invoke-webrequest", "ssh ", "scp ")):
            return "blocked_network"
        if any(fragment in lowered for fragment in ("podman", "docker", "systemctl")):
            return "blocked_host_command"
        return "blocked_destructive"
    if gate_type in {"test", "smoke"}:
        if not lowered.startswith("python -m pytest "):
            return "blocked_host_command"
        return "focused_pytest"
    return "evidence_check"


def _default_specs(record: ServerProjectRecord) -> tuple["ProjectQualityGateSpec", ...]:
    slug = record.project_slug
    return (
        ProjectQualityGateSpec.create(
            gate_id="focused_tests",
            gate_type="test",
            command_text=f"python -m pytest tests/test_{slug.replace('-', '_')}.py -q",
            timeout_seconds=300,
            required=True,
        ),
        ProjectQualityGateSpec.create(
            gate_id="build_evidence",
            gate_type="build",
            evidence_requirement=f"verified build artifact receipt required for {slug}",
            timeout_seconds=60,
            required=True,
        ),
        ProjectQualityGateSpec.create(
            gate_id="smoke_tests",
            gate_type="smoke",
            command_text=f"python -m pytest tests/test_{slug.replace('-', '_')}_smoke.py -q",
            timeout_seconds=300,
            required=True,
        ),
    )


@dataclass(frozen=True, slots=True)
class ProjectQualityGateSpec:
    gate_id: str
    gate_type: str
    command_text: str | None
    evidence_requirement: str | None
    timeout_seconds: int
    required: bool

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        gate_type: Any,
        command_text: Any | None = None,
        evidence_requirement: Any | None = None,
        timeout_seconds: Any = 300,
        required: Any = True,
    ) -> "ProjectQualityGateSpec":
        normalized_type = _normalize_gate_type(gate_type)
        if normalized_type in _COMMAND_GATE_TYPES:
            if command_text is None:
                raise ServerProjectQualityGateError(
                    "command-backed gates require command_text"
                )
            if evidence_requirement is not None:
                raise ServerProjectQualityGateError(
                    "command-backed gates must not define evidence_requirement"
                )
            normalized_command = _normalize_text(
                command_text,
                field_name="command_text",
            )
            normalized_requirement = None
        else:
            if evidence_requirement is None:
                raise ServerProjectQualityGateError(
                    "evidence-backed gates require evidence_requirement"
                )
            if command_text is not None:
                raise ServerProjectQualityGateError(
                    "evidence-backed gates must not define command_text"
                )
            normalized_command = None
            normalized_requirement = _normalize_text(
                evidence_requirement,
                field_name="evidence_requirement",
            )
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id", max_len=80).lower().replace("-", "_"),
            gate_type=normalized_type,
            command_text=normalized_command,
            evidence_requirement=normalized_requirement,
            timeout_seconds=_normalize_timeout(timeout_seconds),
            required=_normalize_required(required),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_type": self.gate_type,
            "command_text": self.command_text,
            "evidence_requirement": self.evidence_requirement,
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ProjectQualityGateEvidence:
    gate_id: str
    evidence_kind: str
    subject_digest: str
    state: str
    result_label: str
    checked_at: str
    summary: str
    evidence_digest: str | None

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        evidence_kind: Any,
        subject_digest: Any,
        state: Any,
        result_label: Any,
        checked_at: Any,
        summary: Any,
        evidence_digest: Any | None = None,
    ) -> "ProjectQualityGateEvidence":
        normalized_kind = _normalize_text(
            evidence_kind,
            field_name="evidence_kind",
        ).lower().replace("-", "_")
        if normalized_kind not in set(_EVIDENCE_KIND_BY_GATE_TYPE.values()):
            raise ServerProjectQualityGateError(
                f"unsupported evidence_kind: {evidence_kind!r}"
            )
        normalized_subject_digest = _normalize_text(
            subject_digest,
            field_name="subject_digest",
        ).lower()
        if not _SHA256_DIGEST_RE.fullmatch(normalized_subject_digest):
            raise ServerProjectQualityGateError(
                "subject_digest must use sha256:<64 lowercase hex>"
            )
        normalized_state = _normalize_evidence_state(state)
        normalized_label = _normalize_result_label(result_label)
        normalized_digest = (
            _normalize_text(evidence_digest, field_name="evidence_digest").lower()
            if evidence_digest is not None
            else None
        )
        if normalized_digest is not None and not _SHA256_DIGEST_RE.fullmatch(
            normalized_digest
        ):
            raise ServerProjectQualityGateError(
                "evidence_digest must use sha256:<64 lowercase hex>"
            )
        if normalized_state == "green" and normalized_label != "pass":
            raise ServerProjectQualityGateError(
                "green evidence must use result_label='pass'"
            )
        if normalized_state == "green" and normalized_digest is None:
            raise ServerProjectQualityGateError(
                "green evidence requires an immutable evidence_digest"
            )
        if normalized_state == "yellow" and normalized_label != "partial":
            raise ServerProjectQualityGateError(
                "yellow evidence must use result_label='partial'"
            )
        if normalized_state == "red" and normalized_label not in {"fail", "blocked"}:
            raise ServerProjectQualityGateError(
                "red evidence must use result_label='fail' or 'blocked'"
            )
        if normalized_state == "missing" and normalized_label != "missing":
            raise ServerProjectQualityGateError(
                "missing evidence must use result_label='missing'"
            )
        if normalized_state == "pending" and normalized_label != "pending":
            raise ServerProjectQualityGateError(
                "pending evidence must use result_label='pending'"
            )
        return cls(
            gate_id=_normalize_text(
                gate_id,
                field_name="gate_id",
                max_len=80,
            ).lower().replace("-", "_"),
            evidence_kind=normalized_kind,
            subject_digest=normalized_subject_digest,
            state=normalized_state,
            result_label=normalized_label,
            checked_at=_normalize_timestamp(checked_at, field_name="checked_at"),
            summary=_normalize_text(summary, field_name="summary"),
            evidence_digest=normalized_digest,
        )

    @property
    def ready(self) -> bool:
        return (
            self.state == "green"
            and self.result_label == "pass"
            and self.evidence_digest is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "evidence_kind": self.evidence_kind,
            "subject_digest": self.subject_digest,
            "state": self.state,
            "result_label": self.result_label,
            "checked_at": self.checked_at,
            "summary": self.summary,
            "evidence_digest": self.evidence_digest,
        }


def project_quality_gate_subject_digest(spec: ProjectQualityGateSpec) -> str:
    """Return the immutable binding for one canonical gate specification."""

    if not isinstance(spec, ProjectQualityGateSpec):
        raise ServerProjectQualityGateError(
            "spec must be a ProjectQualityGateSpec"
        )
    canonical_spec = ProjectQualityGateSpec.create(**spec.to_dict())
    encoded = json.dumps(
        canonical_spec.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_project_quality_gate_evidence(
    *,
    spec: ProjectQualityGateSpec,
    state: Any,
    result_label: Any,
    checked_at: Any,
    summary: Any,
    evidence_digest: Any | None = None,
) -> ProjectQualityGateEvidence:
    """Build evidence bound to the exact canonical gate specification."""

    canonical_spec = ProjectQualityGateSpec.create(**spec.to_dict())
    return ProjectQualityGateEvidence.create(
        gate_id=canonical_spec.gate_id,
        evidence_kind=_EVIDENCE_KIND_BY_GATE_TYPE[canonical_spec.gate_type],
        subject_digest=project_quality_gate_subject_digest(canonical_spec),
        state=state,
        result_label=result_label,
        checked_at=checked_at,
        summary=summary,
        evidence_digest=evidence_digest,
    )


@dataclass(frozen=True, slots=True)
class ProjectQualityGateResult:
    spec: ProjectQualityGateSpec
    command_plan: LiveQualityGateCommandPlan | None
    evidence: ProjectQualityGateEvidence | None

    @property
    def decision(self) -> str:
        if (
            self.command_plan is not None
            and self.command_plan.decision.decision != "plan_ready"
        ):
            return self.command_plan.decision.decision
        if self.evidence is not None and self.evidence.ready:
            return "plan_ready"
        if self.evidence is not None and self.evidence.result_label in {"fail", "blocked"}:
            return "blocked"
        return "hold"

    @property
    def ready(self) -> bool:
        return self.decision == "plan_ready"

    @property
    def blocker(self) -> str:
        if self.ready:
            return ""
        if (
            self.command_plan is not None
            and self.command_plan.decision.decision != "plan_ready"
        ):
            return f"{self.spec.gate_id}: {self.command_plan.decision.next_action}"
        if self.evidence is None:
            return (
                f"{self.spec.gate_id}: structured immutable evidence is missing"
            )
        return (
            f"{self.spec.gate_id}: structured evidence is "
            f"{self.evidence.state}/{self.evidence.result_label}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "decision": self.decision,
            "ready": self.ready,
            "blocker": self.blocker,
            "gate_mode": (
                "command"
                if self.command_plan is not None
                else "structured_evidence"
            ),
            "command_plan": (
                self.command_plan.to_dict()
                if self.command_plan is not None
                else None
            ),
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ProjectQualityGateBundle:
    project_slug: str
    chat_scope: str
    decision: str
    required_gate_count: int
    ready_gate_count: int
    blockers: tuple[str, ...]
    results: tuple[ProjectQualityGateResult, ...]
    next_human_decision: str

    @property
    def deploy_gate_ready(self) -> bool:
        return self.decision == "plan_ready"

    @property
    def focused_tests_green(self) -> bool:
        focused_results = tuple(
            result
            for result in self.results
            if result.spec.required and result.spec.gate_type in _COMMAND_GATE_TYPES
        )
        return bool(focused_results) and all(result.ready for result in focused_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "chat_scope": self.chat_scope,
            "decision": self.decision,
            "deploy_gate_ready": self.deploy_gate_ready,
            "focused_tests_green": self.focused_tests_green,
            "required_gate_count": self.required_gate_count,
            "ready_gate_count": self.ready_gate_count,
            "blockers": list(self.blockers),
            "results": [result.to_dict() for result in self.results],
            "next_human_decision": self.next_human_decision,
        }


def build_project_quality_gate_bundle(
    *,
    record: ServerProjectRecord,
    gate_specs: Iterable[ProjectQualityGateSpec | dict[str, Any]] | None = None,
    evidence_inputs: Iterable[
        ProjectQualityGateEvidence | Mapping[str, Any]
    ] | None = None,
) -> ProjectQualityGateBundle:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectQualityGateError("record must be a ServerProjectRecord")
    specs = _coerce_specs(record, gate_specs)
    evidence_by_gate = _coerce_evidence(specs, evidence_inputs)
    results = tuple(
        _build_result(spec, evidence_by_gate.get(spec.gate_id))
        for spec in specs
    )
    required_results = tuple(result for result in results if result.spec.required)
    blockers = tuple(result.blocker for result in required_results if result.blocker)
    if any(result.decision == "blocked" for result in required_results):
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"
    next_human_decision = (
        "Quality gates are ready for operator review; keep execution separate from this dry-run bundle."
        if decision == "plan_ready"
        else "Replace blocked or incomplete project gates with focused, bounded, redacted checks."
    )
    return ProjectQualityGateBundle(
        project_slug=record.project_slug,
        chat_scope=record.chat_scope,
        decision=decision,
        required_gate_count=len(required_results),
        ready_gate_count=sum(1 for result in required_results if result.ready),
        blockers=blockers,
        results=results,
        next_human_decision=next_human_decision,
    )


def _coerce_specs(
    record: ServerProjectRecord,
    gate_specs: Iterable[ProjectQualityGateSpec | dict[str, Any]] | None,
) -> tuple[ProjectQualityGateSpec, ...]:
    if gate_specs is None:
        return _default_specs(record)
    specs: list[ProjectQualityGateSpec] = []
    for raw in gate_specs:
        if isinstance(raw, ProjectQualityGateSpec):
            specs.append(ProjectQualityGateSpec.create(**raw.to_dict()))
        elif isinstance(raw, dict):
            specs.append(ProjectQualityGateSpec.create(**raw))
        else:
            raise ServerProjectQualityGateError("gate_specs must contain ProjectQualityGateSpec objects or dicts")
    if not specs:
        raise ServerProjectQualityGateError("at least one quality gate is required")
    gate_ids = [spec.gate_id for spec in specs]
    if len(set(gate_ids)) != len(gate_ids):
        raise ServerProjectQualityGateError("duplicate quality gate_id")
    return tuple(specs)


def _coerce_evidence(
    specs: tuple[ProjectQualityGateSpec, ...],
    evidence_inputs: Iterable[
        ProjectQualityGateEvidence | Mapping[str, Any]
    ] | None,
) -> dict[str, ProjectQualityGateEvidence]:
    specs_by_id = {spec.gate_id: spec for spec in specs}
    evidence_by_gate: dict[str, ProjectQualityGateEvidence] = {}
    for raw in evidence_inputs if evidence_inputs is not None else ():
        if isinstance(raw, ProjectQualityGateEvidence):
            evidence = ProjectQualityGateEvidence.create(**raw.to_dict())
        elif isinstance(raw, Mapping):
            evidence = ProjectQualityGateEvidence.create(**dict(raw))
        else:
            raise ServerProjectQualityGateError(
                "evidence_inputs must contain ProjectQualityGateEvidence objects or mappings"
            )
        if evidence.gate_id not in specs_by_id:
            raise ServerProjectQualityGateError(
                f"evidence gate_id is unknown: {evidence.gate_id}"
            )
        if evidence.gate_id in evidence_by_gate:
            raise ServerProjectQualityGateError(
                f"duplicate evidence gate_id: {evidence.gate_id}"
            )
        expected_kind = _EVIDENCE_KIND_BY_GATE_TYPE[
            specs_by_id[evidence.gate_id].gate_type
        ]
        if evidence.evidence_kind != expected_kind:
            raise ServerProjectQualityGateError(
                f"evidence_kind for {evidence.gate_id} must be {expected_kind}"
            )
        expected_subject_digest = project_quality_gate_subject_digest(
            specs_by_id[evidence.gate_id]
        )
        if evidence.subject_digest != expected_subject_digest:
            raise ServerProjectQualityGateError(
                f"subject_digest for {evidence.gate_id} does not match the gate specification"
            )
        evidence_by_gate[evidence.gate_id] = evidence
    return evidence_by_gate


def _build_result(
    spec: ProjectQualityGateSpec,
    evidence: ProjectQualityGateEvidence | None,
) -> ProjectQualityGateResult:
    if spec.gate_type in _EVIDENCE_GATE_TYPES:
        return ProjectQualityGateResult(
            spec=spec,
            command_plan=None,
            evidence=evidence,
        )
    if spec.command_text is None:  # pragma: no cover - constructor invariant
        raise ServerProjectQualityGateError(
            "command-backed gate is missing command_text"
        )
    command_class = _command_class_for_gate(spec.gate_type, spec.command_text)
    command_plan = build_live_quality_gate_command_plan(
        command_class=command_class,
        command_text=spec.command_text,
        timeout_seconds=spec.timeout_seconds,
        redacted_log_policy=_REDACTED_LOG_POLICY,
        operator_approval_required=True,
    )
    return ProjectQualityGateResult(
        spec=spec,
        command_plan=command_plan,
        evidence=evidence,
    )


def project_quality_gate_bundle_is_canonical(
    *,
    record: ServerProjectRecord,
    bundle: Any,
) -> bool:
    """Return whether a bundle exactly matches a fresh builder reconstruction."""

    if not isinstance(record, ServerProjectRecord):
        return False
    if not isinstance(bundle, ProjectQualityGateBundle):
        return False
    try:
        rebuilt = build_project_quality_gate_bundle(
            record=record,
            gate_specs=tuple(result.spec for result in bundle.results),
            evidence_inputs=tuple(
                result.evidence
                for result in bundle.results
                if result.evidence is not None
            ),
        )
        return rebuilt.to_dict() == bundle.to_dict()
    except (AttributeError, TypeError, ValueError):
        return False
