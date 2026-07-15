"""Fail-closed configuration for the localhost-only Temporal Light runtime.

The CLI development server is intentionally a foreground development tool. It
is not a production service, a scheduler authority, or permission to run Agent
effects. Runtime code outside this module should consume the validated config
instead of rebuilding CLI arguments.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence


SDK_VERSION = "1.30.0"
CLI_VERSION = "1.8.0"
LOOPBACK_HOST = "127.0.0.1"
GRPC_PORT = 7233
DEFAULT_NAMESPACE = "default"
DEFAULT_TASK_QUEUE = "odysseus-temporal-light"
_RUNTIME_ENV = "ODYSSEUS_TEMPORAL_RUNTIME_DIR"
_CLI_ENV = "ODYSSEUS_TEMPORAL_CLI_PATH"
_HOST_ENV = "ODYSSEUS_TEMPORAL_HOST"
_PORT_ENV = "ODYSSEUS_TEMPORAL_PORT"


class TemporalLightConfigError(RuntimeError):
    """Raised when a local Temporal Light invariant is not satisfied."""


@dataclass(frozen=True)
class TemporalLightConfig:
    """Validated immutable inputs for one local development-server process."""

    cli_path: Path
    runtime_dir: Path
    repo_root: Path
    host: str = LOOPBACK_HOST
    port: int = GRPC_PORT
    namespace: str = DEFAULT_NAMESPACE
    task_queue: str = DEFAULT_TASK_QUEUE
    headless: bool = True
    development_only: bool = True

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "temporal.db"

    def start_command(self) -> tuple[str, ...]:
        return (
            str(self.cli_path),
            "server",
            "start-dev",
            "--db-filename",
            str(self.db_path),
            "--headless",
            "--ip",
            self.host,
            "--port",
            str(self.port),
            "--namespace",
            self.namespace,
        )

    def health_command(self) -> tuple[str, ...]:
        return (
            str(self.cli_path),
            "operator",
            "cluster",
            "health",
            "--address",
            self.address,
        )

    def public_descriptor(self) -> dict[str, object]:
        """Return operator-safe config without persisting a private host path."""

        return {
            "schema_id": "odysseus.temporal_light.config.v1",
            "sdk_version": SDK_VERSION,
            "cli_version": CLI_VERSION,
            "address": self.address,
            "namespace": self.namespace,
            "task_queue": self.task_queue,
            "headless": self.headless,
            "development_only": self.development_only,
            "cli_path": "<local-user-data>/Odysseus/TemporalLight/bin/temporal",
            "db_path": "<local-user-data>/Odysseus/TemporalLight/runtime/temporal.db",
        }


@dataclass(frozen=True)
class TemporalHealth:
    healthy: bool
    address: str
    output: str


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_local_root(
    environ: Mapping[str, str], *, platform_name: str
) -> Path:
    if platform_name.startswith("win"):
        local_app_data = environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise TemporalLightConfigError(
                "LOCALAPPDATA is required for the Windows Temporal Light runtime"
            )
        return Path(local_app_data) / "Odysseus" / "TemporalLight"

    state_home = environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "odysseus" / "temporal-light"
    return Path.home() / ".local" / "state" / "odysseus" / "temporal-light"


def _is_within(path: Path, root: Path) -> bool:
    path_text = os.path.normcase(str(path.resolve(strict=False)))
    root_text = os.path.normcase(str(root.resolve(strict=False)))
    try:
        return os.path.commonpath((path_text, root_text)) == root_text
    except ValueError:
        return False


def _parse_fixed_port(raw: str) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise TemporalLightConfigError("Temporal Light port must be an integer") from exc
    if port != GRPC_PORT:
        raise TemporalLightConfigError(
            f"Temporal Light is locked to localhost port {GRPC_PORT}"
        )
    return port


def load_temporal_light_config(
    environ: Mapping[str, str] | None = None,
    *,
    repo_root: Path | None = None,
    platform_name: str | None = None,
) -> TemporalLightConfig:
    """Load one immutable, localhost-only development configuration."""

    env = dict(os.environ if environ is None else environ)
    platform_value = platform_name or sys.platform
    root = _default_local_root(env, platform_name=platform_value)

    host = env.get(_HOST_ENV, LOOPBACK_HOST).strip()
    if host != LOOPBACK_HOST:
        raise TemporalLightConfigError(
            f"Temporal Light must bind exactly to {LOOPBACK_HOST}, not {host!r}"
        )
    port = _parse_fixed_port(env.get(_PORT_ENV, str(GRPC_PORT)))

    runtime_dir = Path(env.get(_RUNTIME_ENV, str(root / "runtime"))).expanduser()
    repo = (repo_root or Path(__file__).resolve().parents[2]).resolve(strict=False)
    runtime_dir = runtime_dir.resolve(strict=False)
    if _is_within(runtime_dir, repo):
        raise TemporalLightConfigError(
            "Temporal Light persistence must remain outside the repository"
        )

    configured_cli = env.get(_CLI_ENV)
    if configured_cli:
        cli_path = Path(configured_cli).expanduser()
    elif platform_value.startswith("win"):
        cli_path = root / "bin" / "temporal.exe"
    else:
        cli_path = Path(shutil.which("temporal") or (root / "bin" / "temporal"))

    return TemporalLightConfig(
        cli_path=cli_path.resolve(strict=False),
        runtime_dir=runtime_dir,
        repo_root=repo,
        host=host,
        port=port,
    )


def _run_checked(
    command: Sequence[str],
    *,
    runner: CommandRunner,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TemporalLightConfigError(
            f"Temporal command could not be executed: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no output").strip()
        raise TemporalLightConfigError(
            f"Temporal command failed with exit {completed.returncode}: {detail}"
        )
    return completed


def assert_installed_capabilities(
    config: TemporalLightConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Verify the exact SDK and CLI pins before starting a process."""

    try:
        sdk_version = importlib.metadata.version("temporalio")
    except importlib.metadata.PackageNotFoundError as exc:
        raise TemporalLightConfigError("temporalio is not installed") from exc
    if sdk_version != SDK_VERSION:
        raise TemporalLightConfigError(
            f"temporalio {SDK_VERSION} is required; found {sdk_version}"
        )
    if not config.cli_path.is_file():
        raise TemporalLightConfigError("the pinned Temporal CLI executable is missing")

    completed = _run_checked(
        (str(config.cli_path), "--version"),
        runner=runner,
        timeout_seconds=10,
    )
    version_output = f"{completed.stdout}\n{completed.stderr}"
    if f"temporal version {CLI_VERSION}" not in version_output.lower():
        raise TemporalLightConfigError(f"Temporal CLI {CLI_VERSION} is required")


def check_temporal_health(
    config: TemporalLightConfig,
    *,
    runner: CommandRunner = subprocess.run,
    timeout_seconds: float = 10,
) -> TemporalHealth:
    completed = _run_checked(
        config.health_command(), runner=runner, timeout_seconds=timeout_seconds
    )
    output = (completed.stdout or completed.stderr or "healthy").strip()
    return TemporalHealth(healthy=True, address=config.address, output=output)


def serve_temporal_light(config: TemporalLightConfig) -> int:
    assert_installed_capabilities(config)
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    print(
        "TEMPORAL LIGHT DEVELOPMENT ONLY — localhost, headless, non-production",
        flush=True,
    )
    try:
        return subprocess.run(config.start_command(), check=False).returncode
    except KeyboardInterrupt:
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("describe", "check", "health", "serve"),
        nargs="?",
        default="describe",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = load_temporal_light_config()
        if args.action == "describe":
            print(json.dumps(config.public_descriptor(), sort_keys=True))
            return 0
        if args.action == "check":
            assert_installed_capabilities(config)
            print(json.dumps(config.public_descriptor(), sort_keys=True))
            return 0
        if args.action == "health":
            assert_installed_capabilities(config)
            print(json.dumps(check_temporal_health(config).__dict__, sort_keys=True))
            return 0
        return serve_temporal_light(config)
    except TemporalLightConfigError as exc:
        print(f"Temporal Light configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
