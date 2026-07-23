"""Declarative contract for bounded, offline Pygame headless verification.

The objects in this module only describe work for a future sandbox adapter.
They never import Pygame, spawn processes, read files, or access the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


PYGAME_HEADLESS_CONTRACT_SCHEMA = "odysseus.pygame.headless_plan.v1"
PYGAME_HEADLESS_STATUS_SCHEMA = "odysseus.pygame.headless_status.v1"

SDL_DUMMY_ENVIRONMENT: tuple[tuple[str, str], ...] = (
    ("SDL_VIDEODRIVER", "dummy"),
    ("SDL_AUDIODRIVER", "dummy"),
)

REQUIRED_HEADLESS_EVIDENCE: tuple[str, ...] = (
    "syntax_check_passed",
    "pygame_import_probe_passed",
    "bounded_frame_run_passed",
    "screenshot_artifact_recorded",
)

MIN_FRAME_COUNT = 1
MAX_FRAME_COUNT = 1_800
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60
MIN_SCREENSHOT_BYTES = 1_024
MAX_SCREENSHOT_BYTES = 10_000_000

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SENSITIVE_SEGMENTS = frozenset(
    {
        ".env",
        "credential",
        "credentials",
        "id_ed25519",
        "id_rsa",
        "private-key",
        "private_key",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)


class PygameHeadlessContractError(ValueError):
    """Raised when a Pygame headless plan is unsafe or internally invalid."""


@dataclass(frozen=True, slots=True)
class PygameHeadlessLimits:
    """Hard bounds a future runner must enforce."""

    max_frames: int = 120
    timeout_seconds: int = 10
    screenshot_frame: int = 1

    @classmethod
    def create(
        cls,
        *,
        max_frames: Any = 120,
        timeout_seconds: Any = 10,
        screenshot_frame: Any = 1,
    ) -> "PygameHeadlessLimits":
        frame_limit = _bounded_int(
            max_frames,
            field_name="max_frames",
            minimum=MIN_FRAME_COUNT,
            maximum=MAX_FRAME_COUNT,
        )
        timeout = _bounded_int(
            timeout_seconds,
            field_name="timeout_seconds",
            minimum=MIN_TIMEOUT_SECONDS,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        capture_at = _bounded_int(
            screenshot_frame,
            field_name="screenshot_frame",
            minimum=MIN_FRAME_COUNT,
            maximum=frame_limit,
        )
        return cls(
            max_frames=frame_limit,
            timeout_seconds=timeout,
            screenshot_frame=capture_at,
        )

    def to_redacted_dict(self) -> dict[str, int]:
        return {
            "max_frames": self.max_frames,
            "timeout_seconds": self.timeout_seconds,
            "screenshot_frame": self.screenshot_frame,
        }


@dataclass(frozen=True, slots=True)
class PygameScreenshotArtifact:
    """Expected screenshot output; content and host paths are never embedded."""

    ref: str
    max_bytes: int = 5_000_000
    artifact_id: str = "pygame_headless_screenshot"
    media_type: str = "image/png"

    @classmethod
    def create(
        cls,
        *,
        ref: Any,
        max_bytes: Any = 5_000_000,
    ) -> "PygameScreenshotArtifact":
        safe_ref = _safe_repo_ref(ref, field_name="screenshot_ref")
        if not safe_ref.lower().endswith(".png"):
            raise PygameHeadlessContractError("screenshot_ref must point to a PNG artifact")
        return cls(
            ref=safe_ref,
            max_bytes=_bounded_int(
                max_bytes,
                field_name="max_screenshot_bytes",
                minimum=MIN_SCREENSHOT_BYTES,
                maximum=MAX_SCREENSHOT_BYTES,
            ),
        )

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": "screenshot",
            "ref": self.ref,
            "media_type": self.media_type,
            "required": True,
            "max_bytes": self.max_bytes,
            "digest_algorithm": "sha256",
            "digest_required": True,
            "content_embedded": False,
            "host_path_visible": False,
        }


@dataclass(frozen=True, slots=True)
class PygameHeadlessPlan:
    """A safe, execution-free plan for a later sandbox implementation."""

    source_ref: str
    limits: PygameHeadlessLimits
    screenshot: PygameScreenshotArtifact
    schema: str = PYGAME_HEADLESS_CONTRACT_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        source_ref: Any,
        screenshot_ref: Any = "artifacts/pygame/headless.png",
        max_frames: Any = 120,
        timeout_seconds: Any = 10,
        screenshot_frame: Any = 1,
        max_screenshot_bytes: Any = 5_000_000,
        secrets_attached: bool = False,
    ) -> "PygameHeadlessPlan":
        if secrets_attached is not False:
            raise PygameHeadlessContractError("secrets are not allowed in headless verification plans")

        safe_source_ref = _safe_repo_ref(source_ref, field_name="source_ref")
        if not safe_source_ref.lower().endswith(".py"):
            raise PygameHeadlessContractError("source_ref must point to a Python file")

        limits = PygameHeadlessLimits.create(
            max_frames=max_frames,
            timeout_seconds=timeout_seconds,
            screenshot_frame=screenshot_frame,
        )
        screenshot = PygameScreenshotArtifact.create(
            ref=screenshot_ref,
            max_bytes=max_screenshot_bytes,
        )
        if safe_source_ref == screenshot.ref:
            raise PygameHeadlessContractError("source_ref and screenshot_ref must differ")
        return cls(source_ref=safe_source_ref, limits=limits, screenshot=screenshot)

    def to_redacted_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-compatible data with no content or secrets."""

        environment = {name: value for name, value in SDL_DUMMY_ENVIRONMENT}
        checks: list[dict[str, Any]] = [
            {
                "check_id": "python_syntax_check",
                "kind": "syntax_check",
                "input_ref": self.source_ref,
                "required": True,
                "produces_evidence": "syntax_check_passed",
            },
            {
                "check_id": "pygame_import_probe",
                "kind": "python_import_probe",
                "module": "pygame",
                "required": True,
                "produces_evidence": "pygame_import_probe_passed",
            },
            {
                "check_id": "bounded_dummy_sdl_frame_run",
                "kind": "headless_frame_run",
                "input_ref": self.source_ref,
                "environment_keys": [name for name, _value in SDL_DUMMY_ENVIRONMENT],
                "max_frames": self.limits.max_frames,
                "timeout_seconds": self.limits.timeout_seconds,
                "required": True,
                "produces_evidence": "bounded_frame_run_passed",
            },
            {
                "check_id": "screenshot_capture",
                "kind": "screenshot_capture",
                "artifact_id": self.screenshot.artifact_id,
                "capture_at_frame": self.limits.screenshot_frame,
                "required": True,
                "produces_evidence": "screenshot_artifact_recorded",
            },
        ]
        return {
            "schema": self.schema,
            "execution": {
                "mode": "planned_only",
                "processes_started": False,
                "network_mode": "none",
                "secrets_attached": False,
            },
            "source": {
                "ref": self.source_ref,
                "content_embedded": False,
                "host_path_visible": False,
            },
            "environment": environment,
            "limits": self.limits.to_redacted_dict(),
            "checks": checks,
            "artifacts": [self.screenshot.to_redacted_dict()],
            "required_evidence": list(REQUIRED_HEADLESS_EVIDENCE),
            "claim_semantics": {
                "headless_verified_does_not_imply_interactive_ready": True,
                "interactive_ready": False,
                "interactive_validation_required": True,
            },
        }


@dataclass(frozen=True, slots=True)
class PygameHeadlessVerificationStatus:
    """Interpret externally supplied check booleans without executing checks."""

    passed_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    headless_verified: bool
    interactive_ready: bool = field(default=False, init=False)
    schema: str = PYGAME_HEADLESS_STATUS_SCHEMA

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": "headless_verified" if self.headless_verified else "headless_unverified",
            "passed_evidence": list(self.passed_evidence),
            "missing_evidence": list(self.missing_evidence),
            "headless_verified": self.headless_verified,
            "interactive_ready": False,
            "semantic_boundary": "headless_verified_does_not_imply_interactive_ready",
            "interactive_ready_requires": "separate_visible_interactive_validation",
        }


def build_pygame_headless_plan(**kwargs: Any) -> PygameHeadlessPlan:
    """Build a declarative plan; this function performs validation only."""

    return PygameHeadlessPlan.create(**kwargs)


def evaluate_pygame_headless_evidence(
    *,
    syntax_check_passed: bool,
    pygame_import_probe_passed: bool,
    bounded_frame_run_passed: bool,
    screenshot_artifact_recorded: bool,
) -> PygameHeadlessVerificationStatus:
    """Map known evidence flags to a claim-safe headless status."""

    flags = {
        "syntax_check_passed": syntax_check_passed,
        "pygame_import_probe_passed": pygame_import_probe_passed,
        "bounded_frame_run_passed": bounded_frame_run_passed,
        "screenshot_artifact_recorded": screenshot_artifact_recorded,
    }
    for name, value in flags.items():
        if not isinstance(value, bool):
            raise PygameHeadlessContractError(f"{name} must be a boolean")

    passed = tuple(name for name in REQUIRED_HEADLESS_EVIDENCE if flags[name])
    missing = tuple(name for name in REQUIRED_HEADLESS_EVIDENCE if not flags[name])
    return PygameHeadlessVerificationStatus(
        passed_evidence=passed,
        missing_evidence=missing,
        headless_verified=not missing,
    )


def _safe_repo_ref(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if (
        not text
        or text.startswith(("/", "./", "~/"))
        or _WINDOWS_DRIVE_RE.match(text)
        or "://" in text
        or not _SAFE_REF_RE.fullmatch(text)
    ):
        raise PygameHeadlessContractError(f"{field_name} must be a safe repo-relative ref")

    segments = text.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise PygameHeadlessContractError(f"{field_name} contains unsafe path segments")
    lowered_segments = tuple(segment.lower() for segment in segments)
    if any(
        segment in _SENSITIVE_SEGMENTS or segment.startswith(".env.")
        for segment in lowered_segments
    ):
        raise PygameHeadlessContractError(f"{field_name} must not reference secret material")
    return "/".join(segments)


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise PygameHeadlessContractError(f"{field_name} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        number = int(value.strip())
    else:
        raise PygameHeadlessContractError(f"{field_name} must be an integer")
    if number < minimum or number > maximum:
        raise PygameHeadlessContractError(f"{field_name} out of range")
    return number
