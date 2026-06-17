"""Audit index models for system health plugin foundation review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_dashboard_summary import DashboardSummary
from src.system_health_ops_readiness import OpsReadinessReport
from src.system_health_plugin_foundation_bundle import FoundationBundleReadiness


_SECTION_IDS = (
    "plugin_foundation_artifacts",
    "host_agent_boundaries",
    "no_go_runtime_actions",
    "required_review_tests",
    "operator_audit_checklist",
    "deployment_prerequisites",
    "followup_slices",
)

_DEFAULT_NO_GO_RUNTIME_ACTIONS = (
    "host_commands_from_core",
    "telegram_tokens",
    "webhook_activation",
    "podman_docker_socket_mount",
    "privileged_container_access",
    "direct_smart_access_from_container",
)

_DEFAULT_ARCHITECTURE_NOTES = (
    "podman_first",
    "docker_compatible",
    "host_agent_required",
    "odysseus_consumes_sanitized_snapshot",
)

_DEFAULT_REVIEW_TESTS = (
    "tests/test_system_health_agent_interface.py",
    "tests/test_system_health_basic_collectors.py",
    "tests/test_system_health_rule_engine.py",
    "tests/test_system_health_telegram_pull.py",
    "tests/test_system_health_container_runtime.py",
    "tests/test_system_health_advanced_collectors.py",
    "tests/test_system_health_dashboard_summary.py",
    "tests/test_system_health_ops_readiness.py",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise HealthAgentInterfaceError("unsupported system health plugin audit section_id")
    return text


def _normalize_ref_list(values: Iterable[Any]) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name="reference").replace("\\", "/") for value in values]
    return tuple(sorted(dict.fromkeys(normalized)))


@dataclass(frozen=True, slots=True)
class PluginAuditReference:
    reference_id: str

    @classmethod
    def create(cls, *, reference_id: Any) -> "PluginAuditReference":
        return cls(reference_id=_normalize_text(reference_id, field_name="reference_id").replace("\\", "/"))

    def to_dict(self) -> dict[str, Any]:
        return {"reference_id": self.reference_id}


@dataclass(frozen=True, slots=True)
class PluginAuditSection:
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
    ) -> "PluginAuditSection":
        if detail_count < 0:
            raise HealthAgentInterfaceError("detail_count must be non-negative")
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
class SystemHealthPluginAuditIndex:
    overall_status: str
    no_go_runtime_actions: tuple[str, ...]
    architecture_notes: tuple[str, ...]
    required_review_tests: tuple[PluginAuditReference, ...]
    sections: tuple[PluginAuditSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "no_go_runtime_actions": self.no_go_runtime_actions,
            "architecture_notes": self.architecture_notes,
            "required_review_tests": tuple(item.to_dict() for item in self.required_review_tests),
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# System Health Plugin Audit Index",
            "",
            f"- Overall status: `{self.overall_status}`",
            f"- Architecture notes: {', '.join(self.architecture_notes)}",
            f"- No-go runtime actions: {', '.join(self.no_go_runtime_actions)}",
            "",
            "## Required Review Tests",
        ]
        for item in self.required_review_tests:
            lines.append(f"- {item.reference_id}")
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


def build_system_health_plugin_audit_index(
    *,
    foundation_bundle: FoundationBundleReadiness | None = None,
    ops_readiness: OpsReadinessReport | None = None,
    dashboard_summary: DashboardSummary | None = None,
) -> SystemHealthPluginAuditIndex:
    if foundation_bundle is not None and not isinstance(foundation_bundle, FoundationBundleReadiness):
        raise HealthAgentInterfaceError("foundation_bundle must be a FoundationBundleReadiness or None")
    if ops_readiness is not None and not isinstance(ops_readiness, OpsReadinessReport):
        raise HealthAgentInterfaceError("ops_readiness must be an OpsReadinessReport or None")
    if dashboard_summary is not None and not isinstance(dashboard_summary, DashboardSummary):
        raise HealthAgentInterfaceError("dashboard_summary must be a DashboardSummary or None")

    no_go_actions = _normalize_ref_list(_DEFAULT_NO_GO_RUNTIME_ACTIONS)
    architecture_notes = _normalize_ref_list(_DEFAULT_ARCHITECTURE_NOTES)
    review_tests = tuple(PluginAuditReference.create(reference_id=value) for value in _DEFAULT_REVIEW_TESTS)

    overall_status = (
        "foundation_ready"
        if foundation_bundle is not None and foundation_bundle.foundation_ready
        else "review_required"
        if foundation_bundle is not None or ops_readiness is not None or dashboard_summary is not None
        else "foundation_only"
    )

    foundation_count = 0 if foundation_bundle is None else len(foundation_bundle.components)
    ops_count = 0 if ops_readiness is None else len(ops_readiness.items)
    dashboard_count = 0 if dashboard_summary is None else len(dashboard_summary.sections)

    sections = (
        PluginAuditSection.create(
            section_id="plugin_foundation_artifacts",
            summary=(
                "foundation artifacts are present for plugin audit review"
                if foundation_bundle is not None
                else "foundation artifact bundle is not attached"
            ),
            detail_count=foundation_count,
        ),
        PluginAuditSection.create(
            section_id="host_agent_boundaries",
            summary="host-agent boundaries stay outside core execution and only sanitized snapshots enter Odysseus",
            detail_count=len(architecture_notes),
        ),
        PluginAuditSection.create(
            section_id="no_go_runtime_actions",
            summary="runtime no-go actions remain disabled during foundation audit review",
            detail_count=len(no_go_actions),
        ),
        PluginAuditSection.create(
            section_id="required_review_tests",
            summary="review tests are recorded as references only and are not executed by this model",
            detail_count=len(review_tests),
        ),
        PluginAuditSection.create(
            section_id="operator_audit_checklist",
            summary=(
                "operator audit checklist can reference foundation, ops, and dashboard summaries"
                if any(item is not None for item in (foundation_bundle, ops_readiness, dashboard_summary))
                else "operator audit checklist remains foundation-only until supporting summaries are attached"
            ),
            detail_count=foundation_count + ops_count + dashboard_count,
        ),
        PluginAuditSection.create(
            section_id="deployment_prerequisites",
            summary="deployment prerequisites require a host agent and sanitized snapshot handoff before any runtime phase",
            detail_count=2 if foundation_bundle is None else 3,
        ),
        PluginAuditSection.create(
            section_id="followup_slices",
            summary="follow-up runtime and deployment slices remain deferred beyond the foundation audit index",
            detail_count=len(no_go_actions),
        ),
    )

    return SystemHealthPluginAuditIndex(
        overall_status=overall_status,
        no_go_runtime_actions=no_go_actions,
        architecture_notes=architecture_notes,
        required_review_tests=review_tests,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
