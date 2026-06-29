"""Trusted workflow-bound skill resolution.

This module maps trusted runtime metadata to mandatory workflow skills. It does
not inspect user prompts, extracted document text, file bytes, provider output,
or any other untrusted content.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


WORKFLOW_SKILL_RESOLUTION_SCHEMA = "odysseus.workflow_skills.resolution.v1"

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_FORBIDDEN_CONTEXT_KEYS = {
    "text",
    "prompt",
    "persisted_prompt",
    "content",
    "body",
    "raw_text",
    "full_text",
    "document_text",
    "extracted_text",
    "bytes",
    "payload",
    "chat_id",
    "file_id",
    "telegram_file_id",
    "path",
    "absolute_path",
    "host_path",
    "spool_path",
    "token",
    "secret",
    "password",
    "api_key",
}
_ANALYSIS_INTENTS = ("analyze", "summarize", "question_answer", "follow_up", "inspect")
_EXPORT_INTENTS = ("export", "convert")
_REVIEW_INTENTS = ("approve", "review", "route", "explain")
_INELIGIBLE_AUDITS = {"fail", "failed", "blocked", "no_go", "unsafe", "audit_failed"}


class WorkflowSkillError(ValueError):
    """Raised when workflow skill resolution input is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class WorkflowSkillTrigger:
    workflow_id: str
    channels: tuple[str, ...] = ()
    message_kinds: tuple[str, ...] = ()
    requires_recent_attachment: bool | None = None
    attachment_families: tuple[str, ...] = ()
    attachment_suffixes: tuple[str, ...] = ()
    universal_inbox_statuses: tuple[str, ...] = ()
    memory_write_intent_statuses: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    dsgvo_modes: tuple[str, ...] = ()

    def matches(self, context: Mapping[str, Any]) -> bool:
        return (
            _matches_any(self.channels, _token(context.get("channel"), field_name="channel", allow_empty=True))
            and _matches_any(self.message_kinds, _token(context.get("message_kind"), field_name="message_kind", allow_empty=True))
            and _matches_attachment_requirement(self.requires_recent_attachment, context)
            and _matches_any(self.attachment_families, _recent_token(context, "family"))
            and _matches_any(self.attachment_suffixes, _recent_suffix(context))
            and _matches_any(self.universal_inbox_statuses, _recent_token(context, "universal_inbox_status"))
            and _matches_any(self.memory_write_intent_statuses, _recent_token(context, "memory_write_intent_status"))
            and _matches_any(self.intents, _token(context.get("intent"), field_name="intent", allow_empty=True))
            and _matches_any(self.dsgvo_modes, _token(context.get("dsgvo_mode"), field_name="dsgvo_mode", allow_empty=True))
        )


@dataclass(frozen=True, slots=True)
class WorkflowSkillBinding:
    workflow_id: str
    trigger: WorkflowSkillTrigger
    skill_name: str
    required: bool = True
    priority: int = 100
    reason: str = ""
    block_if_missing: bool = True
    allowed_skill_sources: tuple[str, ...] = ("system", "admin", "user")
    allowed_statuses: tuple[str, ...] = ("published",)
    min_confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkflowSkillResolution:
    required_skill_names: tuple[str, ...]
    optional_skill_names: tuple[str, ...]
    requested_toolsets: tuple[str, ...]
    blockers: tuple[str, ...]
    audit_reasons: tuple[str, ...]
    matched_workflows: tuple[str, ...]
    schema: str = WORKFLOW_SKILL_RESOLUTION_SCHEMA

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "required_skill_names": self.required_skill_names,
            "optional_skill_names": self.optional_skill_names,
            "requested_toolsets": self.requested_toolsets,
            "blockers": self.blockers,
            "blocked": self.blocked,
            "audit_reasons": self.audit_reasons,
            "matched_workflows": self.matched_workflows,
        }


DEFAULT_WORKFLOW_SKILL_BINDINGS = (
    WorkflowSkillBinding(
        workflow_id="telegram-document-analysis-workflow",
        trigger=WorkflowSkillTrigger(
            workflow_id="telegram-document-analysis-workflow",
            channels=("telegram",),
            message_kinds=("text",),
            requires_recent_attachment=True,
            attachment_families=("document",),
            universal_inbox_statuses=("processed", "partial", "review", "go"),
            intents=_ANALYSIS_INTENTS,
        ),
        skill_name="telegram-document-analysis-workflow",
        reason="telegram text follow-up references a recent Universal Inbox document",
    ),
    WorkflowSkillBinding(
        workflow_id="telegram-document-export-workflow",
        trigger=WorkflowSkillTrigger(
            workflow_id="telegram-document-export-workflow",
            channels=("telegram",),
            message_kinds=("text",),
            requires_recent_attachment=True,
            attachment_families=("document", "image"),
            intents=_EXPORT_INTENTS,
        ),
        skill_name="telegram-document-export-workflow",
        reason="telegram text follow-up requests export or conversion of recent attachment",
        priority=110,
    ),
    WorkflowSkillBinding(
        workflow_id="universal-inbox-routing-review-workflow",
        trigger=WorkflowSkillTrigger(
            workflow_id="universal-inbox-routing-review-workflow",
            requires_recent_attachment=True,
            universal_inbox_statuses=("partial", "review", "no_go", "blocked", "failed"),
            intents=_REVIEW_INTENTS,
        ),
        skill_name="universal-inbox-routing-review-workflow",
        reason="Universal Inbox item requires routing or policy review",
        priority=90,
    ),
)


def resolve_workflow_skills(
    context: Mapping[str, Any],
    *,
    skills: Iterable[Mapping[str, Any]] = (),
    bindings: Iterable[WorkflowSkillBinding] = DEFAULT_WORKFLOW_SKILL_BINDINGS,
) -> WorkflowSkillResolution:
    """Resolve mandatory workflow skills from trusted runtime metadata only."""

    safe_context = _validate_context(context)
    skill_index = {_skill_name(skill): skill for skill in skills if _skill_name(skill)}
    required: list[str] = []
    optional: list[str] = []
    toolsets: list[str] = []
    blockers: list[str] = []
    reasons: list[str] = []
    workflows: list[str] = []

    for binding in sorted(tuple(bindings), key=lambda item: (-int(item.priority), item.workflow_id)):
        if not binding.trigger.matches(safe_context):
            continue
        workflows.append(binding.workflow_id)
        reasons.append(f"{binding.workflow_id}:{binding.reason or 'matched'}")
        target = binding.skill_name
        skill = skill_index.get(target)
        if skill is None:
            if binding.required and binding.block_if_missing:
                blockers.append(f"required_skill_missing:{target}")
            continue
        eligible, reason = is_required_workflow_skill_eligible(skill, binding=binding)
        if not eligible:
            if binding.required and binding.block_if_missing:
                blockers.append(f"required_skill_ineligible:{target}:{reason}")
            reasons.append(f"{target}:ineligible:{reason}")
            continue
        (required if binding.required else optional).append(target)
        for toolset in _safe_toolsets(skill.get("requires_toolsets") or ()):
            if toolset not in toolsets:
                toolsets.append(toolset)

    return WorkflowSkillResolution(
        required_skill_names=tuple(dict.fromkeys(required)),
        optional_skill_names=tuple(dict.fromkeys(optional)),
        requested_toolsets=tuple(toolsets),
        blockers=tuple(dict.fromkeys(blockers)),
        audit_reasons=tuple(dict.fromkeys(reasons)),
        matched_workflows=tuple(dict.fromkeys(workflows)),
    )


def is_required_workflow_skill_eligible(
    skill: Mapping[str, Any],
    *,
    binding: WorkflowSkillBinding,
) -> tuple[bool, str]:
    """Return whether a skill may be used as mandatory workflow rail."""

    status = _token(skill.get("status") or "", field_name="skill.status", allow_empty=True)
    if status not in {_normalize_choice(item) for item in binding.allowed_statuses}:
        return False, "status_not_published"
    source = _token(skill.get("source") or "", field_name="skill.source", allow_empty=True)
    if source == "teacher-escalation":
        return False, "teacher_escalation_not_allowed"
    if source not in {_normalize_choice(item) for item in binding.allowed_skill_sources}:
        return False, "source_not_allowed"
    try:
        confidence = float(skill.get("confidence", 1.0))
    except (TypeError, ValueError):
        return False, "confidence_invalid"
    if confidence < binding.min_confidence:
        return False, "confidence_below_minimum"
    audit = _token(skill.get("audit_verdict") or "", field_name="audit_verdict", allow_empty=True)
    if audit in _INELIGIBLE_AUDITS:
        return False, "audit_verdict_blocks_required_workflow"
    necessity = skill.get("necessity")
    if isinstance(necessity, Mapping) and necessity.get("necessary") is False:
        return False, "necessity_review_marked_unnecessary"
    if skill.get("_legacy"):
        return False, "legacy_skill_not_allowed"
    if skill.get("eligible_for_required_workflows") is False:
        return False, "required_workflow_eligibility_disabled"
    return True, "eligible"


def _validate_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(context, Mapping):
        raise WorkflowSkillError("workflow context must be a mapping")
    _reject_forbidden_context_keys(context)
    # Normalize the fields used by triggers once to fail closed on malformed
    # trusted metadata.
    _token(context.get("channel"), field_name="channel", allow_empty=True)
    _token(context.get("message_kind"), field_name="message_kind", allow_empty=True)
    _token(context.get("intent"), field_name="intent", allow_empty=True)
    _token(context.get("dsgvo_mode"), field_name="dsgvo_mode", allow_empty=True)
    recent = context.get("recent_attachment") or {}
    if recent and not isinstance(recent, Mapping):
        raise WorkflowSkillError("recent_attachment must be a mapping")
    if recent:
        _token(recent.get("family"), field_name="recent_attachment.family", allow_empty=True)
        _token(recent.get("universal_inbox_status"), field_name="recent_attachment.universal_inbox_status", allow_empty=True)
        _token(recent.get("memory_write_intent_status"), field_name="recent_attachment.memory_write_intent_status", allow_empty=True)
        _recent_suffix(context)
    return context


def _reject_forbidden_context_keys(payload: Mapping[str, Any], *, prefix: str = "") -> None:
    for key, value in payload.items():
        key_text = str(key).strip().lower()
        if key_text in _FORBIDDEN_CONTEXT_KEYS:
            path = f"{prefix}.{key_text}" if prefix else key_text
            raise WorkflowSkillError(f"workflow context contains untrusted field: {path}")
        if isinstance(value, Mapping):
            _reject_forbidden_context_keys(value, prefix=f"{prefix}.{key_text}" if prefix else key_text)


def _matches_any(allowed: tuple[str, ...], value: str) -> bool:
    if not allowed:
        return True
    normalized = {_normalize_choice(item) for item in allowed}
    return value in normalized


def _matches_attachment_requirement(requirement: bool | None, context: Mapping[str, Any]) -> bool:
    if requirement is None:
        return True
    recent = context.get("recent_attachment") if isinstance(context.get("recent_attachment"), Mapping) else {}
    present = bool(recent.get("present")) if recent else False
    return present is bool(requirement)


def _recent_token(context: Mapping[str, Any], key: str) -> str:
    recent = context.get("recent_attachment") if isinstance(context.get("recent_attachment"), Mapping) else {}
    return _token(recent.get(key), field_name=f"recent_attachment.{key}", allow_empty=True)


def _recent_suffix(context: Mapping[str, Any]) -> str:
    recent = context.get("recent_attachment") if isinstance(context.get("recent_attachment"), Mapping) else {}
    value = str(recent.get("suffix") or "").strip().lower()
    if not value:
        return ""
    if not value.startswith("."):
        value = f".{value}"
    if not re.fullmatch(r"\.[a-z0-9]{1,16}", value):
        raise WorkflowSkillError("recent_attachment.suffix must be a safe suffix")
    return value


def _safe_toolsets(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        token = _toolset_token(value)
        if token not in result:
            result.append(token)
    return tuple(result)


def _skill_name(skill: Mapping[str, Any]) -> str:
    if not isinstance(skill, Mapping):
        return ""
    try:
        return _token(skill.get("name") or skill.get("id") or "", field_name="skill.name", allow_empty=True)
    except WorkflowSkillError:
        return ""


def _normalize_choice(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _toolset_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise WorkflowSkillError("requires_toolsets must not be empty")
    if not re.fullmatch(r"^[a-z][a-z0-9_:-]{0,79}$", text):
        raise WorkflowSkillError("requires_toolsets must be a safe toolset name")
    return text


def _token(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        if allow_empty:
            return ""
        raise WorkflowSkillError(f"{field_name} must not be empty")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise WorkflowSkillError(f"{field_name} must be a safe token")
    return text
