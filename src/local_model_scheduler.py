"""Process-local scheduling for CPU-bound local model calls."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import time
import threading
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from src.maintenance_model_policy import (
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    MaintenanceModelRole,
    evaluate_maintenance_model_eligibility,
)


_MAINTENANCE_HINTS = (
    "consolidate",
    "maintenance",
    "tidy",
    "scheduled_task",
    "email_signature_extract",
    "calendar_classify_events",
)
_FOREGROUND_HINTS = (
    "ask_user",
    "clarification",
    "clarifying",
    "document_review",
    "coding_task",
)
_MARKER_SCHEMA = "odysseus.local_model_foreground_marker.v1"
_DEFAULT_MARKER_PATH = "/tmp/odysseus-local-model-foreground.json"
_DEFAULT_MARKER_TTL_SECONDS = 600.0
# A nested model invocation in one logical request would wait for the slot it
# already owns.  Do not make that an unbounded self-deadlock: nested admission
# is deliberately rejected before it allocates a reservation or touches a
# marker.  A child asyncio task inherits context variables, so the value binds
# the owning task rather than acting as a plain boolean.  A copied context in
# ``asyncio.to_thread`` remains nested and is rejected because no asyncio task
# is running in that worker thread.
_ACTIVE_LOCAL_MODEL_SLOT: ContextVar[tuple[str, object] | None] = ContextVar(
    "odysseus_active_local_model_slot", default=None
)


def _local_model_execution_owner() -> tuple[str, object]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        return ("async_task", task)
    return ("thread", threading.get_ident())


def _local_model_slot_is_reentrant() -> bool:
    active_owner = _ACTIVE_LOCAL_MODEL_SLOT.get()
    if active_owner is None:
        return False
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is None:
        # Pure sync nesting and asyncio.to_thread both inherit an active
        # logical slot but have no independently schedulable asyncio task.
        return True
    return active_owner == ("async_task", task)


@dataclass(frozen=True)
class LocalModelLease:
    kind: str
    url: str
    model: str


@dataclass(frozen=True)
class MaintenanceYieldResult:
    yielded: bool
    sleep_count: int
    slept_seconds: float
    reason: str

    def to_dict(self) -> dict[str, int | float | bool | str]:
        return {
            "yielded": self.yielded,
            "sleep_count": self.sleep_count,
            "slept_seconds": round(self.slept_seconds, 6),
            "reason": self.reason,
        }


class _LocalModelAcquireCancelled(RuntimeError):
    pass


class LocalModelAdmissionError(RuntimeError):
    """Content-free rejection at the shared local heavy-resource boundary."""


class LocalModelRequestGate:
    """A tiny priority gate for local Ollama requests.

    Foreground requests are allowed before queued maintenance requests. A
    running generation cannot be preempted, so callers should acquire this gate
    at each local-model boundary rather than around broad maintenance loops.
    """

    def __init__(self, *, max_concurrency: int = 1) -> None:
        self.max_concurrency = max(1, int(max_concurrency or 1))
        self._condition = threading.Condition()
        self._active = 0
        self._active_foreground = 0
        self._waiting_foreground = 0
        self._active_target_model = 0
        self._waiting_target_model = 0

    def acquire(
        self,
        *,
        kind: str,
        url: str,
        model: str,
        cancel_event: threading.Event | None = None,
    ) -> LocalModelLease:
        normalized_kind = "maintenance" if kind == "maintenance" else "foreground"
        target_model = model == DEFAULT_MAINTENANCE_MODEL
        with self._condition:
            if normalized_kind == "foreground":
                self._waiting_foreground += 1
                _refresh_foreground_marker(model=model, reason="waiting")
            if target_model:
                self._waiting_target_model += 1
            try:
                while not self._can_enter(normalized_kind):
                    if cancel_event is not None and cancel_event.is_set():
                        raise _LocalModelAcquireCancelled("local model acquisition cancelled")
                    self._condition.wait(timeout=0.05 if cancel_event is not None else None)
                if cancel_event is not None and cancel_event.is_set():
                    raise _LocalModelAcquireCancelled("local model acquisition cancelled")
                self._active += 1
                if target_model:
                    self._active_target_model += 1
                if normalized_kind == "foreground":
                    self._active_foreground += 1
                    _refresh_foreground_marker(model=model, reason="active")
            finally:
                if target_model:
                    self._waiting_target_model = max(0, self._waiting_target_model - 1)
                if normalized_kind == "foreground":
                    self._waiting_foreground -= 1
                    self._sync_foreground_marker_locked(model=model)
                    self._condition.notify_all()
        return LocalModelLease(kind=normalized_kind, url=url, model=model)

    def release(self, lease: LocalModelLease) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            if lease.model == DEFAULT_MAINTENANCE_MODEL:
                self._active_target_model = max(0, self._active_target_model - 1)
            if lease.kind == "foreground":
                self._active_foreground = max(0, self._active_foreground - 1)
                self._sync_foreground_marker_locked(model=lease.model)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "active": self._active,
                "active_foreground": self._active_foreground,
                "waiting_foreground": self._waiting_foreground,
                "active_target_model": self._active_target_model,
                "waiting_target_model": self._waiting_target_model,
                "max_concurrency": self.max_concurrency,
            }

    def _can_enter(self, kind: str) -> bool:
        if self._active >= self.max_concurrency:
            return False
        if kind == "maintenance" and self._waiting_foreground > 0:
            return False
        return True

    def _sync_foreground_marker_locked(self, *, model: str) -> None:
        if self._active_foreground > 0 or self._waiting_foreground > 0:
            _refresh_foreground_marker(model=model, reason="active")
        else:
            _clear_foreground_marker(activity_scope="foreground")


@dataclass
class _RegistryEntry:
    gate: LocalModelRequestGate
    last_used: float
    reservations: int = 0


@dataclass(frozen=True)
class _RegistryLease:
    key: tuple[str, str]
    entry: _RegistryEntry
    lease: LocalModelLease


class LocalModelAdmissionRegistry:
    """Bounded per-endpoint/model admission gates for local model calls."""

    def __init__(
        self,
        *,
        max_entries: int = 64,
        idle_ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_entries, bool) or int(max_entries) <= 0:
            raise ValueError("max_entries must be > 0")
        if float(idle_ttl_seconds) < 0:
            raise ValueError("idle_ttl_seconds must be >= 0")
        self.max_entries = int(max_entries)
        self.idle_ttl_seconds = float(idle_ttl_seconds)
        self._clock = clock
        self._condition = threading.Condition()
        self._entries: dict[tuple[str, str], _RegistryEntry] = {}
        self._evictions_total = 0
        self._capacity_waiters = 0

    def acquire(
        self,
        *,
        url: str,
        model: str,
        kind: str = "maintenance",
        cancel_event: threading.Event | None = None,
    ) -> _RegistryLease:
        normalized_kind = "maintenance" if kind == "maintenance" else "foreground"
        key = canonical_local_model_key(url, model)
        capacity_waiting = False
        try:
            with self._condition:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _LocalModelAcquireCancelled("registry acquisition cancelled")
                    now = self._clock()
                    self._evict_expired_locked(now)
                    entry = self._entries.get(key)
                    if entry is None:
                        if len(self._entries) >= self.max_entries and not self._evict_oldest_idle_locked():
                            if not capacity_waiting:
                                self._capacity_waiters += 1
                                capacity_waiting = True
                                self._sync_maintenance_marker_locked()
                            self._condition.wait(timeout=0.05 if cancel_event is not None else None)
                            continue
                        entry = _RegistryEntry(
                            gate=LocalModelRequestGate(max_concurrency=1),
                            last_used=now,
                        )
                        self._entries[key] = entry
                    if capacity_waiting:
                        self._capacity_waiters = max(0, self._capacity_waiters - 1)
                        capacity_waiting = False
                    entry.reservations += 1
                    entry.last_used = now
                    self._sync_maintenance_marker_locked()
                    break
        except BaseException:
            if capacity_waiting:
                with self._condition:
                    self._capacity_waiters = max(0, self._capacity_waiters - 1)
                    self._sync_maintenance_marker_locked()
                    self._condition.notify_all()
            raise
        try:
            lease = entry.gate.acquire(
                kind=normalized_kind,
                url=url,
                model=model,
                cancel_event=cancel_event,
            )
        except BaseException:
            self._release_reservation(key, entry)
            raise
        with self._condition:
            self._sync_maintenance_marker_locked()
        return _RegistryLease(key=key, entry=entry, lease=lease)

    def release(self, handle: _RegistryLease) -> None:
        handle.entry.gate.release(handle.lease)
        self._release_reservation(handle.key, handle.entry)

    def evict_idle(self) -> int:
        with self._condition:
            evicted = self._evict_expired_locked(self._clock())
            if evicted:
                self._condition.notify_all()
            return evicted

    def snapshot(self) -> dict[str, int | str]:
        with self._condition:
            self._evict_expired_locked(self._clock())
            active_leases = 0
            waiting_leases = self._capacity_waiters
            active_keys = 0
            for entry in self._entries.values():
                gate = entry.gate.snapshot()
                active = int(gate.get("active", 0))
                active_leases += active
                waiting_leases += max(0, entry.reservations - active)
                if entry.reservations:
                    active_keys += 1
            return {
                "schema": "odysseus.local_model_admission_registry.v1",
                "entry_count": len(self._entries),
                "active_key_count": active_keys,
                "idle_key_count": len(self._entries) - active_keys,
                "active_lease_count": active_leases,
                "waiting_lease_count": waiting_leases,
                "max_entries": self.max_entries,
                "max_concurrency_per_key": 1,
                # Legacy readers use this scalar as the concurrency of one
                # admission lane. The registry itself remains parallel across
                # distinct canonical keys.
                "max_concurrency": 1,
                "evictions_total": self._evictions_total,
            }

    def _release_reservation(self, key: tuple[str, str], entry: _RegistryEntry) -> None:
        with self._condition:
            current = self._entries.get(key)
            if current is entry:
                entry.reservations = max(0, entry.reservations - 1)
                entry.last_used = self._clock()
                self._evict_expired_locked(entry.last_used)
                self._sync_maintenance_marker_locked()
            self._condition.notify_all()

    def _evict_expired_locked(self, now: float) -> int:
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.reservations == 0 and now - entry.last_used >= self.idle_ttl_seconds
        ]
        for key in expired:
            del self._entries[key]
        self._evictions_total += len(expired)
        return len(expired)

    def _evict_oldest_idle_locked(self) -> bool:
        idle = [
            (entry.last_used, key)
            for key, entry in self._entries.items()
            if entry.reservations == 0
        ]
        if not idle:
            return False
        _, oldest_key = min(idle)
        del self._entries[oldest_key]
        self._evictions_total += 1
        return True

    def _sync_maintenance_marker_locked(self) -> None:
        active = 0
        waiting = self._capacity_waiters
        active_foreground = 0
        waiting_foreground = 0
        for entry in self._entries.values():
            gate = entry.gate.snapshot()
            entry_active = int(gate.get("active", 0))
            active += entry_active
            waiting += max(0, entry.reservations - entry_active)
            active_foreground += int(gate.get("active_foreground", 0))
            waiting_foreground += int(gate.get("waiting_foreground", 0))
        if active or waiting:
            foreground = active_foreground > 0 or waiting_foreground > 0
            _refresh_foreground_marker(
                model="local-model" if foreground else DEFAULT_MAINTENANCE_MODEL,
                reason="active" if active else "waiting",
                activity_scope="foreground" if foreground else "maintenance",
                active_count=active,
                waiting_count=waiting,
            )
        else:
            _clear_foreground_marker(activity_scope="maintenance")


def _env_positive_int(name: str, fallback: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(fallback)) or str(fallback)))
    except ValueError:
        return fallback


def _env_nonnegative_float(name: str, fallback: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(fallback)) or str(fallback)))
    except ValueError:
        return fallback


_GLOBAL_GATE = LocalModelRequestGate(
    max_concurrency=_env_positive_int("ODYSSEUS_LOCAL_MODEL_MAX_CONCURRENCY", 1)
)
_GLOBAL_REGISTRY = LocalModelAdmissionRegistry(
    max_entries=_env_positive_int("ODYSSEUS_LOCAL_MODEL_REGISTRY_MAX_ENTRIES", 64),
    idle_ttl_seconds=_env_nonnegative_float("ODYSSEUS_LOCAL_MODEL_REGISTRY_IDLE_TTL_SECONDS", 600.0),
)


def reset_local_model_gate_for_tests(
    *,
    max_concurrency: int = 1,
    registry_max_entries: int = 64,
    registry_idle_ttl_seconds: float = 600.0,
    registry_clock: Callable[[], float] = time.monotonic,
) -> None:
    global _GLOBAL_GATE, _GLOBAL_REGISTRY
    _GLOBAL_GATE = LocalModelRequestGate(max_concurrency=max_concurrency)
    _GLOBAL_REGISTRY = LocalModelAdmissionRegistry(
        max_entries=registry_max_entries,
        idle_ttl_seconds=registry_idle_ttl_seconds,
        clock=registry_clock,
    )


def local_model_admission_registry_snapshot() -> dict[str, int | str]:
    return _GLOBAL_REGISTRY.snapshot()


def local_model_gate_snapshot() -> dict[str, int]:
    legacy = _GLOBAL_GATE.snapshot()
    registry = _GLOBAL_REGISTRY.snapshot()
    return {
        "active": int(legacy["active"]) + int(registry["active_lease_count"]),
        "active_foreground": int(legacy["active_foreground"]),
        "waiting_foreground": int(legacy["waiting_foreground"]),
        "max_concurrency": 1,
        "registry_entry_count": int(registry["entry_count"]),
        "registry_active_key_count": int(registry["active_key_count"]),
        "registry_waiting_lease_count": int(registry["waiting_lease_count"]),
        "registry_evictions_total": int(registry["evictions_total"]),
    }


def local_model_foreground_marker_path() -> Path:
    return Path(os.getenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", _DEFAULT_MARKER_PATH))


def read_local_model_foreground_marker(
    *,
    path: str | os.PathLike[str] | None = None,
    now: float | None = None,
) -> dict[str, object] | None:
    """Return the active foreground marker, or ``None`` for absent/stale markers."""

    marker_path = Path(path) if path is not None else local_model_foreground_marker_path()
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != _MARKER_SCHEMA:
        return None
    expires_at = _as_float(payload.get("expires_at_epoch"))
    current_time = time.time() if now is None else float(now)
    if expires_at is None or expires_at <= current_time:
        _clear_foreground_marker(path=marker_path)
        return None
    return payload


def is_local_model_foreground_active(*, path: str | os.PathLike[str] | None = None) -> bool:
    return read_local_model_foreground_marker(path=path) is not None


def is_local_model_maintenance_busy(*, path: str | os.PathLike[str] | None = None) -> bool:
    payload = read_local_model_foreground_marker(path=path)
    if payload is None:
        return False
    return (
        payload.get("model_scope") == "gemma3_4b"
        or payload.get("model") == DEFAULT_MAINTENANCE_MODEL
    ) and payload.get("reason") in {"active", "waiting"}


def wait_for_local_model_foreground_clear(
    *,
    path: str | os.PathLike[str] | None = None,
    timeout_seconds: float = 600.0,
    poll_seconds: float = 1.0,
) -> MaintenanceYieldResult:
    """Wait until the process-level foreground marker is absent or stale."""

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    sleep_for = max(0.01, float(poll_seconds))
    start = time.monotonic()
    sleep_count = 0
    while True:
        if not is_local_model_maintenance_busy(path=path):
            return MaintenanceYieldResult(sleep_count > 0, sleep_count, time.monotonic() - start, "clear")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return MaintenanceYieldResult(sleep_count > 0, sleep_count, time.monotonic() - start, "timeout")
        time.sleep(min(sleep_for, remaining))
        sleep_count += 1


def maintenance_cpu_checkpoint(
    *,
    gate: LocalModelRequestGate | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
    sleep_seconds: float = 0.025,
    max_pause_seconds: float = 0.25,
) -> MaintenanceYieldResult:
    """Let CPU-heavy maintenance yield while foreground local model work runs."""

    if os.getenv("ODYSSEUS_MAINTENANCE_CPU_YIELD", "1").strip().lower() in {"0", "false", "off", "no"}:
        return _finish_maintenance_yield(
            MaintenanceYieldResult(False, 0, 0.0, "disabled")
        )

    sleep_for = max(0.0, float(sleep_seconds))
    max_pause = max(0.0, float(max_pause_seconds))
    start = time.monotonic()
    sleep_count = 0
    while True:
        if gate is not None:
            snapshot = gate.snapshot()
            active = int(snapshot.get("active_target_model", 0))
            waiting = int(snapshot.get("waiting_target_model", 0))
        else:
            snapshot = (registry or _GLOBAL_REGISTRY).snapshot()
            active = int(snapshot.get("active_lease_count", 0))
            waiting = int(snapshot.get("waiting_lease_count", 0))
        local_model_busy = active > 0 or waiting > 0 or is_local_model_maintenance_busy()
        if not local_model_busy:
            return _finish_maintenance_yield(
                MaintenanceYieldResult(
                    sleep_count > 0,
                    sleep_count,
                    time.monotonic() - start,
                    "clear",
                )
            )
        elapsed = time.monotonic() - start
        if max_pause <= 0 or elapsed >= max_pause:
            return _finish_maintenance_yield(
                MaintenanceYieldResult(
                    sleep_count > 0,
                    sleep_count,
                    time.monotonic() - start,
                    "max_pause_reached",
                )
            )
        remaining = max(0.0, max_pause - elapsed)
        time.sleep(min(sleep_for, remaining))
        sleep_count += 1


def _local_model_queue_enabled() -> bool:
    return os.getenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def is_local_model_resource(url: str, *, provider: str = "") -> bool:
    if provider not in {"ollama", DEFAULT_MAINTENANCE_PROVIDER, "openai"}:
        return False
    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "").rstrip("/")
    local_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "ollama"}
    if provider == "openai":
        # _detect_provider deliberately labels local OpenAI-compatible servers
        # as ``openai``.  Only a bounded local /v1 endpoint may enter this
        # broker; public OpenAI-compatible providers remain a true bypass.
        return local_host and path.startswith("/v1")
    return (local_host or port == 11434) and (
        path == "" or path.startswith("/api") or path.startswith("/v1")
    )


def should_gate_local_model(url: str, *, provider: str = "") -> bool:
    return _local_model_queue_enabled() and is_local_model_resource(
        url, provider=provider
    )


def _local_model_admission_kind(
    url: str,
    model: str,
    *,
    provider: str,
    role: MaintenanceModelRole | None,
    fallback_requested: bool,
    truth_write_requested: bool,
) -> str | None:
    if not is_local_model_resource(url, provider=provider):
        return None
    if not _local_model_queue_enabled():
        if role is MaintenanceModelRole.MAINTENANCE:
            # Existing typed runtimes treat ``None`` as an explicit disabled
            # admission and fail before transport.
            return "disabled"
        raise LocalModelAdmissionError("local model admission unavailable")
    typed_request = (
        role is not None
        or fallback_requested is not False
        or truth_write_requested is not False
    )
    if typed_request:
        decision = evaluate_maintenance_model_eligibility(
            model_ref=model,
            provider=(
                DEFAULT_MAINTENANCE_PROVIDER if provider == "ollama" else provider
            ),
            role=role,
            fallback_requested=fallback_requested,
            truth_write_requested=truth_write_requested,
        )
        if not decision.eligible:
            model_token = model.strip().casefold() if isinstance(model, str) else ""
            if model == DEFAULT_MAINTENANCE_MODEL or model_token.startswith("gemma3"):
                raise LocalModelAdmissionError("local model admission rejected")
            return None
        kind = "maintenance"
    else:
        kind = "foreground"
    if _local_model_slot_is_reentrant():
        raise LocalModelAdmissionError("local model admission rejected")
    return kind


def canonical_local_model_key(url: str, model: str) -> tuple[str, str]:
    """Return the internal endpoint/model key without userinfo or request path."""

    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid local model endpoint") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("local model endpoint requires a host")
    scheme = (parsed.scheme or "http").lower()
    default_port = 443 if scheme == "https" else 80
    port_part = "" if port in {None, default_port} else f":{port}"
    host_label = f"[{host}]" if ":" in host else host
    return (f"{scheme}://{host_label}{port_part}", str(model))


def is_maintenance_model_eligible(
    url: str,
    model: str,
    *,
    provider: str,
    role: MaintenanceModelRole | None,
    fallback_requested: bool = False,
    truth_write_requested: bool = False,
) -> bool:
    """Return whether a request may enter the isolated maintenance queue."""

    if not should_gate_local_model(url, provider=provider):
        return False
    canonical_provider = DEFAULT_MAINTENANCE_PROVIDER if provider == "ollama" else provider
    return evaluate_maintenance_model_eligibility(
        model_ref=model,
        provider=canonical_provider,
        role=role,
        fallback_requested=fallback_requested,
        truth_write_requested=truth_write_requested,
    ).eligible


def classify_local_model_request(*, surface: str | None = None, prompt_type: str | None = None) -> str:
    haystack = f"{surface or ''} {prompt_type or ''}".lower()
    if any(hint in haystack for hint in _FOREGROUND_HINTS):
        return "foreground"
    if any(hint in haystack for hint in _MAINTENANCE_HINTS):
        return "maintenance"
    return "foreground"


def _marker_ttl_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER_TTL_SECONDS", str(_DEFAULT_MARKER_TTL_SECONDS))))
    except ValueError:
        return _DEFAULT_MARKER_TTL_SECONDS


def _refresh_foreground_marker(
    *,
    model: str,
    reason: str,
    path: str | os.PathLike[str] | None = None,
    activity_scope: str = "foreground",
    active_count: int = 0,
    waiting_count: int = 0,
) -> None:
    marker_path = Path(path) if path is not None else local_model_foreground_marker_path()
    now = time.time()
    safe_model = (
        DEFAULT_MAINTENANCE_MODEL
        if model == DEFAULT_MAINTENANCE_MODEL
        else "local-model"
    )
    payload = {
        "schema": _MARKER_SCHEMA,
        "pid": os.getpid(),
        "model": safe_model,
        "model_scope": "gemma3_4b" if safe_model == DEFAULT_MAINTENANCE_MODEL else "other",
        "activity_scope": "maintenance" if activity_scope == "maintenance" else "foreground",
        "state": "waiting" if reason == "waiting" else "active",
        "reason": str(reason or "foreground")[:40],
        "active_count": max(0, int(active_count)),
        "waiting_count": max(0, int(waiting_count)),
        "updated_at_epoch": round(now, 3),
        "expires_at_epoch": round(now + _marker_ttl_seconds(), 3),
    }
    tmp_path: Path | None = None
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = marker_path.with_name(
            f".{marker_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, marker_path)
    except OSError:
        pass
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _clear_foreground_marker(
    *,
    path: str | os.PathLike[str] | None = None,
    activity_scope: str | None = None,
) -> None:
    marker_path = Path(path) if path is not None else local_model_foreground_marker_path()
    if activity_scope is not None:
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            current_scope = str(payload.get("activity_scope") or "foreground")
            if current_scope != activity_scope:
                return
    try:
        marker_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _acquire_cancel_safe(
    acquire_func: Callable[[threading.Event], Any],
    release_func: Callable[[Any], None],
) -> Any:
    cancel_event = threading.Event()
    acquire_task = asyncio.create_task(asyncio.to_thread(acquire_func, cancel_event))
    try:
        return await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            acquired = await asyncio.shield(acquire_task)
        except _LocalModelAcquireCancelled:
            pass
        else:
            release_func(acquired)
        raise


def _record_gmi_metric(event: str, *, status: str, value: float | int = 1) -> None:
    """Best-effort closed metrics; instrumentation cannot break scheduling."""

    try:
        from src.observability_metrics import record_gmi_runtime_event

        record_gmi_runtime_event(event, status=status, value=value)
    except Exception:
        pass


def _record_maintenance_queue_depth(
    *,
    gate: LocalModelRequestGate | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
) -> None:
    try:
        if gate is not None:
            snapshot = gate.snapshot()
            depth = int(snapshot.get("active_target_model", 0)) + int(
                snapshot.get("waiting_target_model", 0)
            )
        else:
            snapshot = (registry or _GLOBAL_REGISTRY).snapshot()
            depth = int(snapshot.get("active_lease_count", 0)) + int(
                snapshot.get("waiting_lease_count", 0)
            )
        _record_gmi_metric("queue_depth", status="current", value=depth)
    except Exception:
        pass


def _finish_maintenance_yield(result: MaintenanceYieldResult) -> MaintenanceYieldResult:
    status = "disabled" if result.reason == "disabled" else (
        "yielded" if result.yielded else "continued"
    )
    _record_gmi_metric("yield", status=status)
    return result


@asynccontextmanager
async def local_model_async_slot(
    url: str,
    model: str,
    *,
    provider: str = "",
    surface: str | None = None,
    prompt_type: str | None = None,
    role: MaintenanceModelRole | None = None,
    fallback_requested: bool = False,
    truth_write_requested: bool = False,
    gate: LocalModelRequestGate | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
):
    kind = _local_model_admission_kind(
        url,
        model,
        provider=provider,
        role=role,
        fallback_requested=fallback_requested,
        truth_write_requested=truth_write_requested,
    )
    if kind is None:
        _record_gmi_metric("admission", status="bypassed")
        yield None
        return
    if kind == "disabled":
        _record_gmi_metric("admission", status="disabled")
        yield None
        return
    if gate is not None:
        if registry is not None:
            raise ValueError("gate and registry are mutually exclusive")
        if gate.max_concurrency != 1:
            raise ValueError("local model gate concurrency must be 1")
        wait_started = time.monotonic()
        try:
            lease = await _acquire_cancel_safe(
                lambda cancel: gate.acquire(
                    kind=kind,
                    url=url,
                    model=model,
                    cancel_event=cancel,
                ),
                gate.release,
            )
        except asyncio.CancelledError:
            _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
            _record_gmi_metric("cancellation", status="queue_wait")
            _record_maintenance_queue_depth(gate=gate)
            raise
        except Exception:
            _record_gmi_metric("admission", status="rejected")
            raise
        _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
        _record_gmi_metric("admission", status="admitted")
        _record_maintenance_queue_depth(gate=gate)
        runtime_started = time.monotonic()
        runtime_status = "completed"
        slot_token = _ACTIVE_LOCAL_MODEL_SLOT.set(_local_model_execution_owner())
        try:
            yield lease
        except asyncio.CancelledError:
            runtime_status = "cancelled"
            _record_gmi_metric("cancellation", status="runtime")
            raise
        finally:
            _ACTIVE_LOCAL_MODEL_SLOT.reset(slot_token)
            gate.release(lease)
            _record_gmi_metric("runtime", status=runtime_status, value=time.monotonic() - runtime_started)
            _record_maintenance_queue_depth(gate=gate)
        return
    selected_registry = registry or _GLOBAL_REGISTRY
    wait_started = time.monotonic()
    try:
        handle = await _acquire_cancel_safe(
            lambda cancel: selected_registry.acquire(
                url=url,
                model=model,
                kind=kind,
                cancel_event=cancel,
            ),
            selected_registry.release,
        )
    except asyncio.CancelledError:
        _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
        _record_gmi_metric("cancellation", status="queue_wait")
        _record_maintenance_queue_depth(registry=selected_registry)
        raise
    except Exception:
        _record_gmi_metric("admission", status="rejected")
        raise
    _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
    _record_gmi_metric("admission", status="admitted")
    _record_maintenance_queue_depth(registry=selected_registry)
    runtime_started = time.monotonic()
    runtime_status = "completed"
    slot_token = _ACTIVE_LOCAL_MODEL_SLOT.set(_local_model_execution_owner())
    try:
        yield handle.lease
    except asyncio.CancelledError:
        runtime_status = "cancelled"
        _record_gmi_metric("cancellation", status="runtime")
        raise
    finally:
        _ACTIVE_LOCAL_MODEL_SLOT.reset(slot_token)
        selected_registry.release(handle)
        _record_gmi_metric("runtime", status=runtime_status, value=time.monotonic() - runtime_started)
        _record_maintenance_queue_depth(registry=selected_registry)


@contextmanager
def local_model_sync_slot(
    url: str,
    model: str,
    *,
    provider: str = "",
    surface: str | None = None,
    prompt_type: str | None = None,
    role: MaintenanceModelRole | None = None,
    fallback_requested: bool = False,
    truth_write_requested: bool = False,
    gate: LocalModelRequestGate | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
) -> Iterator[LocalModelLease | None]:
    kind = _local_model_admission_kind(
        url,
        model,
        provider=provider,
        role=role,
        fallback_requested=fallback_requested,
        truth_write_requested=truth_write_requested,
    )
    if kind is None:
        _record_gmi_metric("admission", status="bypassed")
        yield None
        return
    if kind == "disabled":
        _record_gmi_metric("admission", status="disabled")
        yield None
        return
    if gate is not None:
        if registry is not None:
            raise ValueError("gate and registry are mutually exclusive")
        if gate.max_concurrency != 1:
            raise ValueError("local model gate concurrency must be 1")
        wait_started = time.monotonic()
        try:
            lease = gate.acquire(kind=kind, url=url, model=model)
        except Exception:
            _record_gmi_metric("admission", status="rejected")
            raise
        _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
        _record_gmi_metric("admission", status="admitted")
        _record_maintenance_queue_depth(gate=gate)
        runtime_started = time.monotonic()
        slot_token = _ACTIVE_LOCAL_MODEL_SLOT.set(_local_model_execution_owner())
        try:
            yield lease
        finally:
            _ACTIVE_LOCAL_MODEL_SLOT.reset(slot_token)
            gate.release(lease)
            _record_gmi_metric("runtime", status="completed", value=time.monotonic() - runtime_started)
            _record_maintenance_queue_depth(gate=gate)
        return
    selected_registry = registry or _GLOBAL_REGISTRY
    wait_started = time.monotonic()
    try:
        handle = selected_registry.acquire(url=url, model=model, kind=kind)
    except Exception:
        _record_gmi_metric("admission", status="rejected")
        raise
    _record_gmi_metric("queue_wait", status="observed", value=time.monotonic() - wait_started)
    _record_gmi_metric("admission", status="admitted")
    _record_maintenance_queue_depth(registry=selected_registry)
    runtime_started = time.monotonic()
    slot_token = _ACTIVE_LOCAL_MODEL_SLOT.set(_local_model_execution_owner())
    try:
        yield handle.lease
    finally:
        _ACTIVE_LOCAL_MODEL_SLOT.reset(slot_token)
        selected_registry.release(handle)
        _record_gmi_metric("runtime", status="completed", value=time.monotonic() - runtime_started)
        _record_maintenance_queue_depth(registry=selected_registry)
