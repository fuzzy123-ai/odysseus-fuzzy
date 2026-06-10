import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


def _obsidian_dir(vault_dir: str) -> str:
    path = os.path.join(vault_dir, ".obsidian")
    os.makedirs(path, exist_ok=True)
    return path


def history_path(vault_dir: str) -> str:
    return os.path.join(_obsidian_dir(vault_dir), "history.json")


def _load_raw(vault_dir: str) -> List[Dict[str, Any]]:
    path = history_path(vault_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_raw(vault_dir: str, entries: List[Dict[str, Any]]) -> None:
    path = history_path(vault_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries[-200:], fh, ensure_ascii=False, indent=2)


def list_history(vault_dir: str, limit: int = 50) -> List[Dict[str, Any]]:
    entries = _load_raw(vault_dir)
    return list(reversed(entries[-max(1, min(limit, 200)):]))


def record_action(
    vault_dir: str,
    *,
    action: str,
    owner: Optional[str],
    tool: str,
    paths: Optional[List[str]] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    reversible: bool = True,
) -> Dict[str, Any]:
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": owner or "default",
        "tool": tool,
        "action": action,
        "paths": paths or [],
        "before": before or {},
        "after": after or {},
        "reversible": bool(reversible),
        "undone": False,
    }
    entries = _load_raw(vault_dir)
    entries.append(entry)
    _save_raw(vault_dir, entries)
    return entry


def mark_undone(vault_dir: str, entry_id: str) -> None:
    entries = _load_raw(vault_dir)
    for entry in entries:
        if entry.get("id") == entry_id:
            entry["undone"] = True
            break
    _save_raw(vault_dir, entries)


def latest_reversible(vault_dir: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    owner_name = owner or "default"
    for entry in reversed(_load_raw(vault_dir)):
        if entry.get("undone"):
            continue
        if not entry.get("reversible"):
            continue
        if owner is not None and entry.get("owner") != owner_name:
            continue
        return entry
    return None
