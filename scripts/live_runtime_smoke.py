from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("ODYSSEUS_SMOKE_URL", "http://127.0.0.1:7000").rstrip("/")


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            lower = str(key).lower()
            if any(marker in lower for marker in ("token", "secret", "password", "chat_id", "chat_target", "repository", "runner_path")):
                redacted[key] = child if isinstance(child, bool) or child in (None, "") else "[redacted]"
            elif key in {"snapshots", "history", "messages", "tools", "resources", "prompts"} and isinstance(child, list):
                redacted[f"{key}_count"] = len(child)
            else:
                redacted[key] = _sanitize(child)
        return redacted
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:6]]
    if isinstance(value, str) and len(value) > 220:
        return value[:220] + "..."
    return value


def _request(method: str, path: str, body: Any | None = None, *, timeout: int = 12) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    internal_token = os.environ.get("ODYSSEUS_INTERNAL_TOKEN")
    if internal_token:
        headers["X-Odysseus-Internal-Token"] = internal_token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {"raw": raw[:200]}
            return {"http": response.status, "ok": 200 <= response.status < 300, "body": _sanitize(payload)}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:200]}
        return {"http": exc.code, "ok": False, "body": _sanitize(payload)}
    except Exception as exc:
        return {"http": 0, "ok": False, "error_type": type(exc).__name__, "error": str(exc)[:180]}


def _telegram_get_me() -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "reason": "TELEGRAM_BOT_TOKEN missing"}
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return {
            "http": response.status,
            "ok": bool(payload.get("ok")),
            "bot_id_present": bool(result.get("id")),
            "username_present": bool(result.get("username")),
        }
    except Exception as exc:
        return {"http": 0, "ok": False, "error_type": type(exc).__name__, "error": str(exc)[:160]}


def main() -> int:
    _load_dotenv()
    results: dict[str, Any] = {}
    for _attempt in range(45):
        results["version"] = _request("GET", "/api/version")
        if results["version"].get("ok"):
            break
        time.sleep(1)

    results["update_status"] = _request("GET", "/api/admin/system/update-status")
    results["update_check"] = _request("POST", "/api/admin/system/update-check", {})
    results["backup_now"] = _request("POST", "/api/admin/system/backup-now", {})
    results["update_now"] = _request("POST", "/api/admin/system/update-now", {})

    results["mcp_info_before"] = _request("GET", "/api/plugins/mcp/info")
    config_before = _request("GET", "/api/plugins/mcp/config")
    results["mcp_config_before"] = config_before
    original = config_before.get("body") if isinstance(config_before.get("body"), dict) else None
    results["mcp_disabled_probe"] = _request("POST", "/api/plugins/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    if original is not None:
        safe_config = dict(original)
        safe_config.update(
            {
                "enabled": True,
                "allow_owner_scoped_writes": False,
                "allow_private_reads": False,
                "allow_filesystem_reads": False,
                "allow_generic_api": False,
            }
        )
        results["mcp_enable_safe"] = _request("POST", "/api/plugins/mcp/config", safe_config)
        batch = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "odysseus-live-smoke", "version": "0"}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "odysseus://mcp/readiness"}},
        ]
        results["mcp_jsonrpc_batch"] = _request("POST", "/api/plugins/mcp", batch, timeout=25)
        results["mcp_restore_config"] = _request("POST", "/api/plugins/mcp/config", original)
        results["mcp_info_after"] = _request("GET", "/api/plugins/mcp/info")

    results["telegram_status"] = _request("GET", "/api/plugins/telegram/status")
    results["telegram_bot_api_getMe"] = _telegram_get_me()
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
