"""Default-off Telegram control plane for one Windows workstation.

The server sends Wake-on-LAN directly and queues every other action for an
outbound-polling Windows user agent.  Persistent state is deliberately
content-free: it never stores Telegram identifiers, MAC addresses, secrets,
hostnames, commands supplied by a user, or subprocess/provider output.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


_STATE_FILE = "telegram_pc_control_state.json"
_ACTIONS = frozenset({"start_codex", "lock", "hibernate", "shutdown"})
_OUTCOMES = frozenset({"accepted", "completed", "failed"})
_RESULT_CODES = frozenset(
    {
        "ok",
        "codex_started",
        "codex_already_running",
        "locked",
        "hibernate_started",
        "shutdown_started",
        "interactive_session_required",
        "unsupported_action",
        "execution_failed",
        "duplicate_action",
    }
)
_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_AGENT_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class PcControlError(ValueError):
    """A fixed-code error that never contains caller-controlled content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class PcControlConfig:
    enabled: bool
    wake_enabled: bool
    mac: str
    broadcast: str
    agent_secret: str
    confirmation_ttl_seconds: int
    command_ttl_seconds: int
    agent_stale_seconds: int

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "PcControlConfig":
        return cls(
            enabled=_bool(environ.get("TELEGRAM_PC_CONTROL_ENABLED")),
            wake_enabled=_bool(environ.get("TELEGRAM_PC_WAKE_ENABLED")),
            mac=str(environ.get("TELEGRAM_PC_MAC") or "").strip(),
            broadcast=str(environ.get("TELEGRAM_PC_BROADCAST") or "").strip(),
            agent_secret=str(environ.get("TELEGRAM_PC_AGENT_SECRET") or ""),
            confirmation_ttl_seconds=_bounded_int(
                environ.get("TELEGRAM_PC_CONFIRM_TTL_SECONDS"), 60, 30, 300
            ),
            command_ttl_seconds=_bounded_int(
                environ.get("TELEGRAM_PC_COMMAND_TTL_SECONDS"), 600, 60, 3600
            ),
            agent_stale_seconds=_bounded_int(
                environ.get("TELEGRAM_PC_AGENT_STALE_SECONDS"), 45, 15, 300
            ),
        )

    @property
    def agent_ready(self) -> bool:
        return self.enabled and len(self.agent_secret) >= 24

    @property
    def wake_ready(self) -> bool:
        if not self.enabled or not self.wake_enabled or not _MAC_RE.fullmatch(self.mac):
            return False
        try:
            return ipaddress.ip_address(self.broadcast).version == 4
        except ValueError:
            return False


def send_magic_packet(
    mac: str,
    broadcast: str,
    *,
    port: int = 9,
    socket_factory: Callable[..., Any] = socket.socket,
) -> dict[str, Any]:
    """Send exactly one Wake-on-LAN packet after strict target validation."""

    if not _MAC_RE.fullmatch(str(mac or "").strip()):
        raise PcControlError("invalid_mac")
    try:
        address = ipaddress.ip_address(str(broadcast or "").strip())
    except ValueError as error:
        raise PcControlError("invalid_broadcast") from None
    if address.version != 4:
        raise PcControlError("invalid_broadcast")
    normalized = str(mac).replace(":", "").replace("-", "")
    packet = b"\xff" * 6 + bytes.fromhex(normalized) * 16
    try:
        with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as handle:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sent = int(handle.sendto(packet, (str(address), int(port))))
    except PcControlError:
        raise
    except Exception:
        raise PcControlError("wake_send_failed") from None
    if sent != len(packet):
        raise PcControlError("wake_send_failed")
    return {"status": "wake_packet_sent", "bytes_sent": sent, "raw_target_visible": False}


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "queue": [],
        "history": [],
        "agent": {
            "last_seen_at": 0,
            "agent_version": "",
            "interactive_session": False,
            "codex_running": False,
        },
        "agent_nonce_hashes": [],
        "raw_content_visible": False,
        "secret_values_visible": False,
        "raw_identifiers_visible": False,
    }


class TelegramPcControlStore:
    """Atomic, bounded persistence for content-free PC actions and heartbeats."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        now_fn: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _STATE_FILE
        self.now = now_fn
        self.id_factory = id_factory or (lambda: secrets.token_hex(12))
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return _empty_state()
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return _empty_state()
        if not isinstance(value.get("queue"), list) or not isinstance(value.get("history"), list):
            return _empty_state()
        if not isinstance(value.get("agent"), dict) or not isinstance(value.get("agent_nonce_hashes"), list):
            return _empty_state()
        value["raw_content_visible"] = False
        value["secret_values_visible"] = False
        value["raw_identifiers_visible"] = False
        return value

    def _write(self, state: dict[str, Any]) -> None:
        state["queue"] = list(state.get("queue") or [])[-50:]
        state["history"] = list(state.get("history") or [])[-100:]
        state["agent_nonce_hashes"] = list(state.get("agent_nonce_hashes") or [])[-256:]
        state["raw_content_visible"] = False
        state["secret_values_visible"] = False
        state["raw_identifiers_visible"] = False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.data_dir
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except Exception:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise PcControlError("pc_state_write_failed") from None

    def _normalize_queue(self, state: dict[str, Any], now: int) -> None:
        for item in state["queue"]:
            if not isinstance(item, dict):
                continue
            if item.get("status") == "leased" and int(item.get("lease_until") or 0) <= now:
                item["status"] = "pending"
                item["lease_until"] = 0
            if item.get("status") in {"pending", "leased"} and int(item.get("expires_at") or 0) <= now:
                item["status"] = "expired"
                item["lease_until"] = 0

    def enqueue(self, action: str, *, ttl_seconds: int, deduplicate: bool = True) -> dict[str, Any]:
        if action not in _ACTIONS:
            raise PcControlError("unsupported_action")
        with self._lock:
            state = self._read()
            now = int(self.now())
            self._normalize_queue(state, now)
            if deduplicate:
                for item in state["queue"]:
                    if item.get("action") == action and item.get("status") in {"pending", "leased"}:
                        self._write(state)
                        return dict(item)
            action_id = str(self.id_factory() or "")
            if not _ACTION_ID_RE.fullmatch(action_id):
                raise PcControlError("invalid_action_id")
            if any(item.get("action_id") == action_id for item in state["queue"]):
                raise PcControlError("action_id_collision")
            item = {
                "action_id": action_id,
                "action": action,
                "status": "pending",
                "created_at": now,
                "expires_at": now + int(ttl_seconds),
                "lease_until": 0,
            }
            state["queue"].append(item)
            state["history"].append(
                {"event": "queued", "action": action, "action_id": action_id, "recorded_at": now}
            )
            self._write(state)
            return dict(item)

    def lease_next(self, *, lease_seconds: int = 30) -> dict[str, Any] | None:
        with self._lock:
            state = self._read()
            now = int(self.now())
            self._normalize_queue(state, now)
            selected = None
            for item in state["queue"]:
                if item.get("status") != "pending":
                    continue
                item["status"] = "leased"
                item["lease_until"] = now + max(10, min(120, int(lease_seconds)))
                selected = {
                    "action_id": item["action_id"],
                    "action": item["action"],
                    "expires_at": int(item["expires_at"]),
                }
                break
            self._write(state)
            return selected

    def record_result(self, action_id: str, *, outcome: str, code: str) -> dict[str, Any]:
        if not _ACTION_ID_RE.fullmatch(str(action_id or "")):
            raise PcControlError("invalid_agent_result")
        if outcome not in _OUTCOMES or code not in _RESULT_CODES:
            raise PcControlError("invalid_agent_result")
        with self._lock:
            state = self._read()
            item = next((entry for entry in state["queue"] if entry.get("action_id") == action_id), None)
            if not isinstance(item, dict):
                raise PcControlError("unknown_action_id")
            item["status"] = outcome
            item["result_code"] = code
            item["lease_until"] = 0
            item["completed_at"] = int(self.now())
            state["history"].append(
                {
                    "event": "result",
                    "action": item.get("action"),
                    "action_id": action_id,
                    "outcome": outcome,
                    "code": code,
                    "recorded_at": int(self.now()),
                }
            )
            self._write(state)
            return dict(item)

    def update_heartbeat(
        self, *, agent_version: str, interactive_session: bool, codex_running: bool
    ) -> dict[str, Any]:
        if not _AGENT_VERSION_RE.fullmatch(agent_version):
            raise PcControlError("invalid_agent_payload")
        if type(interactive_session) is not bool or type(codex_running) is not bool:
            raise PcControlError("invalid_agent_payload")
        with self._lock:
            state = self._read()
            state["agent"] = {
                "last_seen_at": int(self.now()),
                "agent_version": agent_version,
                "interactive_session": interactive_session,
                "codex_running": codex_running,
            }
            self._write(state)
            return dict(state["agent"])

    def summary(self, *, stale_seconds: int) -> dict[str, Any]:
        with self._lock:
            state = self._read()
        agent = state["agent"]
        last_seen = int(agent.get("last_seen_at") or 0)
        now = int(self.now())
        pending = sum(
            1 for item in state["queue"] if isinstance(item, dict) and item.get("status") in {"pending", "leased"}
        )
        return {
            "agent_online": bool(last_seen and now - last_seen <= int(stale_seconds)),
            "last_seen_at": last_seen,
            "interactive_session": agent.get("interactive_session") is True,
            "codex_running": agent.get("codex_running") is True,
            "pending_actions": min(pending, 50),
            "raw_content_visible": False,
        }

    def claim_agent_nonce(self, nonce: str, *, ttl_seconds: int = 120) -> bool:
        nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        with self._lock:
            state = self._read()
            now = int(self.now())
            retained = [
                item
                for item in state["agent_nonce_hashes"]
                if isinstance(item, dict) and now - int(item.get("recorded_at") or 0) <= ttl_seconds
            ]
            if any(item.get("nonce_hash") == nonce_hash for item in retained):
                state["agent_nonce_hashes"] = retained
                self._write(state)
                return False
            retained.append({"nonce_hash": nonce_hash, "recorded_at": now})
            state["agent_nonce_hashes"] = retained
            self._write(state)
            return True


def _body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def build_agent_request_signature(
    secret: str,
    *,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    material = "\n".join(
        ("v1", str(timestamp), str(nonce), str(method).upper(), str(path), _body_digest(body))
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def build_agent_response_signature(
    secret: str, *, request_nonce: str, status_code: int, body: bytes
) -> str:
    material = "\n".join(
        ("v1", str(request_nonce), str(int(status_code)), _body_digest(body))
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def verify_agent_request(
    secret: str,
    *,
    headers: Mapping[str, str],
    method: str,
    path: str,
    body: bytes,
    store: TelegramPcControlStore,
    now_fn: Callable[[], float] = time.time,
    max_skew_seconds: int = 60,
) -> str:
    if len(secret) < 24:
        raise PcControlError("agent_auth_required")
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    timestamp = lowered.get("x-odysseus-pc-timestamp", "")
    nonce = lowered.get("x-odysseus-pc-nonce", "")
    signature = lowered.get("x-odysseus-pc-signature", "").lower()
    if not timestamp or not nonce or not signature:
        raise PcControlError("agent_auth_required")
    try:
        issued_at = int(timestamp)
    except ValueError:
        raise PcControlError("agent_auth_invalid") from None
    if abs(int(now_fn()) - issued_at) > int(max_skew_seconds):
        raise PcControlError("agent_request_expired")
    if not _NONCE_RE.fullmatch(nonce) or not _HEX_64_RE.fullmatch(signature):
        raise PcControlError("agent_auth_invalid")
    expected = build_agent_request_signature(
        secret,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body=body,
    )
    if not hmac.compare_digest(signature, expected):
        raise PcControlError("agent_auth_invalid")
    if not store.claim_agent_nonce(nonce):
        raise PcControlError("agent_request_replayed")
    return nonce


class TelegramPcControlService:
    """Command facade shared by Telegram intake and the Windows agent routes."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
        now_fn: Callable[[], float] = time.time,
        wake_sender: Callable[[str, str], dict[str, Any]] = send_magic_packet,
        id_factory: Callable[[], str] | None = None,
    ):
        self.environ = os.environ if environ is None else environ
        self.now = now_fn
        self.wake_sender = wake_sender
        self.store = TelegramPcControlStore(data_dir, now_fn=now_fn, id_factory=id_factory)
        self._confirmations: dict[str, dict[str, Any]] = {}
        self._confirmation_lock = threading.RLock()

    def config(self) -> PcControlConfig:
        return PcControlConfig.from_environ(self.environ)

    @staticmethod
    def _chat_handle(chat_id: str) -> str:
        return hashlib.sha256(f"pc-confirm:{chat_id}".encode("utf-8")).hexdigest()

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            "enabled": config.enabled,
            "wake_ready": config.wake_ready,
            "agent_ready": config.agent_ready,
            **self.store.summary(stale_seconds=config.agent_stale_seconds),
            "secret_values_visible": False,
            "raw_identifiers_visible": False,
        }

    def _result(self, status: str, reply_text: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "reply_text": reply_text,
            **extra,
            "raw_content_visible": False,
            "secret_values_visible": False,
            "raw_identifiers_visible": False,
        }

    def _require_enabled(self) -> PcControlConfig | None:
        config = self.config()
        return config if config.enabled else None

    def handle_command(self, command: str, *, chat_id: str) -> dict[str, Any]:
        if command == "pc_status":
            snapshot = self.status()
            if not snapshot["enabled"]:
                text = "PC-Steuerung ist serverseitig noch nicht aktiviert."
            elif snapshot["agent_online"]:
                codex = "Codex laeuft" if snapshot["codex_running"] else "Codex laeuft nicht"
                session = "Sitzung aktiv" if snapshot["interactive_session"] else "keine interaktive Sitzung"
                text = f"PC-Agent online; {session}; {codex}; Warteschlange: {snapshot['pending_actions']}."
            else:
                text = f"PC-Agent offline oder unbekannt; Warteschlange: {snapshot['pending_actions']}."
            return self._result("pc_status", text, pc=snapshot)
        if command == "pc_help":
            return self._result(
                "pc_help",
                "PC-Kommandos: /pc status, /pc an, /pc codex, /pc sperren, /pc ruhezustand, /pc aus, /pc abbrechen.",
            )
        if command == "pc_cancel":
            with self._confirmation_lock:
                self._confirmations.pop(self._chat_handle(chat_id), None)
            return self._result("pc_confirmation_cancelled", "Ausstehende PC-Aktion verworfen.")

        config = self._require_enabled()
        if config is None:
            return self._result("pc_control_disabled", "PC-Steuerung ist serverseitig noch nicht aktiviert.")

        if command == "pc_wake":
            if not config.wake_ready:
                return self._result("pc_wake_not_ready", "Wake-on-LAN ist noch nicht vollstaendig konfiguriert.")
            try:
                self.wake_sender(config.mac, config.broadcast)
            except PcControlError as error:
                return self._result(error.code, "Wake-on-LAN konnte nicht gesendet werden.")
            queued_action = ""
            if config.agent_ready:
                self.store.enqueue("start_codex", ttl_seconds=config.command_ttl_seconds)
                queued_action = "start_codex"
            return self._result(
                "pc_wake_sent",
                "Wake-on-LAN gesendet; Codex startet, sobald die Windows-Sitzung erreichbar ist.",
                queued_action=queued_action,
            )

        immediate = {"pc_codex": "start_codex", "pc_lock": "lock"}
        if command in immediate:
            if not config.agent_ready:
                return self._result("pc_agent_not_ready", "Der Windows-Agent ist noch nicht aktiviert.")
            action = immediate[command]
            self.store.enqueue(action, ttl_seconds=config.command_ttl_seconds)
            return self._result("pc_action_queued", f"PC-Aktion vorgemerkt: {action}.", queued_action=action)

        requested = {
            "pc_shutdown_request": "shutdown",
            "pc_hibernate_request": "hibernate",
        }
        if command in requested:
            action = requested[command]
            with self._confirmation_lock:
                self._confirmations[self._chat_handle(chat_id)] = {
                    "action": action,
                    "expires_at": int(self.now()) + config.confirmation_ttl_seconds,
                }
            confirm_text = "/pc aus bestaetigen" if action == "shutdown" else "/pc ruhezustand ja"
            return self._result(
                "pc_confirmation_required",
                f"Bestaetigung erforderlich. Innerhalb von {config.confirmation_ttl_seconds} Sekunden: {confirm_text}",
                pending_action=action,
            )

        confirmed = {
            "pc_shutdown_confirm": "shutdown",
            "pc_hibernate_confirm": "hibernate",
        }
        if command in confirmed:
            action = confirmed[command]
            handle = self._chat_handle(chat_id)
            with self._confirmation_lock:
                pending = self._confirmations.pop(handle, None)
            if not isinstance(pending, dict) or pending.get("action") != action:
                return self._result("pc_confirmation_missing", "Keine passende PC-Bestaetigung ist offen.")
            if int(pending.get("expires_at") or 0) <= int(self.now()):
                return self._result("pc_confirmation_expired", "Die PC-Bestaetigung ist abgelaufen.")
            if not config.agent_ready:
                return self._result("pc_agent_not_ready", "Der Windows-Agent ist noch nicht aktiviert.")
            self.store.enqueue(action, ttl_seconds=config.command_ttl_seconds)
            return self._result("pc_action_queued", f"PC-Aktion bestaetigt und vorgemerkt: {action}.", queued_action=action)

        return self.handle_command("pc_help", chat_id=chat_id)

    def agent_poll(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        config = self.config()
        if not config.enabled or not config.agent_ready:
            raise PcControlError("agent_auth_required")
        if type(payload) is not dict or set(payload) != {
            "agent_version",
            "interactive_session",
            "codex_running",
        }:
            raise PcControlError("invalid_agent_payload")
        self.store.update_heartbeat(
            agent_version=str(payload.get("agent_version") or ""),
            interactive_session=payload.get("interactive_session"),
            codex_running=payload.get("codex_running"),
        )
        return {
            "status": "ok",
            "command": self.store.lease_next(),
            "raw_content_visible": False,
        }

    def agent_result(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        config = self.config()
        if not config.enabled or not config.agent_ready:
            raise PcControlError("agent_auth_required")
        if type(payload) is not dict or set(payload) != {"action_id", "outcome", "code"}:
            raise PcControlError("invalid_agent_result")
        item = self.store.record_result(
            str(payload.get("action_id") or ""),
            outcome=str(payload.get("outcome") or ""),
            code=str(payload.get("code") or ""),
        )
        return {"status": item["status"], "raw_content_visible": False}

    def authenticate_agent_request(
        self, *, headers: Mapping[str, str], method: str, path: str, body: bytes
    ) -> str:
        config = self.config()
        if not config.enabled or not config.agent_ready:
            raise PcControlError("agent_auth_required")
        return verify_agent_request(
            config.agent_secret,
            headers=headers,
            method=method,
            path=path,
            body=body,
            store=self.store,
            now_fn=self.now,
        )

    def sign_agent_response(self, *, request_nonce: str, status_code: int, body: bytes) -> str:
        config = self.config()
        if not config.agent_ready:
            raise PcControlError("agent_auth_required")
        return build_agent_response_signature(
            config.agent_secret,
            request_nonce=request_nonce,
            status_code=status_code,
            body=body,
        )


def handle_telegram_pc_control_command(
    command: str,
    *,
    message: Mapping[str, Any],
    raw_chat_id: str,
    reply_handler: Callable[[str, str, int | None], dict[str, Any]] | None,
    service: TelegramPcControlService | None,
) -> dict[str, Any] | None:
    """Bridge a parsed PC command into the isolated service and safe reply."""

    if not command.startswith("pc_"):
        return None
    if service is None:
        result = {
            "status": "pc_control_unavailable",
            "reply_text": "PC-Steuerung ist in diesem Lauf nicht verfuegbar.",
            "raw_content_visible": False,
        }
    else:
        try:
            result = service.handle_command(command, chat_id=raw_chat_id)
        except PcControlError:
            result = {
                "status": "pc_control_error",
                "reply_text": "PC-Steuerung konnte die Aktion nicht sicher verarbeiten.",
                "raw_content_visible": False,
            }
    reply = None
    if reply_handler is not None and raw_chat_id:
        reply = reply_handler(
            raw_chat_id,
            str(result.get("reply_text") or ""),
            int(message.get("message_id")) if isinstance(message.get("message_id"), int) else None,
        )
    public = {
        key: value
        for key, value in result.items()
        if key not in {"secret", "token", "mac", "broadcast", "chat_id"}
    }
    return {
        "command": command,
        "status": str(result.get("status") or "pc_control_error"),
        "binding": {},
        "reply_text": str(result.get("reply_text") or ""),
        "reply": reply,
        "pc_control": public,
    }
