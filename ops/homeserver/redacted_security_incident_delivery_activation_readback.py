#!/usr/bin/env python3
"""Independent fixed-key readback for the delivery-flag activation transaction."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

SCHEMA_ID = "odysseus.redacted_security_incident_delivery_activation_readback.v1"
TARGET_ROOT = "/opt/odysseus"
APP_CONTAINER = "odysseus_odysseus_1"
CHROMA_CONTAINER = "odysseus_chromadb_1"
_DEPENDENCIES = (CHROMA_CONTAINER, "odysseus_searxng_1", "odysseus_ollama_1", "odysseus_ntfy_1")
_HEX40, _HEX64, _ID = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^[0-9a-f]{12,64}$")
_VISIBILITY = frozenset({"raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible"})
_PROOFS = frozenset({"revision_matches", "manifest_matches", "app_healthy", "chroma_healthy", "dependencies_unchanged", "mounts_intact", "delivery_enabled"})
_KEYS = frozenset({"schema_id", "status", *_PROOFS, *_VISIBILITY, "evidence_sha256"})
_MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME": "/home/homebase", "USER": "homebase", "LOGNAME": "homebase", "XDG_RUNTIME_DIR": f"/run/user/{getattr(os, 'getuid', lambda: 1000)()}"}
_EXPECTED_MOUNTS = frozenset({"/opt/odysseus/data:/app/data", "/opt/odysseus/logs:/app/logs", "/opt/odysseus/data/universal-inbox:/app/universal-inbox"})
_MANIFEST_PROGRAM = "import sys;sys.path.insert(0,'/app');import hashlib;from src.constants import RELEASE_MANIFEST_FILE;from src.release_manifest import read_release_manifest;d,s=read_release_manifest(RELEASE_MANIFEST_FILE,expected_revision=__import__('os').environ.get('ODYSSEUS_RELEASE_REVISION'));print('ok' if s=='ready' and hashlib.sha256(open(RELEASE_MANIFEST_FILE,'rb').read()).hexdigest()==sys.argv[1] else 'bad')"
_VERSION_PROGRAM = "import sys;sys.path.insert(0,'/app');import os;from src.version_info import get_version_info;print('ok' if str(get_version_info().get('commit','')).lower()==os.environ.get('ODYSSEUS_GIT_SHORT_COMMIT','').lower() else 'bad')"
_DELIVERY_PROGRAM = "import os;print('enabled' if os.environ.get('ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED','false')=='true' else 'disabled')"


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID or value.get("status") not in {"ok", "observed", "blocked"}:
        return False
    if any(type(value.get(key)) is not bool for key in _PROOFS | _VISIBILITY):
        return False
    if any(value[key] is not False for key in _VISIBILITY) or type(value.get("evidence_sha256")) is not str or _HEX64.fullmatch(value["evidence_sha256"]) is None or value["evidence_sha256"] != _digest(value):
        return False
    return (value["status"] == "ok") == all(value[key] for key in _PROOFS)


@dataclass(frozen=True, slots=True)
class ReadbackExpectation:
    revision: str
    manifest_sha256: str
    delivery_enabled: bool
    def valid(self) -> bool:
        return bool(_HEX40.fullmatch(self.revision) and _HEX64.fullmatch(self.manifest_sha256) and type(self.delivery_enabled) is bool)


@dataclass(frozen=True, slots=True)
class RuntimeBaseline:
    revision: str
    manifest_sha256: str
    dependency_digests: tuple[str, ...]
    mounts_intact: bool
    delivery_disabled: bool


def _run(runner: Callable[..., Any], command: tuple[str, ...], *, env: Mapping[str, str] | None = None) -> str | None:
    try:
        result = runner(list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, check=False, shell=False, env=dict(_MINIMAL_ENV if env is None else env))
    except Exception:
        return None
    output = getattr(result, "stdout", None)
    return output if getattr(result, "returncode", None) == 0 and type(output) is str and len(output) <= 256 else None


def _dependency_digests(*, runner: Callable[..., Any]) -> tuple[str, ...] | None:
    values = []
    for name in _DEPENDENCIES:
        output = _run(runner, ("podman", "inspect", "--format", "{{.Id}} {{.State.Running}}", name))
        parts = output.strip().split() if output is not None else []
        if len(parts) != 2 or _ID.fullmatch(parts[0]) is None or parts[1] != "true":
            return None
        values.append(hashlib.sha256((name + ":" + " ".join(parts)).encode()).hexdigest())
    return tuple(values)


def capture_runtime_baseline(*, runner: Callable[..., Any] = subprocess.run) -> RuntimeBaseline | None:
    """Capture fixed digests/booleans only while the activation lock is held."""
    revision = _run(runner, ("git", "-C", TARGET_ROOT, "rev-parse", "HEAD"))
    running = _run(runner, ("podman", "inspect", "--format", "{{.State.Running}}", APP_CONTAINER))
    mounts = _run(runner, ("podman", "inspect", "--format", "{{range .Mounts}}{{.Source}}:{{.Destination}};{{end}}", APP_CONTAINER))
    dependencies = _dependency_digests(runner=runner)
    delivery = _run(runner, ("podman", "exec", APP_CONTAINER, "python", "-I", "-c", _DELIVERY_PROGRAM))
    if revision is None or _HEX40.fullmatch(revision.strip()) is None or running != "true\n" or mounts is None or dependencies is None or delivery != "disabled\n":
        return None
    # The manifest helper cannot expose its bytes; retrieve only its digest via the trusted app program.
    digest_program = "import sys,hashlib;sys.path.insert(0,'/app');from src.constants import RELEASE_MANIFEST_FILE;print(hashlib.sha256(open(RELEASE_MANIFEST_FILE,'rb').read()).hexdigest())"
    manifest_digest = _run(runner, ("podman", "exec", APP_CONTAINER, "python", "-I", "-c", digest_program))
    mount_set = {item for item in mounts.strip().split(";") if item}
    if manifest_digest is None or _HEX64.fullmatch(manifest_digest.strip()) is None or not _EXPECTED_MOUNTS.issubset(mount_set):
        return None
    return RuntimeBaseline(revision.strip(), manifest_digest.strip(), dependencies, True, True)


def _ready(runner: Callable[..., Any], url: str, sleeper: Callable[[float], None]) -> bool:
    for attempt in range(20):
        if _run(runner, ("/usr/bin/curl", "--fail", "--silent", "--show-error", "--output", "/dev/null", "--max-time", "3", url)) is not None:
            return True
        if attempt < 19:
            sleeper(2)
    return False


def collect_host_readback(expectation: ReadbackExpectation, baseline: RuntimeBaseline, *, runner: Callable[..., Any] = subprocess.run, sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    flags = {key: False for key in _PROOFS | _VISIBILITY}
    if not isinstance(expectation, ReadbackExpectation) or not expectation.valid() or not isinstance(baseline, RuntimeBaseline):
        payload = {"schema_id": SCHEMA_ID, "status": "blocked", **flags}; payload["evidence_sha256"] = _digest(payload); return payload
    env = {**_MINIMAL_ENV, "ODYSSEUS_RELEASE_REVISION": expectation.revision, "ODYSSEUS_GIT_SHORT_COMMIT": expectation.revision[:8]}
    revision = _run(runner, ("git", "-C", TARGET_ROOT, "rev-parse", "HEAD")) == expectation.revision + "\n"
    manifest = _run(runner, ("podman", "exec", APP_CONTAINER, "python", "-I", "-c", _MANIFEST_PROGRAM, expectation.manifest_sha256), env=env) == "ok\n"
    version = _run(runner, ("podman", "exec", APP_CONTAINER, "python", "-I", "-c", _VERSION_PROGRAM), env=env) == "ok\n"
    mounts = _run(runner, ("podman", "inspect", "--format", "{{range .Mounts}}{{.Source}}:{{.Destination}};{{end}}", APP_CONTAINER))
    delivery = _run(runner, ("podman", "exec", APP_CONTAINER, "python", "-I", "-c", _DELIVERY_PROGRAM)) == ("enabled\n" if expectation.delivery_enabled else "disabled\n")
    flags.update({"revision_matches": revision and version, "manifest_matches": manifest, "app_healthy": _ready(runner, "http://127.0.0.1:7000/api/health", sleeper), "chroma_healthy": _ready(runner, "http://127.0.0.1:8100/api/v2/heartbeat", sleeper), "dependencies_unchanged": _dependency_digests(runner=runner) == baseline.dependency_digests, "mounts_intact": mounts is not None and _EXPECTED_MOUNTS.issubset({item for item in mounts.strip().split(';') if item}), "delivery_enabled": delivery})
    payload = {"schema_id": SCHEMA_ID, "status": "ok" if all(flags[key] for key in _PROOFS) else "observed", **flags}; payload["evidence_sha256"] = _digest(payload); return payload


def main(argv: list[str] | None = None) -> int:
    flags = {key: False for key in _PROOFS | _VISIBILITY}; payload = {"schema_id": SCHEMA_ID, "status": "blocked", **flags}; payload["evidence_sha256"] = _digest(payload)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))); return 1
