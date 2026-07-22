#!/usr/bin/env python3
"""Cross-platform, content-free verification lane entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
VERIFY_RUN_SCHEMA = "odysseus.verify_run.v1"
VERIFY_REGISTRY_SCHEMA = "odysseus.verify_registry.v1"
MAX_VISUAL_EVIDENCE_BYTES = 50 * 1024 * 1024
VISUAL_EVIDENCE_SUFFIXES = frozenset(
    {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".webm"}
)
VISUAL_EVIDENCE_ROOTS = frozenset(
    {"artifacts", "playwright-report", "test-results"}
)


class VerifyExitCode(IntEnum):
    PASSED = 0
    FAILED = 1
    UNAVAILABLE = 2


class CheckStatus(StrEnum):
    PLANNED = "planned"
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class CheckKind(StrEnum):
    INTERNAL = "internal"
    SUBPROCESS = "subprocess"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    kind: CheckKind
    command: tuple[str, ...]
    timeout_seconds: int
    evidence_level: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind.value,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "evidence_level": self.evidence_level,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    check_id: str
    status: CheckStatus
    returncode: int | None
    duration_ms: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, spec: CheckSpec) -> dict[str, Any]:
        return {
            **spec.to_dict(),
            "status": self.status.value,
            "returncode": self.returncode,
            "duration_ms": max(0, self.duration_ms),
            "details": dict(self.details),
        }


class VerificationConfigError(ValueError):
    """Raised for an unknown or structurally invalid verification request."""


class _CheckUnavailable(RuntimeError):
    pass


CHECK_ORDER = (
    "git_diff_check",
    "changed_python_compile",
    "changed_json_parse",
    "pytest_fast",
    "pytest_full",
    "node_syntax",
    "pytest_ui_contract",
    "visual_evidence",
)

LANES: Mapping[str, tuple[str, ...]] = {
    "guards-only": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
    ),
    "fast": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_fast",
    ),
    "full": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_full",
        "node_syntax",
    ),
    "ui": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_ui_contract",
        "visual_evidence",
    ),
}

LANE_LIMITS: Mapping[str, tuple[str, ...]] = {
    "guards-only": (
        "integration_not_verified",
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "fast": (
        "slow_tests_not_verified",
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "full": (
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "ui": (
        "visual_evidence_availability_is_not_semantic_review",
        "live_not_verified",
        "temporal_not_verified",
    ),
}


def build_check_registry() -> dict[str, CheckSpec]:
    registry = {
        "git_diff_check": CheckSpec(
            check_id="git_diff_check",
            kind=CheckKind.INTERNAL,
            command=("git", "diff", "--check", "HEAD", "--"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "changed_python_compile": CheckSpec(
            check_id="changed_python_compile",
            kind=CheckKind.INTERNAL,
            command=("internal", "compile-changed-python"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "changed_json_parse": CheckSpec(
            check_id="changed_json_parse",
            kind=CheckKind.INTERNAL,
            command=("internal", "parse-changed-json"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "pytest_fast": CheckSpec(
            check_id="pytest_fast",
            kind=CheckKind.SUBPROCESS,
            command=(
                "{python}",
                "tests/run_focus.py",
                "--fast",
                "--",
                "-q",
            ),
            timeout_seconds=1_200,
            evidence_level="fast",
        ),
        "pytest_full": CheckSpec(
            check_id="pytest_full",
            kind=CheckKind.SUBPROCESS,
            command=("{python}", "-m", "pytest", "-q"),
            timeout_seconds=3_600,
            evidence_level="full",
        ),
        "node_syntax": CheckSpec(
            check_id="node_syntax",
            kind=CheckKind.INTERNAL,
            command=("node", "--check", "static/js/**/*.js"),
            timeout_seconds=600,
            evidence_level="full",
        ),
        "pytest_ui_contract": CheckSpec(
            check_id="pytest_ui_contract",
            kind=CheckKind.SUBPROCESS,
            command=(
                "{python}",
                "tests/run_focus.py",
                "--area",
                "js",
                "--fast",
                "--",
                "-q",
            ),
            timeout_seconds=1_200,
            evidence_level="ui_contract",
        ),
        "visual_evidence": CheckSpec(
            check_id="visual_evidence",
            kind=CheckKind.EVIDENCE,
            command=("evidence", "visual-artifact"),
            timeout_seconds=30,
            evidence_level="visual",
        ),
    }
    if tuple(registry) != CHECK_ORDER:
        raise VerificationConfigError("check registry order is not canonical")
    return registry


def resolve_lane(
    lane: object,
    *,
    registry: Mapping[str, CheckSpec] | None = None,
) -> tuple[CheckSpec, ...]:
    if not isinstance(lane, str) or lane not in LANES:
        raise VerificationConfigError("unknown verification lane")
    registry = registry or build_check_registry()
    try:
        checks = tuple(registry[check_id] for check_id in LANES[lane])
    except KeyError as exc:
        raise VerificationConfigError("lane references an unavailable check") from exc
    if any(not check.required for check in checks):
        raise VerificationConfigError("lane contains a non-required check")
    return checks


def registry_payload() -> dict[str, Any]:
    registry = build_check_registry()
    return {
        "schema": VERIFY_REGISTRY_SCHEMA,
        "lanes": {lane: list(checks) for lane, checks in LANES.items()},
        "checks": [registry[check_id].to_dict() for check_id in CHECK_ORDER],
    }


def run_lane(
    lane: str,
    *,
    root: Path = ROOT,
    visual_evidence: Path | None = None,
    dry_run: bool = False,
    check_runner: Callable[[CheckSpec], CheckOutcome] | None = None,
) -> tuple[dict[str, Any], VerifyExitCode]:
    registry = build_check_registry()
    specs = resolve_lane(lane, registry=registry)
    if dry_run:
        outcomes = [
            CheckOutcome(
                check_id=spec.check_id,
                status=CheckStatus.PLANNED,
                returncode=None,
                duration_ms=0,
            )
            for spec in specs
        ]
        exit_code = VerifyExitCode.PASSED
        status = "planned"
    else:
        outcomes = []
        for spec in specs:
            if check_runner is None:
                outcome = run_check(
                    spec,
                    root=root,
                    visual_evidence=visual_evidence,
                )
            else:
                outcome = check_runner(spec)
            if outcome.check_id != spec.check_id:
                raise VerificationConfigError("check runner returned a mismatched id")
            outcomes.append(outcome)
        exit_code = _exit_code(outcomes)
        status = (
            "passed"
            if exit_code == VerifyExitCode.PASSED
            else "failed"
            if exit_code == VerifyExitCode.FAILED
            else "unavailable"
        )

    report = {
        "schema": VERIFY_RUN_SCHEMA,
        "lane": lane,
        "status": status,
        "exit_code": int(exit_code),
        "strongest_evidence_level": _strongest_level(lane, outcomes),
        "checks": [
            outcome.to_dict(spec)
            for spec, outcome in zip(specs, outcomes, strict=True)
        ],
        "verification_limits": list(LANE_LIMITS[lane]),
    }
    return report, exit_code


def run_check(
    spec: CheckSpec,
    *,
    root: Path,
    visual_evidence: Path | None,
) -> CheckOutcome:
    started = time.monotonic()
    status = CheckStatus.FAILED
    returncode: int | None = 1
    details: dict[str, Any] = {}
    try:
        if spec.check_id == "git_diff_check":
            _git_diff_check(root, timeout=spec.timeout_seconds)
        elif spec.check_id == "changed_python_compile":
            _compile_changed_python(root)
        elif spec.check_id == "changed_json_parse":
            _parse_changed_json(root)
        elif spec.check_id == "node_syntax":
            details = _node_syntax(root, timeout=spec.timeout_seconds)
        elif spec.check_id == "visual_evidence":
            details = _visual_evidence(visual_evidence, root=root)
        elif spec.kind == CheckKind.SUBPROCESS:
            _run_subprocess_check(spec, root=root)
        else:
            raise VerificationConfigError("check has no registered implementation")
        status = CheckStatus.PASSED
        returncode = 0
    except _CheckUnavailable:
        status = CheckStatus.UNAVAILABLE
        returncode = None
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        SyntaxError,
        subprocess.TimeoutExpired,
    ):
        status = CheckStatus.FAILED
        returncode = 1
    duration_ms = min(
        int((time.monotonic() - started) * 1_000),
        spec.timeout_seconds * 1_000,
    )
    return CheckOutcome(
        check_id=spec.check_id,
        status=status,
        returncode=returncode,
        duration_ms=duration_ms,
        details=details,
    )


def _git_diff_check(root: Path, *, timeout: int) -> None:
    try:
        completed = subprocess.run(
            ["git", "diff", "--check", "HEAD", "--"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _CheckUnavailable from exc
    if completed.returncode != 0:
        raise ValueError("git diff check failed")


def _changed_paths(root: Path) -> tuple[Path, ...]:
    commands = (
        ("git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"),
        ("git", "ls-files", "--others", "--exclude-standard"),
    )
    names: set[str] = set()
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise _CheckUnavailable from exc
        if completed.returncode != 0:
            raise _CheckUnavailable
        for line in completed.stdout.decode("utf-8", errors="strict").splitlines():
            normalized = line.strip().replace("\\", "/")
            if not normalized:
                continue
            candidate = Path(normalized)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("unsafe changed path")
            names.add(candidate.as_posix())
    return tuple(root / name for name in sorted(names))


def _compile_changed_python(root: Path) -> None:
    for path in _changed_paths(root):
        if path.suffix.lower() != ".py" or not path.is_file():
            continue
        source = path.read_text(encoding="utf-8-sig")
        compile(source, "<changed-python>", "exec", dont_inherit=True)


def _parse_changed_json(root: Path) -> None:
    for path in _changed_paths(root):
        if path.suffix.lower() != ".json" or not path.is_file():
            continue
        json.loads(path.read_text(encoding="utf-8-sig"))


def _node_syntax(root: Path, *, timeout: int) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise _CheckUnavailable
    paths = tuple(sorted((root / "static" / "js").rglob("*.js")))
    if not paths:
        raise _CheckUnavailable
    started = time.monotonic()
    checked = 0
    for path in paths:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("node syntax timeout")
        completed = subprocess.run(
            [node, "--check", str(path.relative_to(root))],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("node syntax check failed")
        checked += 1
    return {"checked_file_count": checked}


def _run_subprocess_check(spec: CheckSpec, *, root: Path) -> None:
    command = [
        sys.executable if token == "{python}" else token
        for token in spec.command
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _CheckUnavailable from exc
    if completed.returncode != 0:
        raise ValueError("verification subprocess failed")


def _visual_evidence(path: Path | None, *, root: Path) -> dict[str, Any]:
    if path is None:
        raise _CheckUnavailable
    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix.lower() not in VISUAL_EVIDENCE_SUFFIXES:
        raise _CheckUnavailable
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise _CheckUnavailable from exc
    if not relative.parts or relative.parts[0] not in VISUAL_EVIDENCE_ROOTS:
        raise _CheckUnavailable
    size = resolved.stat().st_size
    if not 0 < size <= MAX_VISUAL_EVIDENCE_BYTES:
        raise ValueError("visual evidence size is invalid")
    with resolved.open("rb") as handle:
        header = handle.read(16)
    media_type = _validated_media_type(resolved.suffix.lower(), header)
    if media_type is None:
        raise ValueError("visual evidence format is invalid")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return {
        "available": True,
        "bytes": size,
        "media_type": media_type,
        "sha256": digest.hexdigest(),
    }


def _validated_media_type(suffix: str, header: bytes) -> str | None:
    signatures = {
        ".gif": header.startswith((b"GIF87a", b"GIF89a")),
        ".jpeg": header.startswith(b"\xff\xd8\xff"),
        ".jpg": header.startswith(b"\xff\xd8\xff"),
        ".mp4": len(header) >= 8 and header[4:8] == b"ftyp",
        ".png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webm": header.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if suffix == ".webp":
        valid = (
            len(header) >= 12
            and header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        )
    else:
        valid = signatures.get(suffix, False)
    return suffix.lstrip(".") if valid else None


def _exit_code(outcomes: Sequence[CheckOutcome]) -> VerifyExitCode:
    if any(outcome.status == CheckStatus.FAILED for outcome in outcomes):
        return VerifyExitCode.FAILED
    if any(outcome.status == CheckStatus.UNAVAILABLE for outcome in outcomes):
        return VerifyExitCode.UNAVAILABLE
    if not outcomes or any(outcome.status != CheckStatus.PASSED for outcome in outcomes):
        return VerifyExitCode.FAILED
    return VerifyExitCode.PASSED


def _strongest_level(
    lane: str,
    outcomes: Sequence[CheckOutcome],
) -> str:
    if not outcomes or any(outcome.status != CheckStatus.PASSED for outcome in outcomes):
        return "none"
    return {
        "guards-only": "static",
        "fast": "fast",
        "full": "full",
        "ui": "ui_contract_plus_visual_artifact",
    }[lane]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one named Odysseus verification lane."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--lane", choices=tuple(LANES))
    target.add_argument("--list", action="store_true", help="print the registry")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact content-free plan without running checks",
    )
    parser.add_argument(
        "--visual-evidence",
        type=Path,
        help="existing visual artifact required by the ui lane",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        payload = registry_payload()
        exit_code = VerifyExitCode.PASSED
    else:
        payload, exit_code = run_lane(
            args.lane,
            visual_evidence=args.visual_evidence,
            dry_run=args.dry_run,
        )
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
