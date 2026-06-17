"""Foundation closure bundle models for orchestration activation review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import ActivationAuditError
from src.orchestration_activation_readiness_index import (
    ActivationReadinessIndex,
    ActivationReadinessIndexStatus,
)
from src.orchestration_operator_activation_packet import (
    OperatorActivationPacket,
    OperatorActivationPacketState,
)


class ClosureBundleStatus(StrEnum):
    FOUNDATION_READY = "foundation_ready"
    RUNTIME_BLOCKED = "runtime_blocked"
    REVIEW_REQUIRED = "review_required"
    INCOMPLETE = "incomplete"


_SECTION_IDS = (
    "foundation_components",
    "artifact_inventory",
    "readiness_index_summary",
    "runtime_gates_closed",
    "operator_release_note",
    "followup_slices",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: ClosureBundleStatus | str) -> ClosureBundleStatus:
    if isinstance(value, ClosureBundleStatus):
        return value
    text = _normalize_text(value, field_name="status").strip().lower()
    try:
        return ClosureBundleStatus(text)
    except ValueError as exc:
        raise ActivationAuditError("unsupported closure bundle status") from exc


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise ActivationAuditError("unsupported closure bundle section_id")
    return text


@dataclass(frozen=True, slots=True)
class ClosureBundleSection:
    section_id: str
    status: ClosureBundleStatus
    summary: str
    detail_count: int

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        status: ClosureBundleStatus | str,
        summary: Any,
        detail_count: int = 0,
    ) -> "ClosureBundleSection":
        if detail_count < 0:
            raise ActivationAuditError("detail_count must be non-negative")
        return cls(
            section_id=_normalize_section_id(section_id),
            status=_normalize_status(status),
            summary=_normalize_text(summary, field_name="summary"),
            detail_count=detail_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "status": self.status.value,
            "summary": self.summary,
            "detail_count": self.detail_count,
        }


@dataclass(frozen=True, slots=True)
class ActivationFoundationClosureBundle:
    status: ClosureBundleStatus
    sections: tuple[ClosureBundleSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Activation Foundation Closure Bundle",
            "",
            f"- Status: `{self.status.value}`",
            "",
        ]
        for section in self.sections:
            lines.extend(
                [
                    f"## {section.section_id}",
                    f"- Status: `{section.status.value}`",
                    f"- Summary: {section.summary}",
                    f"- Count: {section.detail_count}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


def _derive_bundle_status(
    readiness_index: ActivationReadinessIndex | None,
    packet: OperatorActivationPacket | None,
) -> ClosureBundleStatus:
    if readiness_index is None and packet is None:
        return ClosureBundleStatus.INCOMPLETE
    if readiness_index is None:
        return ClosureBundleStatus.INCOMPLETE
    if readiness_index.overall_status == ActivationReadinessIndexStatus.BLOCKED:
        return ClosureBundleStatus.REVIEW_REQUIRED
    if readiness_index.overall_status in {
        ActivationReadinessIndexStatus.REVIEW_REQUIRED,
        ActivationReadinessIndexStatus.DEFERRED,
        ActivationReadinessIndexStatus.NOT_STARTED,
    }:
        return ClosureBundleStatus.REVIEW_REQUIRED
    if packet is None:
        return ClosureBundleStatus.INCOMPLETE
    if packet.blocked_runtime_actions:
        if packet.state == OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE:
            return ClosureBundleStatus.RUNTIME_BLOCKED
        return ClosureBundleStatus.FOUNDATION_READY
    return ClosureBundleStatus.INCOMPLETE


def build_activation_foundation_closure_bundle(
    *,
    readiness_index: ActivationReadinessIndex | None = None,
    packet: OperatorActivationPacket | None = None,
) -> ActivationFoundationClosureBundle:
    if readiness_index is not None and not isinstance(readiness_index, ActivationReadinessIndex):
        raise ActivationAuditError("readiness_index must be an ActivationReadinessIndex or None")
    if packet is not None and not isinstance(packet, OperatorActivationPacket):
        raise ActivationAuditError("packet must be an OperatorActivationPacket or None")

    bundle_status = _derive_bundle_status(readiness_index, packet)
    packet_sections = () if packet is None else packet.sections
    readiness_items = () if readiness_index is None else readiness_index.items

    sections = (
        ClosureBundleSection.create(
            section_id="foundation_components",
            status=(
                ClosureBundleStatus.INCOMPLETE
                if readiness_index is None
                else ClosureBundleStatus.FOUNDATION_READY
                if readiness_index.overall_status == ActivationReadinessIndexStatus.READY
                else ClosureBundleStatus.REVIEW_REQUIRED
            ),
            summary=(
                "foundation components are ready for operator-facing closure"
                if readiness_index is not None and readiness_index.overall_status == ActivationReadinessIndexStatus.READY
                else "foundation components are incomplete or still under review"
            ),
            detail_count=len(readiness_items),
        ),
        ClosureBundleSection.create(
            section_id="artifact_inventory",
            status=(
                ClosureBundleStatus.INCOMPLETE
                if packet is None
                else ClosureBundleStatus.FOUNDATION_READY
                if len(packet_sections) > 0
                else ClosureBundleStatus.REVIEW_REQUIRED
            ),
            summary="activation packet artifacts are present"
            if packet is not None
            else "activation packet artifacts are not yet assembled",
            detail_count=len(packet_sections),
        ),
        ClosureBundleSection.create(
            section_id="readiness_index_summary",
            status=(
                ClosureBundleStatus.INCOMPLETE
                if readiness_index is None
                else ClosureBundleStatus.FOUNDATION_READY
                if readiness_index.overall_status == ActivationReadinessIndexStatus.READY
                else ClosureBundleStatus.REVIEW_REQUIRED
            ),
            summary=(
                f"readiness index is {readiness_index.overall_status.value}"
                if readiness_index is not None
                else "readiness index is not available"
            ),
            detail_count=len(readiness_items),
        ),
        ClosureBundleSection.create(
            section_id="runtime_gates_closed",
            status=(
                ClosureBundleStatus.RUNTIME_BLOCKED
                if packet is not None and packet.blocked_runtime_actions
                else ClosureBundleStatus.INCOMPLETE
            ),
            summary=(
                "runtime gates remain intentionally closed for foundation mode"
                if packet is not None and packet.blocked_runtime_actions
                else "runtime gate closure has not been documented"
            ),
            detail_count=0 if packet is None else len(packet.blocked_runtime_actions),
        ),
        ClosureBundleSection.create(
            section_id="operator_release_note",
            status=(
                ClosureBundleStatus.REVIEW_REQUIRED
                if bundle_status == ClosureBundleStatus.REVIEW_REQUIRED
                else ClosureBundleStatus.FOUNDATION_READY
                if bundle_status == ClosureBundleStatus.FOUNDATION_READY
                else bundle_status
            ),
            summary=(
                "foundation is ready for operator review while runtime actions stay closed"
                if bundle_status == ClosureBundleStatus.FOUNDATION_READY
                else "operator review must confirm remaining readiness gaps"
                if bundle_status == ClosureBundleStatus.REVIEW_REQUIRED
                else "foundation closure is incomplete without both readiness index and packet"
                if bundle_status == ClosureBundleStatus.INCOMPLETE
                else "operator approval exists, but runtime gates stay blocked by design"
            ),
            detail_count=1,
        ),
        ClosureBundleSection.create(
            section_id="followup_slices",
            status=(
                ClosureBundleStatus.RUNTIME_BLOCKED
                if packet is not None and packet.blocked_runtime_actions
                else ClosureBundleStatus.REVIEW_REQUIRED
                if bundle_status == ClosureBundleStatus.REVIEW_REQUIRED
                else ClosureBundleStatus.INCOMPLETE
            ),
            summary=(
                "follow-up runtime slices remain outside this foundation closure bundle"
                if packet is not None and packet.blocked_runtime_actions
                else "follow-up slices require operator review planning"
                if bundle_status == ClosureBundleStatus.REVIEW_REQUIRED
                else "follow-up slices cannot be summarized yet"
            ),
            detail_count=0 if packet is None else len(packet.blocked_runtime_actions),
        ),
    )

    return ActivationFoundationClosureBundle(
        status=bundle_status,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
