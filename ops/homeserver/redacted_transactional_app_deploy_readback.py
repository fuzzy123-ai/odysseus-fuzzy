#!/usr/bin/env python3
"""Independent, fixed-key readback predicate for transactional app deploys."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

SCHEMA_ID = "odysseus.redacted_transactional_app_deploy_readback.v1"
_HEX40, _HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")
_VISIBILITY = frozenset({"raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible"})
_KEYS = frozenset({"schema_id", "status", "revision_matches", "manifest_matches", "app_healthy", "chroma_healthy", "dependencies_unchanged", "delivery_disabled", *_VISIBILITY, "evidence_sha256"})

def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _KEYS or payload.get("schema_id") != SCHEMA_ID or payload.get("status") not in {"ok", "observed", "blocked"}: return False
    flags = _KEYS - {"schema_id", "status", "evidence_sha256"}
    if any(type(payload.get(key)) is not bool for key in flags): return False
    proof={"revision_matches", "manifest_matches", "app_healthy", "chroma_healthy", "dependencies_unchanged", "delivery_disabled"}
    if payload["status"] == "ok" and not all(payload[k] for k in proof): return False
    if payload["status"] == "blocked" and any(payload[k] for k in proof): return False
    if payload["status"] == "observed" and all(payload[k] for k in proof): return False
    return all(payload[k] is False for k in _VISIBILITY) and type(payload.get("evidence_sha256")) is str and _HEX64.fullmatch(payload["evidence_sha256"]) is not None and payload["evidence_sha256"] == _digest(payload)

@dataclass(frozen=True, slots=True)
class ReadbackExpectation:
    revision: str
    manifest_sha256: str
    def valid(self) -> bool: return bool(_HEX40.fullmatch(self.revision) and _HEX64.fullmatch(self.manifest_sha256))

@dataclass(frozen=True, slots=True)
class RuntimeBaseline:
    revision: str
    manifest_sha256: str
    image_id: str
    dependency_digests: tuple[str, ...]
    expected_mounts: bool

Probe = Callable[[ReadbackExpectation], Mapping[str, Any]]
TARGET_ROOT = "/opt/odysseus"
APP_CONTAINER = "odysseus_odysseus_1"
CHROMA_CONTAINER = "odysseus_chromadb_1"
_CONTAINERS = (CHROMA_CONTAINER, "odysseus_searxng_1", "odysseus_ollama_1", "odysseus_ntfy_1")
_MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME":"/home/homebase", "USER":"homebase", "LOGNAME":"homebase", "XDG_RUNTIME_DIR":f"/run/user/{getattr(os,'getuid',lambda:1000)()}"}
_ID = re.compile(r"^[0-9a-f]{12,64}$")
_MANIFEST_PROGRAM = "import sys; sys.path.insert(0,'/app'); import hashlib; from src.constants import RELEASE_MANIFEST_FILE; from src.release_manifest import read_release_manifest; d,s=read_release_manifest(RELEASE_MANIFEST_FILE,expected_revision=__import__('os').environ.get('ODYSSEUS_RELEASE_REVISION')); print('ok' if s=='ready' and hashlib.sha256(open(RELEASE_MANIFEST_FILE,'rb').read()).hexdigest()==__import__('sys').argv[1] else 'bad')"
_VERSION_PROGRAM = "import sys; sys.path.insert(0,'/app'); import os; from src.version_info import get_version_info; print('ok' if str(get_version_info().get('commit','')).lower()==os.environ.get('ODYSSEUS_GIT_SHORT_COMMIT','').lower() else 'bad')"
_DELIVERY_PROGRAM = "import os; print('disabled' if os.environ.get('ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED','false').lower() in ('','0','false','no','off') else 'enabled')"
_BASELINE_PROGRAM = "import hashlib,sys; sys.path.insert(0,'/app'); from src.constants import RELEASE_MANIFEST_FILE; from src.release_manifest import read_release_manifest; from src.version_info import get_version_info; d,s=read_release_manifest(RELEASE_MANIFEST_FILE,expected_revision=sys.argv[1]); print(hashlib.sha256(open(RELEASE_MANIFEST_FILE,'rb').read()).hexdigest() if s=='ready' and str(get_version_info().get('commit','')).lower()==sys.argv[1][:8] else 'bad')"

def _run(runner: Callable[..., Any], command: tuple[str, ...]) -> str | None:
    try:
        result=runner(list(command),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=10,check=False,shell=False,env=dict(_MINIMAL_ENV))
    except Exception: return None
    output=getattr(result,'stdout',None)
    return output if getattr(result,'returncode',None)==0 and type(output) is str and len(output)<=256 else None

def capture_dependency_baseline(*, runner: Callable[..., Any]=subprocess.run) -> tuple[str, ...] | None:
    """Capture only allowlisted dependency identities/statuses; never return raw output."""
    values=[]
    for name in _CONTAINERS:
        output=_run(runner,("podman","inspect","--format","{{.Id}} {{.State.Running}}",name))
        if output is None: return None
        parts=output.strip().split()
        if len(parts)!=2 or not _ID.fullmatch(parts[0]) or parts[1] != "true": return None
        values.append(hashlib.sha256((name+":"+" ".join(parts)).encode()).hexdigest())
    return tuple(values)

def capture_runtime_baseline(*, runner: Callable[..., Any]=subprocess.run) -> RuntimeBaseline | None:
    """Fixed redacted old-runtime snapshot, called only while the deploy lock is held."""
    revision=_run(runner,("git","-C",TARGET_ROOT,"rev-parse","HEAD"))
    manifest=_run(runner,("podman","inspect","--format","{{.State.Running}}",APP_CONTAINER))
    image=_run(runner,("podman","inspect","--format","{{.Image}}",APP_CONTAINER))
    deps=capture_dependency_baseline(runner=runner)
    mounts=_run(runner,("podman","inspect","--format","{{range .Mounts}}{{.Source}}:{{.Destination}};{{end}}",APP_CONTAINER))
    if revision is None or not _HEX40.fullmatch(revision.strip()) or manifest != "true\n" or image is None or not _HEX64.fullmatch(image.strip().removeprefix("sha256:")) or deps is None or mounts is None:
        return None
    # The source values are never returned: only exact expected mount identity.
    expected={"/opt/odysseus/data:/app/data", "/opt/odysseus/logs:/app/logs", "/opt/odysseus/data/universal-inbox:/app/universal-inbox"}
    mount_set={part for part in mounts.strip().split(';') if part}
    digest=_run(runner,("podman","exec",APP_CONTAINER,"python","-I","-c",_BASELINE_PROGRAM,revision.strip()))
    if digest is None or not _HEX64.fullmatch(digest.strip()): return None
    mounts_valid = expected.issubset(mount_set)
    return RuntimeBaseline(revision.strip(),digest.strip(),image.strip(),deps,True) if mounts_valid else None

def _ready(runner: Callable[..., Any], url: str, *, sleeper: Callable[[float], None] = time.sleep) -> bool:
    # Bounded, output-discarding readiness polling: bodies never enter an envelope.
    for attempt in range(20):
        if _run(runner,("/usr/bin/curl","--fail","--silent","--show-error","--output","/dev/null","--max-time","3",url)) is not None:
            return True
        if attempt < 19: sleeper(2)
    return False

def collect_host_readback(expectation: ReadbackExpectation, baseline: tuple[str, ...], *, checkout_required: bool = True, runner: Callable[..., Any]=subprocess.run, sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Concrete fixed host-local readback with no caller command/path inputs."""
    if type(baseline) is not tuple or len(baseline)!=len(_CONTAINERS) or any(type(item) is not str or not _HEX64.fullmatch(item) for item in baseline):
        return collect_readback(ReadbackExpectation("", ""), probe=lambda _e:{})
    def probe(expected: ReadbackExpectation) -> Mapping[str, Any]:
        revision=(not checkout_required) or _run(runner,("git","-C",TARGET_ROOT,"rev-parse","HEAD"))==expected.revision+"\n"
        manifest=_run(runner,("podman","exec",APP_CONTAINER,"python","-I","-c",_MANIFEST_PROGRAM,expected.manifest_sha256))=="ok\n"
        version=_run(runner,("podman","exec",APP_CONTAINER,"python","-I","-c",_VERSION_PROGRAM))=="ok\n"
        app=_ready(runner,"http://127.0.0.1:7000/api/health",sleeper=sleeper)
        chroma=_ready(runner,"http://127.0.0.1:8100/api/v2/heartbeat",sleeper=sleeper)
        dependencies=capture_dependency_baseline(runner=runner)==baseline
        delivery=_run(runner,("podman","exec",APP_CONTAINER,"python","-I","-c",_DELIVERY_PROGRAM))=="disabled\n"
        return {"revision_matches":revision and version,"manifest_matches":manifest,"app_healthy":app,"chroma_healthy":chroma,"dependencies_unchanged":dependencies,"delivery_disabled":delivery}
    return collect_readback(expectation,probe=probe)

def collect_readback(expectation: ReadbackExpectation, *, probe: Probe) -> dict[str, Any]:
    flags = {key: False for key in _KEYS - {"schema_id", "status", "evidence_sha256"}}
    if not isinstance(expectation, ReadbackExpectation) or not expectation.valid(): status = "blocked"
    else:
        try: observed = probe(expectation)
        except Exception: observed = {}
        allowed = {"revision_matches", "manifest_matches", "app_healthy", "chroma_healthy", "dependencies_unchanged", "delivery_disabled"}
        if type(observed) is dict and set(observed) == allowed and all(type(observed[k]) is bool for k in allowed): flags.update(observed)
        status = "ok" if all(flags[k] for k in allowed) else "observed"
    payload = {"schema_id": SCHEMA_ID, "status": status, **flags}; payload["evidence_sha256"] = _digest(payload)
    return payload

def main(argv: list[str] | None = None) -> int:
    # Source is transportable without accepting packet, path, or command arguments.
    payload = collect_readback(ReadbackExpectation("", ""), probe=lambda _expectation: {}) if not (sys.argv[1:] if argv is None else argv) else collect_readback(ReadbackExpectation("", ""), probe=lambda _expectation: {})
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))); return 1
if __name__ == "__main__": raise SystemExit(main())
