"""Safe sandbox job contract for disposable Podman-backed agent work."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from src.sandbox_network_policy import build_sandbox_network_policy


SANDBOX_JOB_SCHEMA = "odysseus.agent.sandbox_job.v1"
DEFAULT_SANDBOX_CAPABILITIES = (
    "python",
    "node",
    "playwright",
    "browser_gui",
    "screenshot_artifacts",
)

_ARG_RE = re.compile(r"^[^\r\n\x00]{1,240}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,48}$")
_FORBIDDEN_EXECUTABLES = {"rm", "shutdown", "reboot", "mkfs", "mount", "umount", "docker"}
_FORBIDDEN_ARG_PARTS = ("--privileged", "/var/run/docker.sock", "&&", "||", ";", "`", "$(")


class SandboxContractError(ValueError):
    """Raised when a sandbox job contract is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class SandboxResourceLimits:
    timeout_seconds: int = 120
    memory_mb: int = 1024
    cpu_count: float = 1.0
    output_bytes: int = 200_000
    artifact_bytes: int = 5_000_000

    @classmethod
    def create(cls, **kwargs: Any) -> "SandboxResourceLimits":
        return cls(
            timeout_seconds=_bounded_int(kwargs.get("timeout_seconds", 120), "timeout_seconds", 1, 7200),
            memory_mb=_bounded_int(kwargs.get("memory_mb", 1024), "memory_mb", 64, 32768),
            cpu_count=max(0.1, min(16.0, float(kwargs.get("cpu_count", 1.0)))),
            output_bytes=_bounded_int(kwargs.get("output_bytes", 200_000), "output_bytes", 1024, 5_000_000),
            artifact_bytes=_bounded_int(kwargs.get("artifact_bytes", 5_000_000), "artifact_bytes", 1024, 100_000_000),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "memory_mb": self.memory_mb,
            "cpu_count": self.cpu_count,
            "output_bytes": self.output_bytes,
            "artifact_bytes": self.artifact_bytes,
        }


@dataclass(frozen=True, slots=True)
class SandboxMount:
    source: str
    target: str
    mode: str = "ro"

    @classmethod
    def create(cls, *, source: Any, target: Any, mode: Any = "ro") -> "SandboxMount":
        normalized_mode = str(mode or "ro").lower()
        if normalized_mode not in {"ro", "rw"}:
            raise SandboxContractError("mount mode must be ro or rw")
        return cls(
            source=_repo_path(source, field_name="source"),
            target=_container_path(target, field_name="target"),
            mode=normalized_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "mode": self.mode}


@dataclass(frozen=True, slots=True)
class SandboxJobRequest:
    job_id: str
    argv: tuple[str, ...]
    image: str
    mounts: tuple[SandboxMount, ...]
    limits: SandboxResourceLimits
    network_mode: str = "none"
    network_allowlist: tuple[str, ...] = ()
    secrets_attached: bool = False
    capabilities: tuple[str, ...] = DEFAULT_SANDBOX_CAPABILITIES
    schema: str = SANDBOX_JOB_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        job_id: Any,
        argv: Iterable[Any],
        image: Any,
        mounts: Iterable[SandboxMount | dict[str, Any]] = (),
        limits: SandboxResourceLimits | dict[str, Any] | None = None,
        network_mode: Any = "none",
        network_allowlist: Iterable[Any] = (),
        secrets_attached: bool = False,
        capabilities: Iterable[Any] | None = None,
    ) -> "SandboxJobRequest":
        args = _argv(argv)
        mount_tuple = tuple(m if isinstance(m, SandboxMount) else SandboxMount.create(**m) for m in mounts)
        limit_obj = limits if isinstance(limits, SandboxResourceLimits) else SandboxResourceLimits.create(**(limits or {}))
        net = str(network_mode or "none").lower()
        try:
            network_policy = build_sandbox_network_policy(mode=net, allowlist=network_allowlist)
        except ValueError as exc:
            raise SandboxContractError(str(exc)) from exc
        if net == "fullweb":
            raise SandboxContractError("fullweb network requires a separate live gate")
        if not network_policy.allowed:
            raise SandboxContractError(",".join(network_policy.reasons))
        return cls(
            job_id=_slug(job_id, field_name="job_id"),
            argv=args,
            image=_image(image),
            mounts=mount_tuple,
            limits=limit_obj,
            network_mode=net,
            network_allowlist=network_policy.allowlist,
            secrets_attached=bool(secrets_attached),
            capabilities=_capabilities(capabilities),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "argv": self.argv,
            "image": self.image,
            "mounts": tuple(mount.to_dict() for mount in self.mounts),
            "limits": self.limits.to_dict(),
            "network_mode": self.network_mode,
            "network_allowlist": self.network_allowlist,
            "secrets_attached": self.secrets_attached,
            "capabilities": self.capabilities,
        }


@dataclass(frozen=True, slots=True)
class SandboxPolicyDecision:
    allowed: bool
    reason: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "warnings": self.warnings}


def evaluate_sandbox_job(job: SandboxJobRequest) -> SandboxPolicyDecision:
    if not isinstance(job, SandboxJobRequest):
        raise SandboxContractError("job must be a SandboxJobRequest")
    warnings: list[str] = []
    executable = job.argv[0].lower()
    if executable in _FORBIDDEN_EXECUTABLES:
        return SandboxPolicyDecision(False, "forbidden_executable", ())
    joined = " ".join(job.argv).lower()
    if any(part in joined for part in _FORBIDDEN_ARG_PARTS):
        return SandboxPolicyDecision(False, "forbidden_shell_or_privileged_pattern", ())
    if job.secrets_attached and job.network_mode != "none":
        return SandboxPolicyDecision(False, "secrets_with_network_blocked", ())
    if any(mount.mode == "rw" for mount in job.mounts):
        warnings.append("write_mount_requires_scope_review")
    return SandboxPolicyDecision(True, "allowed", tuple(warnings))


def _argv(values: Iterable[Any]) -> tuple[str, ...]:
    args = tuple(str(value) for value in values)
    if not args:
        raise SandboxContractError("argv must not be empty")
    for arg in args:
        if not _ARG_RE.fullmatch(arg):
            raise SandboxContractError("argv contains unsafe argument")
        lowered_arg = arg.lower()
        if any(part in lowered_arg for part in _FORBIDDEN_ARG_PARTS):
            raise SandboxContractError("argv contains shell or privileged pattern")
    return args


def _repo_path(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("/") or text.startswith("./") or re.match(r"^[A-Za-z]:", text):
        raise SandboxContractError(f"{field_name} must be repo-relative")
    if ".." in text.split("/") or not _PATH_RE.fullmatch(text):
        raise SandboxContractError(f"{field_name} is unsafe")
    return text


def _container_path(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text.startswith("/workspace/"):
        raise SandboxContractError(f"{field_name} must be under /workspace")
    if ".." in text.split("/"):
        raise SandboxContractError(f"{field_name} is unsafe")
    return text


def _image(value: Any) -> str:
    text = str(value or "").strip()
    if not text or any(part in text for part in ("\r", "\n", " ", ";")):
        raise SandboxContractError("image is unsafe")
    return text[:160]


def _slug(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", text):
        raise SandboxContractError(f"{field_name} is unsafe")
    return text


def _capabilities(values: Iterable[Any] | None) -> tuple[str, ...]:
    raw_values = DEFAULT_SANDBOX_CAPABILITIES if values is None else tuple(values)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = str(raw or "").strip().lower().replace(" ", "_")
        if not text:
            continue
        if not _CAPABILITY_RE.fullmatch(text):
            raise SandboxContractError("capability is unsafe")
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _bounded_int(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SandboxContractError(f"{field_name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise SandboxContractError(f"{field_name} out of range")
    return number
