"""Persistent recent-change snapshots for local Odysseus patch notes.

The collector is intentionally local and read-only against the repository:
it summarizes commits, dirty tracked files, untracked files, and recently
modified artifacts. Snapshots are persisted under DATA_DIR so the future UI can
show patch-note history without relying on git commits having been created.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.constants import BASE_DIR, RECENT_CHANGES_DIR

HISTORY_FILE = "history.jsonl"
LATEST_FILE = "latest.json"
DEFAULT_HOURS = 12
MAX_HISTORY_LIMIT = 100
MAX_LIST_ITEMS = 200
MAX_RENDERED_ITEMS = 30

_SKIP_DIRS = {
    ".agents",
    ".codex",
    ".git",
    ".impeccable",
    ".pytest_cache",
    "__pycache__",
    "backups",
    "data",
    "logs",
    "node_modules",
    "output",
    "venv",
    "venv.broken-20260609-220301",
}
_SKIP_PREFIXES = (
    ".agents/",
    ".codex/",
    ".impeccable/",
    ".pytest-tmp",
    ".tmp",
    "backups/",
    "data/",
    "logs/",
    "output/",
)
_SKIP_SUFFIXES = (".log", ".tmp")


@dataclass(frozen=True)
class GitCommandResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_path(repo_root: str | os.PathLike[str] | None = None) -> Path:
    return Path(repo_root or BASE_DIR).resolve()


def _history_dir(history_dir: str | os.PathLike[str] | None = None) -> Path:
    return Path(history_dir or RECENT_CHANGES_DIR).resolve()


def _run_git(repo: Path, *args: str) -> GitCommandResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=8,
            shell=False,
            check=False,
        )
    except Exception as exc:
        return GitCommandResult(False, stderr=str(exc))
    return GitCommandResult(
        completed.returncode == 0,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _rel(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except Exception:
        return path.as_posix()


def _should_skip_rel(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return (
        any(normalized.startswith(prefix) for prefix in _SKIP_PREFIXES)
        or any(normalized.endswith(suffix) for suffix in _SKIP_SUFFIXES)
    )


def _parse_log(output: str) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        commit, authored_at, author, subject = parts
        commits.append(
            {
                "commit": commit,
                "authored_at": authored_at,
                "author": author,
                "subject": subject,
            }
        )
    return commits


def _parse_name_status(output: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        path = parts[-1].strip() if len(parts) > 1 else ""
        if path and not _should_skip_rel(path):
            files.append({"status": status, "path": path})
    return files[:MAX_LIST_ITEMS]


def _parse_numstat(output: str) -> dict[str, dict[str, int | None]]:
    stats: dict[str, dict[str, int | None]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path = parts[0], parts[1], parts[-1]
        if _should_skip_rel(path):
            continue
        added = None if added_raw == "-" else int(added_raw)
        deleted = None if deleted_raw == "-" else int(deleted_raw)
        stats[path] = {"additions": added, "deletions": deleted}
    return stats


def _recent_files(repo: Path, cutoff: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cutoff_ts = cutoff.timestamp()
    for root, dirs, files in os.walk(repo):
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".pytest-tmp")
        ]
        root_path = Path(root)
        rel_root = _rel(repo, root_path)
        if rel_root != "." and _should_skip_rel(rel_root.rstrip("/") + "/"):
            dirs[:] = []
            continue
        for filename in files:
            path = root_path / filename
            rel_path = _rel(repo, path)
            if _should_skip_rel(rel_path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff_ts:
                continue
            rows.append(
                {
                    "path": rel_path,
                    "modified_at": _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
                    "size": stat.st_size,
                }
            )
    rows.sort(key=lambda item: item["modified_at"], reverse=True)
    return rows[:MAX_LIST_ITEMS]


def _domain_for_path(path: str) -> str:
    if path.startswith("plugins/telegram/") or "telegram" in path:
        return "Telegram"
    if path.startswith("plugins/obsidian/") or "obsidian" in path or "orca" in path:
        return "Obsidian"
    if path.startswith("static/") or path.startswith("routes/") or path.startswith("app.py"):
        return "UI/API"
    if path.startswith("docs/"):
        return "Docs/Roadmaps"
    if path.startswith("tests/"):
        return "Tests"
    if "nextcloud" in path:
        return "Nextcloud"
    if "release" in path or "mvp_" in path or "roadmap" in path:
        return "Release/MVP"
    if "memory" in path:
        return "Memory"
    if "secure" in path or "provider" in path:
        return "Security/Providers"
    return "Core"


def _summarize(snapshot: dict[str, Any]) -> list[str]:
    commits = snapshot.get("commits") or []
    tracked = snapshot.get("tracked_changes") or []
    untracked = snapshot.get("untracked_files") or []
    recent = snapshot.get("recent_files") or []
    domains: dict[str, int] = {}
    for item in [*tracked, *({"path": p} for p in untracked), *recent]:
        path = str(item.get("path") or "")
        if path:
            domains[_domain_for_path(path)] = domains.get(_domain_for_path(path), 0) + 1
    lines = [
        f"{len(commits)} commit(s), {len(tracked)} tracked file(s), {len(untracked)} new untracked file(s), {len(recent)} recently modified file(s)."
    ]
    if domains:
        top = sorted(domains.items(), key=lambda item: (-item[1], item[0]))[:8]
        lines.append("Main areas: " + ", ".join(f"{name} ({count})" for name, count in top) + ".")
    if commits:
        lines.append("Latest commits: " + "; ".join(c["subject"] for c in commits[:5]) + ".")
    return lines


def render_patch_notes(snapshot: dict[str, Any]) -> str:
    lines = [
        f"Patch notes snapshot `{snapshot.get('id')}`",
        f"Window: {snapshot.get('since')} to {snapshot.get('generated_at')} ({snapshot.get('hours')}h)",
        "",
    ]
    for summary in snapshot.get("summary") or []:
        lines.append(f"- {summary}")
    commits = snapshot.get("commits") or []
    if commits:
        lines.extend(["", "Commits:"])
        for commit in commits[:MAX_RENDERED_ITEMS]:
            lines.append(f"- {commit['commit']} {commit['subject']} ({commit['authored_at']})")
    tracked = snapshot.get("tracked_changes") or []
    if tracked:
        lines.extend(["", "Tracked changes:"])
        numstat = snapshot.get("numstat") or {}
        for item in tracked[:MAX_RENDERED_ITEMS]:
            path = item["path"]
            stat = numstat.get(path) or {}
            delta = ""
            if stat:
                delta = f" (+{stat.get('additions')}, -{stat.get('deletions')})"
            lines.append(f"- {item['status']} {path}{delta}")
    untracked = snapshot.get("untracked_files") or []
    if untracked:
        lines.extend(["", "New files:"])
        for path in untracked[:MAX_RENDERED_ITEMS]:
            lines.append(f"- {path}")
    recent = snapshot.get("recent_files") or []
    if recent:
        lines.extend(["", "Recently touched files:"])
        for item in recent[:MAX_RENDERED_ITEMS]:
            lines.append(f"- {item['path']} ({item['modified_at']})")
    return "\n".join(lines)


def _fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        "commits": payload.get("commits") or [],
        "tracked_changes": payload.get("tracked_changes") or [],
        "untracked_files": payload.get("untracked_files") or [],
        "numstat": payload.get("numstat") or {},
        "recent_paths": [item.get("path") for item in payload.get("recent_files") or []],
    }
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _history_path(history_dir: str | os.PathLike[str] | None = None) -> Path:
    return _history_dir(history_dir) / HISTORY_FILE


def _latest_path(history_dir: str | os.PathLike[str] | None = None) -> Path:
    return _history_dir(history_dir) / LATEST_FILE


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def _write_snapshot(snapshot: dict[str, Any], history_dir: str | os.PathLike[str] | None = None, *, force: bool = False) -> dict[str, Any]:
    target_dir = _history_dir(history_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    history = _read_jsonl(_history_path(target_dir))
    latest = history[-1] if history else None
    duplicate = bool(latest and latest.get("fingerprint") == snapshot.get("fingerprint"))
    if duplicate and not force:
        snapshot["persisted"] = False
        snapshot["duplicate_of"] = latest.get("id")
        _latest_path(target_dir).write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")
        return snapshot
    snapshot["persisted"] = True
    with _history_path(target_dir).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")
    _latest_path(target_dir).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot


def collect_recent_changes(
    *,
    hours: int = DEFAULT_HOURS,
    repo_root: str | os.PathLike[str] | None = None,
    history_dir: str | os.PathLike[str] | None = None,
    persist: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    hours = max(1, min(int(hours or DEFAULT_HOURS), 24 * 30))
    repo = _repo_path(repo_root)
    now = _utc_now()
    since = now - timedelta(hours=hours)
    since_arg = f"{hours} hours ago"

    log = _run_git(repo, "log", f"--since={since_arg}", "--date=iso", "--pretty=format:%h%x09%ad%x09%an%x09%s")
    name_status = _run_git(repo, "diff", "--name-status")
    numstat = _run_git(repo, "diff", "--numstat")
    diff_stat = _run_git(repo, "diff", "--stat")
    untracked = _run_git(repo, "ls-files", "--others", "--exclude-standard")

    tracked_changes = _parse_name_status(name_status.stdout if name_status.ok else "")
    payload: dict[str, Any] = {
        "schema_version": "recent_changes.v1",
        "generated_at": _iso(now),
        "since": _iso(since),
        "hours": hours,
        "repo_root": str(repo),
        "git_available": all(result.ok for result in (log, name_status, numstat, diff_stat, untracked)),
        "git_errors": [result.stderr for result in (log, name_status, numstat, diff_stat, untracked) if not result.ok and result.stderr],
        "commits": _parse_log(log.stdout if log.ok else ""),
        "tracked_changes": tracked_changes,
        "numstat": _parse_numstat(numstat.stdout if numstat.ok else ""),
        "diff_stat": diff_stat.stdout.strip() if diff_stat.ok else "",
        "untracked_files": [
            line.strip()
            for line in (untracked.stdout if untracked.ok else "").splitlines()
            if line.strip() and not _should_skip_rel(line.strip())
        ][:MAX_LIST_ITEMS],
        "recent_files": _recent_files(repo, since),
    }
    payload["summary"] = _summarize(payload)
    payload["fingerprint"] = _fingerprint(payload)
    payload["id"] = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{payload['fingerprint'][:10]}"
    payload["patch_notes"] = render_patch_notes(payload)
    if persist:
        return _write_snapshot(payload, history_dir, force=force)
    payload["persisted"] = False
    return payload


def list_change_history(
    *,
    history_dir: str | os.PathLike[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), MAX_HISTORY_LIMIT))
    rows = _read_jsonl(_history_path(history_dir))
    out = []
    for item in reversed(rows[-limit:]):
        out.append(
            {
                "id": item.get("id"),
                "generated_at": item.get("generated_at"),
                "since": item.get("since"),
                "hours": item.get("hours"),
                "summary": item.get("summary") or [],
                "fingerprint": item.get("fingerprint"),
            }
        )
    return out


def read_change_snapshot(
    snapshot_id: str | None = None,
    *,
    history_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    rows = _read_jsonl(_history_path(history_dir))
    if not rows:
        return None
    if not snapshot_id or snapshot_id == "latest":
        return rows[-1]
    for item in reversed(rows):
        if item.get("id") == snapshot_id:
            return item
    return None


def maybe_record_startup_snapshot() -> None:
    """Best-effort startup capture used to keep history warm across restarts."""
    try:
        collect_recent_changes(hours=24, persist=True)
    except Exception:
        pass
