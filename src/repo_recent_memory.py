"""Repo-scoped recent-change capsules for project context and memory."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.atomic_io import atomic_write_json
from src.constants import BASE_DIR, RECENT_CHANGES_DIR
from src.recent_changes import collect_recent_changes
from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError


REPO_CHANGES_SCHEMA = "odysseus.repo_recent_changes.v1"
REPO_MEMORY_RECORD_SCHEMA = "odysseus.repo_change_memory_record.v1"
REPO_RAPTORGRAPH_EVENT_SCHEMA = "odysseus.repo_change_raptorgraph_event.v1"
REPO_PROJECT_CONTEXT_SCHEMA = "odysseus.repo_project_context.v1"
HISTORY_FILE = "history.jsonl"
LATEST_FILE = "latest.json"
MAX_HISTORY_LIMIT = 100
MAX_PATHS = 80
MAX_COMMITS = 25

_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|credential)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")


class RepoRecentMemoryError(ValueError):
    """Raised when a repo change capsule would be unsafe."""


@dataclass(frozen=True, slots=True)
class RepoChangeCapsuleReport:
    status: str
    snapshot: dict[str, Any]
    project_context: dict[str, Any]
    memory_records: tuple[dict[str, Any], ...]
    raptorgraph_event: dict[str, Any]
    persisted: bool
    duplicate_of: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "snapshot": dict(self.snapshot),
            "project_context": dict(self.project_context),
            "memory_records": [dict(record) for record in self.memory_records],
            "raptorgraph_event": dict(self.raptorgraph_event),
            "persisted": self.persisted,
            "duplicate_of": self.duplicate_of,
        }


def collect_repo_change_capsule(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    workspace_base: str | Path = BASE_DIR,
    hours: int = 12,
    history_dir: str | Path | None = None,
    persist: bool = True,
    force: bool = False,
) -> RepoChangeCapsuleReport:
    record, repo_path = _resolve_repo(registry=registry, repo_id=repo_id, workspace_base=workspace_base)
    raw_snapshot = collect_recent_changes(
        repo_root=repo_path,
        history_dir=None,
        hours=hours,
        persist=False,
        force=force,
    )
    snapshot = build_repo_change_snapshot(record=record, raw_snapshot=raw_snapshot)
    persisted = False
    duplicate_of = ""
    if persist:
        snapshot, persisted, duplicate_of = _write_snapshot(snapshot, history_dir=history_dir, force=force)
    else:
        snapshot["persisted"] = False
    project_context = build_repo_project_context(snapshot)
    memory_records = build_repo_memory_records(snapshot)
    raptor_event = build_repo_raptorgraph_event(snapshot, project_context=project_context, memory_records=memory_records)
    return RepoChangeCapsuleReport(
        status="collected",
        snapshot=snapshot,
        project_context=project_context,
        memory_records=tuple(memory_records),
        raptorgraph_event=raptor_event,
        persisted=bool(persisted),
        duplicate_of=duplicate_of,
    )


def build_repo_change_snapshot(*, record: RepoRecord, raw_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, RepoRecord):
        raise RepoRecentMemoryError("record must be a RepoRecord")
    if not isinstance(raw_snapshot, Mapping):
        raise RepoRecentMemoryError("raw_snapshot must be a mapping")
    privacy = record.privacy_class
    commits = _safe_commits(raw_snapshot.get("commits") or [], privacy=privacy)
    tracked = _safe_paths(raw_snapshot.get("tracked_changes") or [], privacy=privacy)
    untracked = _safe_untracked(raw_snapshot.get("untracked_files") or [], privacy=privacy)
    recent = _safe_recent_files(raw_snapshot.get("recent_files") or [], privacy=privacy)
    domains = _domain_counts(_domain_source_paths(raw_snapshot))
    counts = {
        "commits": len(raw_snapshot.get("commits") or []),
        "tracked_changes": len(raw_snapshot.get("tracked_changes") or []),
        "untracked_files": len(raw_snapshot.get("untracked_files") or []),
        "recent_files": len(raw_snapshot.get("recent_files") or []),
    }
    snapshot: dict[str, Any] = {
        "schema": REPO_CHANGES_SCHEMA,
        "repo_id": record.repo_id,
        "repo_title": _redact_text(record.title),
        "repo_kind": record.repo_kind,
        "linked_project_slug": record.linked_project_slug,
        "privacy_class": privacy,
        "provider_scope": record.provider_scope,
        "external_summary_allowed": privacy == "public",
        "local_only_summary": privacy in {"private", "sensitive"},
        "generated_at": _redact_text(raw_snapshot.get("generated_at") or ""),
        "since": _redact_text(raw_snapshot.get("since") or ""),
        "hours": int(raw_snapshot.get("hours") or 0),
        "git_available": bool(raw_snapshot.get("git_available", False)),
        "counts": counts,
        "domains": domains,
        "commits": commits,
        "tracked_changes": tracked,
        "untracked_files": untracked,
        "recent_files": recent,
        "summary": _summary_lines(record, counts=counts, domains=domains, commits=commits),
        "redaction_policy": _redaction_policy(privacy),
    }
    snapshot["fingerprint"] = _fingerprint(snapshot)
    snapshot["id"] = f"{record.repo_id}-{snapshot['fingerprint'][:12]}"
    return snapshot


def build_repo_project_context(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    _assert_snapshot(snapshot)
    summary = list(snapshot.get("summary") or [])
    return {
        "schema": REPO_PROJECT_CONTEXT_SCHEMA,
        "repo_id": snapshot["repo_id"],
        "snapshot_id": snapshot["id"],
        "privacy_class": snapshot["privacy_class"],
        "provider_scope": snapshot["provider_scope"],
        "external_summary_allowed": bool(snapshot.get("external_summary_allowed")),
        "context_lines": summary[:8],
        "counts": dict(snapshot.get("counts") or {}),
        "domains": dict(snapshot.get("domains") or {}),
        "source_fingerprint": snapshot["fingerprint"],
    }


def build_repo_memory_records(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    _assert_snapshot(snapshot)
    lines = list(snapshot.get("summary") or [])
    counts = dict(snapshot.get("counts") or {})
    domains = dict(snapshot.get("domains") or {})
    text = "\n".join(
        [
            f"Repo changes for {snapshot['repo_id']}",
            f"Snapshot: {snapshot['id']}",
            f"Privacy: {snapshot['privacy_class']} ({snapshot['provider_scope']})",
            *lines[:8],
        ]
    )
    metadata = {
        "schema": REPO_MEMORY_RECORD_SCHEMA,
        "repo_id": snapshot["repo_id"],
        "snapshot_id": snapshot["id"],
        "source_fingerprint": snapshot["fingerprint"],
        "privacy_class": snapshot["privacy_class"],
        "provider_scope": snapshot["provider_scope"],
        "external_summary_allowed": bool(snapshot.get("external_summary_allowed")),
        "counts": counts,
        "domains": domains,
    }
    return (
        {
            "schema": REPO_MEMORY_RECORD_SCHEMA,
            "memory_id": f"repo-change-{snapshot['repo_id']}-{snapshot['fingerprint'][:12]}",
            "source": "repo_recent_changes",
            "category": "project",
            "text": _redact_text(text),
            "metadata": metadata,
        },
    )


def build_repo_raptorgraph_event(
    snapshot: Mapping[str, Any],
    *,
    project_context: Mapping[str, Any] | None = None,
    memory_records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    _assert_snapshot(snapshot)
    records = tuple(memory_records)
    return {
        "schema": REPO_RAPTORGRAPH_EVENT_SCHEMA,
        "event": "repo_recent_changes_snapshot",
        "source_provider": "repo_control",
        "repo_id": snapshot["repo_id"],
        "snapshot_id": snapshot["id"],
        "source_fingerprint": snapshot["fingerprint"],
        "privacy_class": snapshot["privacy_class"],
        "provider_scope": snapshot["provider_scope"],
        "external_summary_allowed": bool(snapshot.get("external_summary_allowed")),
        "counts": dict(snapshot.get("counts") or {}),
        "domains": dict(snapshot.get("domains") or {}),
        "memory_record_ids": [str(record.get("memory_id") or "") for record in records if record.get("memory_id")],
        "project_context_schema": (project_context or {}).get("schema") if isinstance(project_context, Mapping) else None,
        "redaction_policy": dict(snapshot.get("redaction_policy") or {}),
    }


def list_repo_change_history(
    *,
    repo_id: Any,
    history_dir: str | Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = _read_history(repo_id=str(repo_id), history_dir=history_dir)
    limit = max(1, min(int(limit or 20), MAX_HISTORY_LIMIT))
    out: list[dict[str, Any]] = []
    for item in reversed(rows[-limit:]):
        out.append(
            {
                "id": item.get("id"),
                "repo_id": item.get("repo_id"),
                "generated_at": item.get("generated_at"),
                "since": item.get("since"),
                "hours": item.get("hours"),
                "privacy_class": item.get("privacy_class"),
                "counts": item.get("counts") or {},
                "domains": item.get("domains") or {},
                "fingerprint": item.get("fingerprint"),
            }
        )
    return out


def _resolve_repo(*, registry: RepoRegistry, repo_id: Any, workspace_base: str | Path) -> tuple[RepoRecord, Path]:
    if not isinstance(registry, RepoRegistry):
        raise RepoRecentMemoryError("registry must be a RepoRegistry")
    try:
        record = registry.get(repo_id)
    except RepoRegistryError as exc:
        raise RepoRecentMemoryError(str(exc)) from exc
    base = Path(workspace_base).resolve()
    repo_path = (base / record.path_ref).resolve()
    try:
        repo_path.relative_to(base)
    except ValueError as exc:
        raise RepoRecentMemoryError("registered repo path is outside workspace_base") from exc
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        raise RepoRecentMemoryError("registered repo path is not a local Git repository")
    return record, repo_path


def _write_snapshot(
    snapshot: dict[str, Any],
    *,
    history_dir: str | Path | None,
    force: bool,
) -> tuple[dict[str, Any], bool, str]:
    target = _repo_history_dir(snapshot["repo_id"], history_dir=history_dir)
    target.mkdir(parents=True, exist_ok=True)
    rows = _read_history(repo_id=snapshot["repo_id"], history_dir=history_dir)
    latest = rows[-1] if rows else None
    duplicate = bool(latest and latest.get("fingerprint") == snapshot.get("fingerprint"))
    if duplicate and not force:
        snapshot["persisted"] = False
        snapshot["duplicate_of"] = latest.get("id", "")
        atomic_write_json(str(target / LATEST_FILE), latest, indent=2)
        return snapshot, False, str(latest.get("id") or "")
    snapshot["persisted"] = True
    with (target / HISTORY_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")
    atomic_write_json(str(target / LATEST_FILE), snapshot, indent=2)
    return snapshot, True, ""


def _read_history(*, repo_id: str, history_dir: str | Path | None) -> list[dict[str, Any]]:
    path = _repo_history_dir(repo_id, history_dir=history_dir) / HISTORY_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows


def _repo_history_dir(repo_id: str, *, history_dir: str | Path | None) -> Path:
    safe_repo = _safe_repo_id(repo_id)
    return Path(history_dir or RECENT_CHANGES_DIR).resolve() / "repos" / safe_repo


def _safe_commits(values: Iterable[Any], *, privacy: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in list(values)[:MAX_COMMITS]:
        if not isinstance(item, Mapping):
            continue
        commit = _commit_ref(item.get("commit"))
        if privacy == "sensitive":
            rows.append({"commit": commit})
            continue
        rows.append(
            {
                "commit": commit,
                "authored_at": _redact_text(item.get("authored_at") or ""),
                "subject": _redact_text(item.get("subject") or ""),
            }
        )
    return rows


def _safe_paths(values: Iterable[Any], *, privacy: str) -> list[dict[str, str]]:
    if privacy == "sensitive":
        return []
    rows: list[dict[str, str]] = []
    for item in list(values)[:MAX_PATHS]:
        if not isinstance(item, Mapping):
            continue
        path = _safe_rel_path(item.get("path") or "")
        status = _redact_text(item.get("status") or "")[:20]
        if path:
            rows.append({"status": status, "path": path})
    return rows


def _safe_untracked(values: Iterable[Any], *, privacy: str) -> list[str]:
    if privacy == "sensitive":
        return []
    rows: list[str] = []
    for value in list(values)[:MAX_PATHS]:
        path = _safe_rel_path(value)
        if path:
            rows.append(path)
    return rows


def _safe_recent_files(values: Iterable[Any], *, privacy: str) -> list[dict[str, Any]]:
    if privacy == "sensitive":
        return []
    rows: list[dict[str, Any]] = []
    for item in list(values)[:MAX_PATHS]:
        if not isinstance(item, Mapping):
            continue
        path = _safe_rel_path(item.get("path") or "")
        if path:
            rows.append(
                {
                    "path": path,
                    "modified_at": _redact_text(item.get("modified_at") or ""),
                    "size": int(item.get("size") or 0),
                }
            )
    return rows


def _summary_lines(
    record: RepoRecord,
    *,
    counts: Mapping[str, Any],
    domains: Mapping[str, int],
    commits: Iterable[Mapping[str, str]],
) -> list[str]:
    lines = [
        (
            f"{record.repo_id}: {counts.get('commits', 0)} commit(s), "
            f"{counts.get('tracked_changes', 0)} tracked change(s), "
            f"{counts.get('untracked_files', 0)} new file(s)."
        )
    ]
    if domains:
        top = sorted(domains.items(), key=lambda item: (-item[1], item[0]))[:6]
        lines.append("Main repo areas: " + ", ".join(f"{name} ({count})" for name, count in top) + ".")
    if record.privacy_class == "sensitive":
        lines.append("Sensitive repo: only redacted counts and domain-level metadata are available.")
        return lines
    subjects = [str(item.get("subject") or "").strip() for item in commits if item.get("subject")]
    if subjects:
        lines.append("Latest commit topics: " + "; ".join(subjects[:5]) + ".")
    if record.privacy_class == "private":
        lines.append("Private repo: summary is local-only and must not be sent to external providers by default.")
    return lines


def _domain_counts(paths: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        domain = _domain_for_path(str(path or ""))
        counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _domain_source_paths(raw_snapshot: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in raw_snapshot.get("tracked_changes") or []:
        if isinstance(item, Mapping):
            path = _safe_rel_path(item.get("path") or "")
            if path:
                paths.append(path)
    for value in raw_snapshot.get("untracked_files") or []:
        path = _safe_rel_path(value)
        if path:
            paths.append(path)
    for item in raw_snapshot.get("recent_files") or []:
        if isinstance(item, Mapping):
            path = _safe_rel_path(item.get("path") or "")
            if path:
                paths.append(path)
    return paths[:MAX_PATHS * 3]


def _domain_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    if normalized.startswith("docs/"):
        return "docs"
    if normalized.startswith("tests/"):
        return "tests"
    if normalized.startswith("routes/"):
        return "api"
    if normalized.startswith("static/"):
        return "frontend"
    if "memory" in normalized or "raptor" in normalized:
        return "memory"
    if "repo" in normalized or "git" in normalized or "forge" in normalized:
        return "repo_control"
    if "nextcloud" in normalized:
        return "nextcloud"
    return "core"


def _redaction_policy(privacy: str) -> dict[str, Any]:
    if privacy == "public":
        return {
            "mode": "public_summary",
            "paths_included": True,
            "commit_subjects_included": True,
            "raw_diffs_included": False,
            "external_summary_allowed": True,
        }
    if privacy == "sensitive":
        return {
            "mode": "redacted_metadata_only",
            "paths_included": False,
            "commit_subjects_included": False,
            "raw_diffs_included": False,
            "external_summary_allowed": False,
        }
    return {
        "mode": "local_only_summary",
        "paths_included": True,
        "commit_subjects_included": True,
        "raw_diffs_included": False,
        "external_summary_allowed": False,
    }


def _fingerprint(snapshot: Mapping[str, Any]) -> str:
    stable = {
        "repo_id": snapshot.get("repo_id"),
        "privacy_class": snapshot.get("privacy_class"),
        "counts": snapshot.get("counts"),
        "domains": snapshot.get("domains"),
        "commits": snapshot.get("commits"),
        "tracked_changes": snapshot.get("tracked_changes"),
        "untracked_files": snapshot.get("untracked_files"),
        "recent_files": snapshot.get("recent_files"),
    }
    raw = json.dumps(stable, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assert_snapshot(snapshot: Mapping[str, Any]) -> None:
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != REPO_CHANGES_SCHEMA:
        raise RepoRecentMemoryError("snapshot must be a repo change snapshot")


def _safe_rel_path(value: Any) -> str:
    raw = _redact_text(value).replace("\\", "/").strip()
    if not raw or raw.startswith(("/", "~")) or _WINDOWS_PATH_RE.match(raw):
        return ""
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)[:240]


def _commit_ref(value: Any) -> str:
    text = _redact_text(value).strip()
    if re.fullmatch(r"[A-Fa-f0-9]{7,40}", text):
        return text.lower()
    return text[:12]


def _safe_repo_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    safe = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not safe:
        raise RepoRecentMemoryError("repo_id must produce a safe history path")
    return safe[:80]


def _redact_text(value: Any) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    return _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)
