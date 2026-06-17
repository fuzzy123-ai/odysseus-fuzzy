"""Operator handoff index models for activation foundation review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import ActivationAuditError
from src.orchestration_activation_foundation_closure import (
    ActivationFoundationClosureBundle,
    ClosureBundleStatus,
)


_SECTION_IDS = (
    "purpose",
    "completed_foundation_artifacts",
    "verification_tests",
    "runtime_no_go_list",
    "operator_checklist",
    "next_manual_gate",
    "followup_slices",
)

_DEFAULT_RUNTIME_NO_GO_LIST = (
    "thread_sends",
    "scheduler_loop",
    "git_runner",
    "test_runner",
    "provider_rag_runtime",
    "telegram_delivery",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise ActivationAuditError("unsupported operator handoff section_id")
    return text


def _normalize_runtime_no_go(values: Iterable[Any] | None) -> tuple[str, ...]:
    raw_values = _DEFAULT_RUNTIME_NO_GO_LIST if values is None else tuple(values)
    normalized = [_normalize_text(value, field_name="runtime_no_go_item").lower().replace(" ", "_") for value in raw_values]
    return tuple(sorted(dict.fromkeys(normalized)))


@dataclass(frozen=True, slots=True)
class HandoffIndexSection:
    section_id: str
    summary: str
    detail_count: int

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        summary: Any,
        detail_count: int = 0,
    ) -> "HandoffIndexSection":
        if detail_count < 0:
            raise ActivationAuditError("detail_count must be non-negative")
        return cls(
            section_id=_normalize_section_id(section_id),
            summary=_normalize_text(summary, field_name="summary"),
            detail_count=detail_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "summary": self.summary,
            "detail_count": self.detail_count,
        }


@dataclass(frozen=True, slots=True)
class ActivationOperatorHandoffIndex:
    overall_status: str
    runtime_no_go_list: tuple[str, ...]
    sections: tuple[HandoffIndexSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "runtime_no_go_list": self.runtime_no_go_list,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Operator Activation Handoff Index",
            "",
            f"- Overall status: `{self.overall_status}`",
            f"- Runtime no-go list: {', '.join(self.runtime_no_go_list) if self.runtime_no_go_list else 'none'}",
            "",
        ]
        for section in self.sections:
            lines.extend(
                [
                    f"## {section.section_id}",
                    f"- Summary: {section.summary}",
                    f"- Count: {section.detail_count}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


def build_operator_handoff_index(
    *,
    closure_bundle: ActivationFoundationClosureBundle | None = None,
    runtime_no_go_list: Iterable[Any] | None = None,
) -> ActivationOperatorHandoffIndex:
    if closure_bundle is not None and not isinstance(closure_bundle, ActivationFoundationClosureBundle):
        raise ActivationAuditError("closure_bundle must be an ActivationFoundationClosureBundle or None")

    normalized_no_go = _normalize_runtime_no_go(runtime_no_go_list)
    sections_source = () if closure_bundle is None else closure_bundle.sections

    overall_status = (
        "incomplete"
        if closure_bundle is None
        else closure_bundle.status.value
    )

    sections = (
        HandoffIndexSection.create(
            section_id="purpose",
            summary="summarize foundation-only activation readiness for an operator handoff without enabling runtime execution",
            detail_count=1,
        ),
        HandoffIndexSection.create(
            section_id="completed_foundation_artifacts",
            summary=(
                "foundation closure bundle is present"
                if closure_bundle is not None
                else "foundation closure bundle is not yet assembled"
            ),
            detail_count=len(sections_source),
        ),
        HandoffIndexSection.create(
            section_id="verification_tests",
            summary="verification remains model-level and should be confirmed from recorded evidence, not rerun from this index",
            detail_count=0 if closure_bundle is None else 1,
        ),
        HandoffIndexSection.create(
            section_id="runtime_no_go_list",
            summary="runtime execution remains out of scope for operator handoff review",
            detail_count=len(normalized_no_go),
        ),
        HandoffIndexSection.create(
            section_id="operator_checklist",
            summary=(
                "review the closure bundle state and confirm runtime gates stay closed"
                if closure_bundle is not None
                else "assemble closure bundle before operator checklist review"
            ),
            detail_count=0 if closure_bundle is None else len(sections_source),
        ),
        HandoffIndexSection.create(
            section_id="next_manual_gate",
            summary=(
                "next manual gate is operator review of the foundation closure bundle"
                if closure_bundle is not None
                else "next manual gate cannot start until the closure bundle exists"
            ),
            detail_count=1,
        ),
        HandoffIndexSection.create(
            section_id="followup_slices",
            summary=(
                "follow-up runtime slices remain deferred after foundation closure"
                if closure_bundle is not None and closure_bundle.status in {ClosureBundleStatus.FOUNDATION_READY, ClosureBundleStatus.RUNTIME_BLOCKED}
                else "follow-up slices remain unresolved until foundation closure is reviewable"
            ),
            detail_count=len(normalized_no_go),
        ),
    )

    return ActivationOperatorHandoffIndex(
        overall_status=overall_status,
        runtime_no_go_list=normalized_no_go,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
