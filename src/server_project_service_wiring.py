"""Systemd/Podman service wiring plans for universal server projects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.server_project_deploy_handoff import ProjectDeployHandoff
from src.server_project_registry import ServerProjectRecord


_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("plan_ready", "hold", "blocked")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


class ServerProjectServiceWiringError(ValueError):
    """Raised when project service wiring cannot be safely planned."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectServiceWiringError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectServiceWiringError(f"{field_name} exceeds max length {max_len}")
    lowered = text.lower()
    if any(token in lowered for token in ("token=", "secret=", "password=", "api_key=", "bearer ")):
        raise ServerProjectServiceWiringError(f"{field_name} appears to contain secret material")
    if re.search(r"[A-Za-z]:\\", text) or text.startswith("/home/") or text.startswith("/root/"):
        raise ServerProjectServiceWiringError(f"{field_name} must not contain private host-local paths")
    return text


def _normalize_operator(value: Any) -> str:
    text = _normalize_text(value, field_name="operator_decision").lower().replace("-", "_")
    if text not in _OPERATOR_DECISIONS:
        raise ServerProjectServiceWiringError(f"unsupported operator_decision: {value!r}")
    return text


def _unit_slug(project_slug: str) -> str:
    if not _SLUG_RE.fullmatch(project_slug):
        raise ServerProjectServiceWiringError("project_slug must be a slug")
    return project_slug.replace("_", "-").replace(".", "-")


@dataclass(frozen=True, slots=True)
class ProjectServiceWiringPlan:
    project_slug: str
    service_unit: str
    health_unit: str
    log_unit: str
    project_root_placeholder: str
    wrapper_path_placeholder: str
    healthcheck_url: str
    cloudflare_tunnel_requested: bool
    cloudflare_gate: str
    operator_decision: str
    decision: str
    blockers: tuple[str, ...]
    unit_templates: tuple[Mapping[str, str], ...]
    planned_steps: tuple[Mapping[str, Any], ...]
    next_human_decision: str

    @property
    def install_allowed(self) -> bool:
        return self.decision == "plan_ready" and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "service_unit": self.service_unit,
            "health_unit": self.health_unit,
            "log_unit": self.log_unit,
            "project_root_placeholder": self.project_root_placeholder,
            "wrapper_path_placeholder": self.wrapper_path_placeholder,
            "healthcheck_url": self.healthcheck_url,
            "cloudflare_tunnel_requested": self.cloudflare_tunnel_requested,
            "cloudflare_gate": self.cloudflare_gate,
            "operator_decision": self.operator_decision,
            "decision": self.decision,
            "install_allowed": self.install_allowed,
            "blockers": list(self.blockers),
            "unit_templates": [dict(item) for item in self.unit_templates],
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


def build_project_service_wiring_plan(
    *,
    record: ServerProjectRecord,
    deploy_handoff: ProjectDeployHandoff,
    operator_decision: Any = "missing",
    healthcheck_port: int = 7000,
    healthcheck_path: Any = "/health",
) -> ProjectServiceWiringPlan:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectServiceWiringError("record must be a ServerProjectRecord")
    if not isinstance(deploy_handoff, ProjectDeployHandoff):
        raise ServerProjectServiceWiringError("deploy_handoff must be a ProjectDeployHandoff")
    normalized_operator = _normalize_operator(operator_decision)
    slug = _unit_slug(record.project_slug)
    checked_path = _normalize_health_path(healthcheck_path)
    checked_port = _normalize_port(healthcheck_port)
    service_unit = f"odysseus-project-{slug}.service"
    health_unit = f"odysseus-project-{slug}-health.service"
    log_unit = f"odysseus-project-{slug}-logs.service"
    project_root = f"$ODYSSEUS_PROJECTS_ROOT/{record.project_slug}"
    wrapper_path = f"$ODYSSEUS_USER_BIN_DIR/odysseus-project-{slug}.sh"
    healthcheck_url = f"http://127.0.0.1:{checked_port}{checked_path}"

    blockers: list[str] = []
    if deploy_handoff.decision != "ready_for_operator_go":
        blockers.append(f"deploy handoff decision is {deploy_handoff.decision}")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if record.project_spec.cloudflare_tunnel_requested:
        blockers.append("Cloudflare Tunnel exposure requires a separate route and token operator gate")

    if normalized_operator == "no_go":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return ProjectServiceWiringPlan(
        project_slug=record.project_slug,
        service_unit=service_unit,
        health_unit=health_unit,
        log_unit=log_unit,
        project_root_placeholder=project_root,
        wrapper_path_placeholder=wrapper_path,
        healthcheck_url=healthcheck_url,
        cloudflare_tunnel_requested=record.project_spec.cloudflare_tunnel_requested,
        cloudflare_gate=_cloudflare_gate(record),
        operator_decision=normalized_operator,
        decision=decision,
        blockers=tuple(blockers),
        unit_templates=_unit_templates(
            record=record,
            service_unit=service_unit,
            health_unit=health_unit,
            log_unit=log_unit,
            project_root=project_root,
            wrapper_path=wrapper_path,
            healthcheck_url=healthcheck_url,
        ),
        planned_steps=_planned_steps(record),
        next_human_decision=_next_human_decision(decision),
    )


def _normalize_health_path(value: Any) -> str:
    path = _normalize_text(value, field_name="healthcheck_path", max_len=80)
    if not path.startswith("/") or ".." in path or "\\" in path:
        raise ServerProjectServiceWiringError("healthcheck_path must be an absolute URL path without traversal")
    return path


def _normalize_port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerProjectServiceWiringError("healthcheck_port must be an integer")
    if value < 1 or value > 65535:
        raise ServerProjectServiceWiringError("healthcheck_port must be between 1 and 65535")
    return value


def _cloudflare_gate(record: ServerProjectRecord) -> str:
    if record.project_spec.cloudflare_tunnel_requested:
        return "Cloudflare route, service token, DNS name and healthcheck must be approved before public exposure."
    return "Cloudflare Tunnel not requested; keep project internal until exposure gate is opened."


def _unit_templates(
    *,
    record: ServerProjectRecord,
    service_unit: str,
    health_unit: str,
    log_unit: str,
    project_root: str,
    wrapper_path: str,
    healthcheck_url: str,
) -> tuple[Mapping[str, str], ...]:
    return (
        {
            "unit": service_unit,
            "kind": "service",
            "content": "\n".join(
                (
                    "[Unit]",
                    f"Description=Odysseus project runner for {record.project_slug}",
                    "After=network-online.target",
                    "Wants=network-online.target",
                    "",
                    "[Service]",
                    "Type=oneshot",
                    f"WorkingDirectory={project_root}",
                    f"ExecStart={wrapper_path}",
                    "TimeoutStartSec=7200",
                )
            ),
        },
        {
            "unit": health_unit,
            "kind": "healthcheck",
            "content": "\n".join(
                (
                    "[Unit]",
                    f"Description=Odysseus project healthcheck for {record.project_slug}",
                    "",
                    "[Service]",
                    "Type=oneshot",
                    f"ExecStart=python -m pytest tests/project_smoke.py -q --health-url {healthcheck_url}",
                    "TimeoutStartSec=900",
                )
            ),
        },
        {
            "unit": log_unit,
            "kind": "logs",
            "content": "\n".join(
                (
                    "[Unit]",
                    f"Description=Odysseus project log review for {record.project_slug}",
                    "",
                    "[Service]",
                    "Type=oneshot",
                    "ExecStart=journalctl --user --no-pager --since -1h --unit %i",
                    "TimeoutStartSec=300",
                )
            ),
        },
    )


def _planned_steps(record: ServerProjectRecord) -> tuple[Mapping[str, Any], ...]:
    return (
        {"step_id": "write_wrapper", "summary": f"write reviewed project wrapper for {record.project_slug}", "executes": False},
        {"step_id": "write_units", "summary": "write reviewed systemd user unit templates", "executes": False},
        {"step_id": "daemon_reload", "summary": "operator reloads user service manager after reviewing units", "executes": False},
        {"step_id": "enable_service", "summary": "operator enables project service after backup and deploy gates", "executes": False},
        {"step_id": "healthcheck", "summary": "operator runs healthcheck unit and records redacted result", "executes": False},
        {"step_id": "cloudflare_gate", "summary": _cloudflare_gate(record), "executes": False},
    )


def _next_human_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Operator may review generated unit templates and decide on a separate server install Go/No-Go."
    if decision == "blocked":
        return "Do not install project service wiring until blocked operator decision is cleared."
    return "Clear deploy handoff, operator Go and Cloudflare exposure gates before service install review."
