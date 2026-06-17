"""Regression and evidence index models for activation foundation review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import ActivationAuditError
from src.orchestration_activation_foundation_closure import ActivationFoundationClosureBundle
from src.orchestration_activation_operator_handoff_index import ActivationOperatorHandoffIndex


_SECTION_IDS = (
    "foundation_artifacts",
    "required_regression_tests",
    "operator_review_order",
    "runtime_capabilities_still_disabled",
    "evidence_boundaries",
    "release_gate_summary",
    "next_post_foundation_slices",
)

_DEFAULT_RUNTIME_DISABLED = (
    "thread_sends",
    "scheduler_loop",
    "git_runner",
    "test_runner",
    "provider_rag_runtime",
    "telegram_delivery",
)

_DEFAULT_TEST_REFS = (
    "tests/test_orchestration_activation_audit_trail.py",
    "tests/test_orchestration_activation_handoff_checklist.py",
    "tests/test_orchestration_operator_activation_packet.py",
    "tests/test_orchestration_activation_packet_renderers.py",
    "tests/test_orchestration_activation_readiness_index.py",
    "tests/test_orchestration_activation_foundation_closure.py",
    "tests/test_orchestration_activation_operator_handoff_index.py",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise ActivationAuditError("unsupported regression index section_id")
    return text


def _normalize_ref_list(values: Iterable[Any]) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name="reference").replace("\\", "/") for value in values]
    return tuple(sorted(dict.fromkeys(normalized)))


@dataclass(frozen=True, slots=True)
class RegressionTestReference:
    test_ref: str

    @classmethod
    def create(cls, *, test_ref: Any) -> "RegressionTestReference":
        return cls(test_ref=_normalize_text(test_ref, field_name="test_ref").replace("\\", "/"))

    def to_dict(self) -> dict[str, Any]:
        return {"test_ref": self.test_ref}


@dataclass(frozen=True, slots=True)
class RegressionIndexSection:
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
    ) -> "RegressionIndexSection":
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
class ActivationFoundationRegressionIndex:
    overall_status: str
    required_regression_tests: tuple[RegressionTestReference, ...]
    runtime_capabilities_still_disabled: tuple[str, ...]
    sections: tuple[RegressionIndexSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "required_regression_tests": tuple(item.to_dict() for item in self.required_regression_tests),
            "runtime_capabilities_still_disabled": self.runtime_capabilities_still_disabled,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Activation Foundation Regression Index",
            "",
            f"- Overall status: `{self.overall_status}`",
            f"- Runtime still disabled: {', '.join(self.runtime_capabilities_still_disabled)}",
            "",
            "## Required Regression Tests",
        ]
        for item in self.required_regression_tests:
            lines.append(f"- {item.test_ref}")
        lines.append("")
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


def build_activation_foundation_regression_index(
    *,
    handoff_index: ActivationOperatorHandoffIndex | None = None,
    closure_bundle: ActivationFoundationClosureBundle | None = None,
) -> ActivationFoundationRegressionIndex:
    if handoff_index is not None and not isinstance(handoff_index, ActivationOperatorHandoffIndex):
        raise ActivationAuditError("handoff_index must be an ActivationOperatorHandoffIndex or None")
    if closure_bundle is not None and not isinstance(closure_bundle, ActivationFoundationClosureBundle):
        raise ActivationAuditError("closure_bundle must be an ActivationFoundationClosureBundle or None")

    runtime_disabled = _normalize_ref_list(_DEFAULT_RUNTIME_DISABLED)
    test_refs = tuple(RegressionTestReference.create(test_ref=value) for value in _DEFAULT_TEST_REFS)

    overall_status = (
        "incomplete"
        if handoff_index is None or closure_bundle is None
        else closure_bundle.status.value
    )

    handoff_sections = () if handoff_index is None else handoff_index.sections
    closure_sections = () if closure_bundle is None else closure_bundle.sections

    sections = (
        RegressionIndexSection.create(
            section_id="foundation_artifacts",
            summary=(
                "foundation closure and operator handoff artifacts are present"
                if handoff_index is not None and closure_bundle is not None
                else "foundation artifacts are incomplete"
            ),
            detail_count=len(handoff_sections) + len(closure_sections),
        ),
        RegressionIndexSection.create(
            section_id="required_regression_tests",
            summary="required regression tests are recorded as references only and are not executed by this model",
            detail_count=len(test_refs),
        ),
        RegressionIndexSection.create(
            section_id="operator_review_order",
            summary="operator should review closure bundle first, then handoff index, then regression references",
            detail_count=3,
        ),
        RegressionIndexSection.create(
            section_id="runtime_capabilities_still_disabled",
            summary="runtime capabilities remain disabled in the foundation phase",
            detail_count=len(runtime_disabled),
        ),
        RegressionIndexSection.create(
            section_id="evidence_boundaries",
            summary="this index only references evidence and tests; it does not execute or persist anything",
            detail_count=2,
        ),
        RegressionIndexSection.create(
            section_id="release_gate_summary",
            summary=(
                f"current foundation gate status is {overall_status}"
                if handoff_index is not None and closure_bundle is not None
                else "release gate summary is incomplete until closure bundle and handoff index are both present"
            ),
            detail_count=2 if handoff_index is not None and closure_bundle is not None else 0,
        ),
        RegressionIndexSection.create(
            section_id="next_post_foundation_slices",
            summary="post-foundation runtime slices remain deferred until operator review explicitly opens them",
            detail_count=len(runtime_disabled),
        ),
    )

    return ActivationFoundationRegressionIndex(
        overall_status=overall_status,
        required_regression_tests=test_refs,
        runtime_capabilities_still_disabled=runtime_disabled,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
