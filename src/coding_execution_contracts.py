"""Pure, content-free contracts for scoped sandbox job planning.

These descriptors deliberately describe a job without providing a way to run
one.  The execution plane is allowed to render deterministic plans only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.coding_loop_contracts import CodingLoopContractError, strict_id, validate_budget
from src.runtime_event_envelope import stable_payload_hash


MAX_ARGV_ITEMS = 16
MAX_ARGV_ITEM_LENGTH = 180
MAX_CPU_MILLIS = 2_000
MAX_MEMORY_MB = 1_024
MAX_WALL_TIME_SECONDS = 900

_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9_.@+\-=/]{1,240}$")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)


class CodingExecutionContractError(CodingLoopContractError):
    """Raised when a sandbox-plan descriptor is unsafe or inconsistent."""


class SandboxRuntimeProfile(StrEnum):
    PYTHON_PYTEST_311 = "python-pytest-311"


class SandboxNetworkMode(StrEnum):
    NONE = "none"


class SandboxJobStatusKind(StrEnum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class BoundedCheckCommand(StrEnum):
    PYTEST = "pytest"


_COMMAND_PREFIXES = {
    BoundedCheckCommand.PYTEST: ("python", "-m", "pytest", "-q"),
}
_RUNTIME_PROFILES = {
    BoundedCheckCommand.PYTEST: SandboxRuntimeProfile.PYTHON_PYTEST_311,
}
_TRUSTED_CAPABILITIES = {
    BoundedCheckCommand.PYTEST: ("runner.pytest", "sandbox.network_none", "sandbox.read_only"),
}


@dataclass(frozen=True, slots=True)
class SandboxMount:
    repo_path: str
    read_only: bool = True

    def __post_init__(self) -> None:
        _repo_path(self.repo_path, "repo_path")
        if self.read_only is not True:
            raise CodingExecutionContractError("sandbox mounts must be read-only")

    def semantic_dict(self) -> dict[str, Any]:
        return {"repo_path": self.repo_path, "read_only": True}


@dataclass(frozen=True, slots=True)
class SandboxResourceLimits:
    cpu_millis: int
    memory_mb: int
    wall_time_seconds: int

    def __post_init__(self) -> None:
        validate_budget(self.cpu_millis, "cpu_millis", MAX_CPU_MILLIS)
        validate_budget(self.memory_mb, "memory_mb", MAX_MEMORY_MB)
        validate_budget(self.wall_time_seconds, "wall_time_seconds", MAX_WALL_TIME_SECONDS)

    def semantic_dict(self) -> dict[str, int]:
        return {
            "cpu_millis": self.cpu_millis,
            "memory_mb": self.memory_mb,
            "wall_time_seconds": self.wall_time_seconds,
        }


@dataclass(frozen=True, slots=True)
class BoundedCheckRequest:
    request_id: str
    controller_state_id: str
    intent_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    parent_envelope_id: str
    capsule_id: str
    check_ref: str
    capability_ref: str
    command: BoundedCheckCommand
    argv: tuple[str, ...]
    resources: SandboxResourceLimits
    execution_allowed: bool = False
    dispatch_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _enum(self.command, BoundedCheckCommand, "command"))
        _sha256(self.request_id, "request_id")
        _sha256(self.controller_state_id, "controller_state_id")
        _sha256(self.intent_id, "intent_id")
        _sha256(self.scope_digest, "scope_digest")
        for field in (
            "planning_item_id", "planning_revision", "claim_id", "claim_owner",
            "input_revision", "parent_envelope_id", "capsule_id", "check_ref", "capability_ref",
        ):
            _safe_id(getattr(self, field), field)
        _validate_argv(self.command, self.argv)
        if not isinstance(self.resources, SandboxResourceLimits):
            raise CodingExecutionContractError("resources must be typed")
        _zero_authority(self)
        if self.request_id != stable_payload_hash(self.semantic_dict()):
            raise CodingExecutionContractError("request_id does not match canonical request facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "controller_state_id": self.controller_state_id,
            "intent_id": self.intent_id,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "parent_envelope_id": self.parent_envelope_id,
            "capsule_id": self.capsule_id,
            "check_ref": self.check_ref,
            "capability_ref": self.capability_ref,
            "command": self.command.value,
            "argv": self.argv,
            "resources": self.resources.semantic_dict(),
            "execution_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class SandboxJobRequest:
    job_id: str
    request_id: str
    controller_state_id: str
    intent_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    capsule_id: str
    check_ref: str
    capability_ref: str
    runtime_profile: SandboxRuntimeProfile
    argv: tuple[str, ...]
    mounts: tuple[SandboxMount, ...]
    network: SandboxNetworkMode
    resources: SandboxResourceLimits
    trusted_capabilities: tuple[str, ...]
    network_allowlist: tuple[str, ...] = ()
    dispatch_allowed: bool = False
    execution_performed: bool = False
    live_effect_allowed: bool = False
    secrets_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_profile", _enum(self.runtime_profile, SandboxRuntimeProfile, "runtime_profile"))
        object.__setattr__(self, "network", _enum(self.network, SandboxNetworkMode, "network"))
        for field in ("job_id", "request_id", "controller_state_id", "intent_id", "scope_digest"):
            _sha256(getattr(self, field), field)
        for field in (
            "planning_item_id", "planning_revision", "claim_id", "claim_owner",
            "input_revision", "capsule_id", "check_ref", "capability_ref",
        ):
            _safe_id(getattr(self, field), field)
        if not isinstance(self.mounts, tuple) or not self.mounts or not all(
            isinstance(item, SandboxMount) for item in self.mounts
        ):
            raise CodingExecutionContractError("mounts must be a non-empty typed tuple")
        if tuple(item.repo_path for item in self.mounts) != tuple(sorted(item.repo_path for item in self.mounts)):
            raise CodingExecutionContractError("mounts must be canonical")
        if len({item.repo_path for item in self.mounts}) != len(self.mounts):
            raise CodingExecutionContractError("mounts must be unique")
        if self.network is not SandboxNetworkMode.NONE:
            raise CodingExecutionContractError("network must remain none")
        if self.network_allowlist != ():
            raise CodingExecutionContractError("network allowlist must remain empty")
        if not isinstance(self.resources, SandboxResourceLimits):
            raise CodingExecutionContractError("resources must be typed")
        if not isinstance(self.trusted_capabilities, tuple) or self.trusted_capabilities != tuple(sorted(self.trusted_capabilities)):
            raise CodingExecutionContractError("trusted_capabilities must be canonical")
        for capability in self.trusted_capabilities:
            _safe_id(capability, "trusted_capability")
        if not self.trusted_capabilities:
            raise CodingExecutionContractError("trusted_capabilities must be minimal but non-empty")
        _zero_authority(self)
        if self.job_id != stable_payload_hash(self.semantic_dict()):
            raise CodingExecutionContractError("job_id does not match canonical job facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "controller_state_id": self.controller_state_id,
            "intent_id": self.intent_id,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "capsule_id": self.capsule_id,
            "check_ref": self.check_ref,
            "capability_ref": self.capability_ref,
            "runtime_profile": self.runtime_profile.value,
            "argv": self.argv,
            "mounts": tuple(item.semantic_dict() for item in self.mounts),
            "network": SandboxNetworkMode.NONE.value,
            "network_allowlist": (),
            "resources": self.resources.semantic_dict(),
            "trusted_capabilities": self.trusted_capabilities,
            "dispatch_allowed": False,
            "execution_performed": False,
            "live_effect_allowed": False,
            "secrets_allowed": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, **self.semantic_dict()}


@dataclass(frozen=True, slots=True)
class SandboxJobStatus:
    status_id: str
    job_id: str
    status: SandboxJobStatusKind
    execution_performed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, SandboxJobStatusKind, "status"))
        _sha256(self.status_id, "status_id")
        _sha256(self.job_id, "job_id")
        if self.execution_performed is not False or self.raw_content_visible is not False:
            raise CodingExecutionContractError("fake status must remain content-free and non-executing")
        if self.status_id != stable_payload_hash(self.semantic_dict()):
            raise CodingExecutionContractError("status_id does not match canonical status facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "execution_performed": False,
            "raw_content_visible": False,
        }


def create_bounded_check_request(**facts: Any) -> BoundedCheckRequest:
    core = dict(facts)
    supplied_request_id = core.pop("request_id", None)
    core.pop("request_id", None)
    core.setdefault("execution_allowed", False)
    core.setdefault("dispatch_allowed", False)
    core.setdefault("live_effect_allowed", False)
    core.setdefault("raw_content_visible", False)
    command = _enum(core.get("command"), BoundedCheckCommand, "command")
    core["command"] = command.value
    resources = core.get("resources")
    if isinstance(resources, SandboxResourceLimits):
        core["resources"] = resources.semantic_dict()
    request_id = stable_payload_hash(core)
    if supplied_request_id is not None and supplied_request_id != request_id:
        raise CodingExecutionContractError("request_id does not match canonical request facts")
    return BoundedCheckRequest(request_id=request_id, **{
        key: value for key, value in facts.items() if key != "request_id"
    })


def create_sandbox_job_request(
    request: BoundedCheckRequest, *, mounts: tuple[SandboxMount, ...]
) -> SandboxJobRequest:
    if not isinstance(request, BoundedCheckRequest):
        raise CodingExecutionContractError("request must be typed")
    profile = _RUNTIME_PROFILES[request.command]
    core = {
        "request_id": request.request_id,
        "controller_state_id": request.controller_state_id,
        "intent_id": request.intent_id,
        "planning_item_id": request.planning_item_id,
        "planning_revision": request.planning_revision,
        "claim_id": request.claim_id,
        "claim_owner": request.claim_owner,
        "scope_digest": request.scope_digest,
        "input_revision": request.input_revision,
        "capsule_id": request.capsule_id,
        "check_ref": request.check_ref,
        "capability_ref": request.capability_ref,
        "runtime_profile": profile.value,
        "argv": request.argv,
        "mounts": tuple(item.semantic_dict() for item in mounts),
        "network": SandboxNetworkMode.NONE.value,
        "network_allowlist": (),
        "resources": request.resources.semantic_dict(),
        "trusted_capabilities": _TRUSTED_CAPABILITIES[request.command],
        "dispatch_allowed": False,
        "execution_performed": False,
        "live_effect_allowed": False,
        "secrets_allowed": False,
        "raw_content_visible": False,
    }
    return SandboxJobRequest(
        job_id=stable_payload_hash(core),
        mounts=mounts,
        resources=request.resources,
        **{key: value for key, value in core.items() if key not in {"mounts", "resources"}},
    )


def reduce_fake_sandbox_status(
    job: SandboxJobRequest, status: SandboxJobStatusKind | str
) -> SandboxJobStatus:
    if not isinstance(job, SandboxJobRequest):
        raise CodingExecutionContractError("job must be typed")
    status_kind = _enum(status, SandboxJobStatusKind, "status")
    core = {
        "job_id": job.job_id,
        "status": status_kind.value,
        "execution_performed": False,
        "raw_content_visible": False,
    }
    return SandboxJobStatus(status_id=stable_payload_hash(core), job_id=job.job_id, status=status_kind)


def _validate_argv(command: BoundedCheckCommand, argv: Any) -> None:
    if not isinstance(argv, tuple) or not argv or len(argv) > MAX_ARGV_ITEMS:
        raise CodingExecutionContractError("argv must be a non-empty bounded tuple")
    prefix = _COMMAND_PREFIXES[command]
    if argv[: len(prefix)] != prefix or len(argv) == len(prefix):
        raise CodingExecutionContractError("argv must use one canonical bounded check command")
    for item in argv:
        if not isinstance(item, str) or not item or len(item) > MAX_ARGV_ITEM_LENGTH or _SECRET_RE.search(item):
            raise CodingExecutionContractError("argv contains unsafe content")
    for target in argv[len(prefix):]:
        _repo_path(target, "argv target")


def _repo_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str) or value != value.strip() or not _REPO_PATH_RE.fullmatch(value)
        or value.startswith("/") or value.startswith("~") or re.match(r"^[A-Za-z]:", value)
        or ".." in value.split("/") or value.startswith(("-", "@"))
        or _ENV_ASSIGNMENT_RE.match(value) or _SECRET_RE.search(value)
    ):
        raise CodingExecutionContractError(f"{field} must be a safe repository-relative path")
    return value


def _safe_id(value: Any, field: str) -> str:
    try:
        return strict_id(value, field)
    except CodingLoopContractError as exc:
        raise CodingExecutionContractError(str(exc)) from exc


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CodingExecutionContractError(f"{field} must be canonical SHA-256")
    return value


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingExecutionContractError(f"{field} is invalid") from exc


def _zero_authority(value: Any) -> None:
    for field in ("execution_allowed", "dispatch_allowed", "live_effect_allowed", "raw_content_visible"):
        if hasattr(value, field) and getattr(value, field) is not False:
            raise CodingExecutionContractError(f"{field} must remain false")
    for field in ("execution_performed", "secrets_allowed"):
        if hasattr(value, field) and getattr(value, field) is not False:
            raise CodingExecutionContractError(f"{field} must remain false")


__all__ = [
    "BoundedCheckCommand", "BoundedCheckRequest", "CodingExecutionContractError",
    "SandboxJobRequest", "SandboxJobStatus", "SandboxJobStatusKind", "SandboxMount",
    "SandboxNetworkMode", "SandboxResourceLimits", "SandboxRuntimeProfile",
    "create_bounded_check_request", "create_sandbox_job_request", "reduce_fake_sandbox_status",
]
