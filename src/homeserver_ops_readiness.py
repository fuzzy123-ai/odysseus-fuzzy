"""Small backend contract for homeserver operations readiness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_ACCELERATOR_TERMS = ("qdrant", "kuzu", "umap", "gmm", "accelerator")


class HomeserverOpsReadinessError(ValueError):
    """Raised when a homeserver ops readiness payload is invalid or unsafe."""


class OpsReadinessStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    GO = "go"
    NO_GO = "no_go"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise HomeserverOpsReadinessError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise HomeserverOpsReadinessError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise HomeserverOpsReadinessError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HomeserverOpsReadinessError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise HomeserverOpsReadinessError(f"{field_name} must be a bool")


def _normalize_positive_number(value: Any, *, field_name: str, float_ok: bool = False) -> int | float:
    try:
        normalized = float(value) if float_ok else int(value)
    except (TypeError, ValueError):
        kind = "number" if float_ok else "int"
        raise HomeserverOpsReadinessError(f"{field_name} must be a {kind}") from None
    if normalized <= 0:
        raise HomeserverOpsReadinessError(f"{field_name} must be > 0")
    return normalized


def _normalize_percent(value: Any, *, field_name: str) -> int:
    normalized = int(_normalize_positive_number(value, field_name=field_name))
    if normalized > 100:
        raise HomeserverOpsReadinessError(f"{field_name} must be <= 100")
    return normalized


def _normalize_status(value: Any) -> OpsReadinessStatus:
    if isinstance(value, OpsReadinessStatus):
        return value
    normalized = _normalize_slug(value, field_name="go_no_go_status").replace("-", "_")
    try:
        return OpsReadinessStatus(normalized)
    except ValueError as exc:
        raise HomeserverOpsReadinessError("go_no_go_status is not supported") from exc


def _reject_accelerator_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_ACCELERATOR_TERMS):
        raise HomeserverOpsReadinessError("accelerator activation is out of scope for homeserver ops readiness")


@dataclass(frozen=True, slots=True)
class HomeserverProfile:
    homeserver_profile: str
    service_ref: str
    postgres_ref: str
    data_volume_ref: str
    backup_volume_ref: str
    cpu_cores: int
    ram_gb: int
    storage_gb: int

    @classmethod
    def create(
        cls,
        *,
        homeserver_profile: Any,
        service_ref: Any,
        postgres_ref: Any,
        data_volume_ref: Any,
        backup_volume_ref: Any,
        cpu_cores: Any,
        ram_gb: Any,
        storage_gb: Any,
    ) -> "HomeserverProfile":
        return cls(
            homeserver_profile=_normalize_slug(homeserver_profile, field_name="homeserver_profile"),
            service_ref=_normalize_slug(service_ref, field_name="service_ref"),
            postgres_ref=_normalize_slug(postgres_ref, field_name="postgres_ref"),
            data_volume_ref=_normalize_slug(data_volume_ref, field_name="data_volume_ref"),
            backup_volume_ref=_normalize_slug(backup_volume_ref, field_name="backup_volume_ref"),
            cpu_cores=int(_normalize_positive_number(cpu_cores, field_name="cpu_cores")),
            ram_gb=int(_normalize_positive_number(ram_gb, field_name="ram_gb")),
            storage_gb=int(_normalize_positive_number(storage_gb, field_name="storage_gb")),
        )


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_memory_job_concurrency: int
    max_index_job_concurrency: int
    max_cpu_percent: int
    max_ram_percent: int
    storage_warning_percent: int
    storage_block_percent: int
    current_storage_percent: int

    @classmethod
    def create(
        cls,
        *,
        max_memory_job_concurrency: Any,
        max_index_job_concurrency: Any,
        max_cpu_percent: Any,
        max_ram_percent: Any,
        storage_warning_percent: Any,
        storage_block_percent: Any,
        current_storage_percent: Any = 0,
    ) -> "ResourceBudget":
        memory_concurrency = int(_normalize_positive_number(max_memory_job_concurrency, field_name="max_memory_job_concurrency"))
        index_concurrency = int(_normalize_positive_number(max_index_job_concurrency, field_name="max_index_job_concurrency"))
        cpu_percent = _normalize_percent(max_cpu_percent, field_name="max_cpu_percent")
        ram_percent = _normalize_percent(max_ram_percent, field_name="max_ram_percent")
        warning_percent = _normalize_percent(storage_warning_percent, field_name="storage_warning_percent")
        block_percent = _normalize_percent(storage_block_percent, field_name="storage_block_percent")
        current_percent = _normalize_percent(current_storage_percent or 1, field_name="current_storage_percent")
        if warning_percent >= block_percent:
            raise HomeserverOpsReadinessError("storage_warning_percent must be below storage_block_percent")
        return cls(
            max_memory_job_concurrency=memory_concurrency,
            max_index_job_concurrency=index_concurrency,
            max_cpu_percent=cpu_percent,
            max_ram_percent=ram_percent,
            storage_warning_percent=warning_percent,
            storage_block_percent=block_percent,
            current_storage_percent=current_percent,
        )

    def storage_blocked(self) -> bool:
        return self.current_storage_percent >= self.storage_block_percent


@dataclass(frozen=True, slots=True)
class BackupRestorePlan:
    backup_ref: str
    restore_ref: str
    restore_drill_status: str
    last_restore_drill_ref: str

    @classmethod
    def create(
        cls,
        *,
        backup_ref: Any,
        restore_ref: Any,
        restore_drill_status: Any,
        last_restore_drill_ref: Any,
    ) -> "BackupRestorePlan":
        status = _normalize_slug(restore_drill_status, field_name="restore_drill_status")
        return cls(
            backup_ref=_normalize_text(backup_ref, field_name="backup_ref", allow_empty=True, limit=_MAX_LONG_TEXT),
            restore_ref=_normalize_text(restore_ref, field_name="restore_ref", allow_empty=True, limit=_MAX_LONG_TEXT),
            restore_drill_status=status,
            last_restore_drill_ref=_normalize_text(
                last_restore_drill_ref,
                field_name="last_restore_drill_ref",
                allow_empty=True,
                limit=_MAX_LONG_TEXT,
            ),
        )

    def drill_ok(self) -> bool:
        return self.restore_drill_status in {"ok", "passed", "success"}


@dataclass(frozen=True, slots=True)
class MaintenancePolicy:
    maintenance_window: str
    vacuum_policy: str
    index_maintenance_policy: str
    retention_policy: str

    @classmethod
    def create(
        cls,
        *,
        maintenance_window: Any,
        vacuum_policy: Any,
        index_maintenance_policy: Any,
        retention_policy: Any,
    ) -> "MaintenancePolicy":
        return cls(
            maintenance_window=_normalize_text(maintenance_window, field_name="maintenance_window", allow_empty=False),
            vacuum_policy=_normalize_text(vacuum_policy, field_name="vacuum_policy", allow_empty=False, limit=_MAX_LONG_TEXT),
            index_maintenance_policy=_normalize_text(
                index_maintenance_policy,
                field_name="index_maintenance_policy",
                allow_empty=False,
                limit=_MAX_LONG_TEXT,
            ),
            retention_policy=_normalize_text(retention_policy, field_name="retention_policy", allow_empty=False, limit=_MAX_LONG_TEXT),
        )


@dataclass(frozen=True, slots=True)
class OpsReadinessReport:
    profile: HomeserverProfile
    resource_budget: ResourceBudget
    backup_restore_plan: BackupRestorePlan
    maintenance_policy: MaintenancePolicy
    go_no_go_status: OpsReadinessStatus
    risk_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        profile: HomeserverProfile,
        resource_budget: ResourceBudget,
        backup_restore_plan: BackupRestorePlan,
        maintenance_policy: MaintenancePolicy,
        go_no_go_status: OpsReadinessStatus | str,
        risk_evidence_ref: Any,
        reason: Any = "",
        next_action: Any = "",
    ) -> "OpsReadinessReport":
        if not isinstance(profile, HomeserverProfile):
            raise HomeserverOpsReadinessError("profile must be a HomeserverProfile")
        if not isinstance(resource_budget, ResourceBudget):
            raise HomeserverOpsReadinessError("resource_budget must be a ResourceBudget")
        if not isinstance(backup_restore_plan, BackupRestorePlan):
            raise HomeserverOpsReadinessError("backup_restore_plan must be a BackupRestorePlan")
        if not isinstance(maintenance_policy, MaintenancePolicy):
            raise HomeserverOpsReadinessError("maintenance_policy must be a MaintenancePolicy")
        normalized_status = _normalize_status(go_no_go_status)
        normalized_risk = _normalize_text(
            risk_evidence_ref,
            field_name="risk_evidence_ref",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_accelerator_terms(
            profile.service_ref,
            profile.postgres_ref,
            normalized_risk,
            normalized_reason,
            normalized_next_action,
            maintenance_policy.index_maintenance_policy,
            maintenance_policy.retention_policy,
        )
        if normalized_status == OpsReadinessStatus.GO:
            if not (
                backup_restore_plan.backup_ref
                and backup_restore_plan.restore_ref
                and backup_restore_plan.drill_ok()
                and backup_restore_plan.last_restore_drill_ref
                and maintenance_policy.maintenance_window
                and maintenance_policy.vacuum_policy
                and maintenance_policy.index_maintenance_policy
                and maintenance_policy.retention_policy
                and normalized_risk
            ):
                raise HomeserverOpsReadinessError(
                    "go requires backup_ref, restore_ref, successful restore drill, maintenance policies, and risk_evidence_ref"
                )
            if resource_budget.storage_blocked():
                raise HomeserverOpsReadinessError("storage block pressure must not allow go")
        if normalized_status in {OpsReadinessStatus.BLOCKED, OpsReadinessStatus.FAILED, OpsReadinessStatus.NO_GO} and not (
            normalized_reason or normalized_next_action
        ):
            raise HomeserverOpsReadinessError("blocked, failed, and no_go reports require reason or next_action")
        return cls(
            profile=profile,
            resource_budget=resource_budget,
            backup_restore_plan=backup_restore_plan,
            maintenance_policy=maintenance_policy,
            go_no_go_status=normalized_status,
            risk_evidence_ref=normalized_risk,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "homeserver_profile": self.profile.homeserver_profile,
            "service_ref": self.profile.service_ref,
            "postgres_ref": self.profile.postgres_ref,
            "go_no_go_status": self.go_no_go_status.value,
            "cpu_cores": self.profile.cpu_cores,
            "ram_gb": self.profile.ram_gb,
            "storage_gb": self.profile.storage_gb,
            "max_memory_job_concurrency": self.resource_budget.max_memory_job_concurrency,
            "max_index_job_concurrency": self.resource_budget.max_index_job_concurrency,
            "storage_warning_percent": self.resource_budget.storage_warning_percent,
            "storage_block_percent": self.resource_budget.storage_block_percent,
            "current_storage_percent": self.resource_budget.current_storage_percent,
            "restore_drill_status": self.backup_restore_plan.restore_drill_status,
            "has_backup_ref": bool(self.backup_restore_plan.backup_ref),
            "has_restore_ref": bool(self.backup_restore_plan.restore_ref),
            "has_evidence_ref": bool(self.risk_evidence_ref),
        }
