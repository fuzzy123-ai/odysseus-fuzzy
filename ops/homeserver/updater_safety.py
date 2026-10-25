#!/usr/bin/env python3
"""Updater preflight, stable readiness and host-owned outcome delivery.

No raw process output, provider response or exception is returned.
No automatic rollback: migration compatibility requires operator review.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.request

STAGES = (
    "initializing", "lock", "repository_access", "git_metadata", "git_fetch",
    "git_resolution", "fast_forward_check", "worktree_check",
    "backup_privilege_check", "snapshot", "git_pull", "version_metadata",
    "release_manifest", "rollback_image_preservation", "app_image_build",
    "candidate_configuration", "app_container_switch", "image_prune",
    "service_readiness", "version_verification", "runtime_stability",
    "tool_capability_refresh",
)
FLAGS = (
    "ODYSSEUS_TRANSCRIPTION_ENABLED", "ODYSSEUS_TRANSCRIPTION_LOCAL_ONLY",
    "ODYSSEUS_TRANSCRIPTION_RECORDING_AUTHORIZED",
)
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}
MAX_BYTES = 65536
CONTAINER = "odysseus_odysseus_1"


def configuration_check(values, *, dependency_check=None):
    """Mirror the actual startup validators; never normalize invalid input."""
    for key in FLAGS:
        value = values.get(key)
        if value is not None and str(value).strip().lower() not in TRUE | FALSE:
            return {"ok": False, "reason": "invalid_boolean", "setting": key}
    try:
        from src.transcription_runtime import TranscriptionRuntimeConfig
        config = TranscriptionRuntimeConfig.from_mapping(values)
    except Exception:
        return {"ok": False, "reason": "invalid_transcription_configuration"}
    legacy = []
    for key, default in (
        ("ODYSSEUS_LEGACY_STT_ENABLED", "true"),
        ("TELEGRAM_STT_ENABLED", "false"),
        ("TELEGRAM_VOICE_STT_ENABLED", "false"),
    ):
        value = str(values.get(key, default)).strip().lower()
        if value not in {"true", "false"}:
            return {"ok": False, "reason": "invalid_legacy_boolean", "setting": key}
        legacy.append(value == "true")
    if config.enabled and any(legacy):
        return {"ok": False, "reason": "transcription_legacy_conflict"}
    if config.enabled:
        if not values.get("ODYSSEUS_TRANSCRIPTION_MODEL_DIR"):
            return {"ok": False, "reason": "model_directory_missing"}
        try:
            (dependency_check or transcription_dependencies)(values)
        except Exception:
            return {"ok": False, "reason": "transcription_dependencies_unavailable"}
    return {"ok": True, "reason": "configuration_valid"}


def transcription_dependencies(values):
    # Reuse startup validation without constructing a store, downloading a model,
    # invoking ASR or sending a model request.
    from importlib.metadata import version
    from services.transcription.faster_whisper_adapter import (
        FASTER_WHISPER_VERSION, LocalFasterWhisperAdapter,
    )
    from src.maintenance_model_policy import MaintenanceModelProfile
    from src.transcription_local_model import Gemma3LocalReviewTransport
    if version("faster-whisper") != FASTER_WHISPER_VERSION:
        raise ValueError("unsupported_stt_package")
    LocalFasterWhisperAdapter(values["ODYSSEUS_TRANSCRIPTION_MODEL_DIR"])
    Gemma3LocalReviewTransport(
        values.get("OLLAMA_BASE_URL", "http://ollama:11434"),
        MaintenanceModelProfile.create(runtime_enabled=True),
    )


def _run(argv):
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=12, check=False,
        )
        if result.returncode or len(result.stdout) > MAX_BYTES:
            return None
        return result.stdout
    except Exception:
        return None


_API_PROBE = r'''
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:7000/api/ready", timeout=3) as r:
        ready = json.load(r).get("ready") is True
    with urllib.request.urlopen("http://127.0.0.1:7000/api/version", timeout=3) as r:
        commit = json.load(r).get("commit")
    print(json.dumps({"ready": ready, "commit": commit}))
except Exception:
    print("{}")
'''


def runtime_sample():
    """Read narrow metadata and reduce API results inside this boundary."""
    try:
        metadata = _run([
            "podman", "inspect", "--format",
            '{"id":{{json .Id}},"started":{{json .State.StartedAt}},'
            '"running":{{.State.Running}},"restarts":{{.RestartCount}}}', CONTAINER,
        ])
        api = _run(["podman", "exec", CONTAINER, "python3", "-I", "-c", _API_PROBE])
        state, status = json.loads(metadata or "{}"), json.loads(api or "{}")
        identity = state.get("id")
        commit = status.get("commit")
        if (
            not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or not isinstance(state.get("started"), str)
            or type(state.get("restarts")) is not int
            or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", commit)
        ):
            return None
        return {
            "identity": (identity, state["started"]),
            "healthy": state.get("running") is True and status.get("ready") is True,
            "restarts": state["restarts"], "commit": commit,
        }
    except Exception:
        return None


def stable_runtime(expected, *, sample=runtime_sample, clock=time.monotonic,
                   sleep=time.sleep, duration=30, interval=5):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        return {"ok": False, "reason": "invalid_expected_revision"}
    started = clock()
    identity = None
    while True:
        current = sample()
        if not current or not current["healthy"]:
            return {"ok": False, "reason": "runtime_not_ready"}
        if current["restarts"] != 0:
            return {"ok": False, "reason": "runtime_restarted"}
        if not expected.startswith(current["commit"]):
            return {"ok": False, "reason": "revision_mismatch"}
        if identity is not None and current["identity"] != identity:
            return {"ok": False, "reason": "runtime_replaced"}
        identity = current["identity"]
        if clock() - started >= duration:
            return {"ok": True, "reason": "stable_runtime"}
        sleep(interval)


def notification_settings(path):
    """Read fixed existing notification keys only, without shell evaluation."""
    wanted = {
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_NOTIFICATION_CHAT_ID",
        "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_CHAT_ID",
        "TELEGRAM_AGENT_REPLY_ENABLED", "ODYSSEUS_UPDATE_NOTIFICATIONS_ENABLED",
    }
    meta = path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_BYTES:
        raise ValueError("unsafe_config")
    if os.name == "posix" and (meta.st_uid != os.getuid() or meta.st_mode & 0o077):
        raise ValueError("unsafe_config")
    values = {}
    # Not shell-sourced: quoted literals supported; interpolation is rejected.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if key not in wanted:
            continue
        if key in values:
            raise ValueError("ambiguous_config")
        words = shlex.split(raw, comments=True, posix=True)
        if len(words) > 1 or (words and ("$" in words[0] or "\n" in words[0])):
            raise ValueError("invalid_config")
        values[key] = words[0] if words else ""
    return values


def notify(result, stage, revision, *, settings, send=None):
    """Host-side send independent of the app; disabled until explicitly enabled."""
    if result not in {"completed", "failed", "unchanged"} or stage not in STAGES:
        return {"ok": False, "reason": "invalid_event"}
    if revision and not re.fullmatch(r"[0-9a-f]{40}", revision):
        return {"ok": False, "reason": "invalid_revision"}
    if result == "unchanged":
        return {"ok": True, "reason": "unchanged_silent"}
    if str(settings.get("ODYSSEUS_UPDATE_NOTIFICATIONS_ENABLED", "")).lower() != "true":
        return {"ok": False, "reason": "notification_disabled"}
    if str(settings.get("TELEGRAM_AGENT_REPLY_ENABLED", "")).lower() not in TRUE:
        return {"ok": False, "reason": "reply_gate_closed"}
    token = settings.get("TELEGRAM_BOT_TOKEN", "")
    target = (
        settings.get("TELEGRAM_NOTIFICATION_CHAT_ID")
        or settings.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",", 1)[0].strip()
        or settings.get("TELEGRAM_CHAT_ID", "")
    )
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?[0-9]+", target):
        return {"ok": False, "reason": "notification_not_configured"}
    title = "Odysseus-Update erfolgreich." if result == "completed" else "Odysseus-Update fehlgeschlagen."
    message = title + "\nPhase: " + stage
    if revision:
        message += "\nRevision: " + revision[:12]
    if result == "failed":
        message += "\nKein automatischer Rollback. Zustand vor erneutem Update pruefen."
    try:
        delivered = (send or _telegram_send)(token, target, message)
    except Exception:
        delivered = False
    return {"ok": delivered is True, "reason": "delivered" if delivered is True else "delivery_failed"}


def _telegram_send(token, target, message):
    # Fixed Telegram endpoint, no redirect-following to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps({"chat_id": target, "text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            return False
        payload = json.loads(body)
        return response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configuration", "stable", "notify"])
    parser.add_argument("--revision", default="")
    parser.add_argument("--result", choices=["completed", "failed", "unchanged"])
    parser.add_argument("--stage", choices=STAGES, default="initializing")
    args = parser.parse_args()
    try:
        if args.action == "configuration":
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from dotenv import load_dotenv
            load_dotenv(encoding="utf-8-sig")
            # All input stays internal. Report fixed reason/setting identifiers only.
            keys = FLAGS + (
                "ODYSSEUS_TRANSCRIPTION_MODEL_DIR", "OLLAMA_BASE_URL",
                "ODYSSEUS_TRANSCRIPTION_MAX_BYTES", "ODYSSEUS_TRANSCRIPTION_MAX_CHUNK_BYTES",
                "ODYSSEUS_TRANSCRIPTION_RESERVATION_TTL_SECONDS",
                "ODYSSEUS_TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS",
                "ODYSSEUS_TRANSCRIPTION_BRIDGE_DEPTH", "ODYSSEUS_TRANSCRIPTION_WORKER_POLL_SECONDS",
                "ODYSSEUS_TRANSCRIPTION_SHUTDOWN_TIMEOUT_SECONDS",
                "ODYSSEUS_TRANSCRIPTION_RETENTION_DAYS",
                "ODYSSEUS_LEGACY_STT_ENABLED", "TELEGRAM_STT_ENABLED", "TELEGRAM_VOICE_STT_ENABLED",
            )
            report = configuration_check({key: os.environ[key] for key in keys if key in os.environ})
        elif args.action == "stable":
            report = stable_runtime(args.revision)
        else:
            report = notify(args.result, args.stage, args.revision,
                            settings=notification_settings(Path("/opt/odysseus/.env")))
    except Exception:
        report = {"ok": False, "reason": "updater_safety_failed"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
