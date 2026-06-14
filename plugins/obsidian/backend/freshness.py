import hashlib
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import vault_service
from .feature_flags import all_flags, is_enabled


EXCLUDED_STATUSES = {"stale", "superseded", "quarantined", "archived", "conflict"}
STATUS_ALIASES = {
    "unresolved conflict": "conflict",
    "unresolved_conflict": "conflict",
    "unresolved-conflict": "conflict",
    "conflicted": "conflict",
}
NEEDS_REVIEW_STATUSES = {"needs_review", "review", "draft", "todo"}
REVIEW_QUEUE_PREFIXES = ("AI Memory/Review Queue/", "AI Memory/Inbox/", "AI Memory/Quarantine/")


def _source_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _mtime(vault_dir: str, path: str) -> str:
    stat = os.stat(vault_service.secure_path(vault_dir, path))
    return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_days(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds() // 86400))


def _policy(path: str, frontmatter: Dict[str, Any]) -> str:
    explicit = str(frontmatter.get("freshness_policy") or "").strip()
    if explicit:
        return explicit
    normalized = path.replace("\\", "/")
    note_type = str(frontmatter.get("type") or "").strip().lower()
    if normalized.startswith("AI Memory/Canonical/") or note_type == "canonical":
        return "architecture_decision"
    if "roadmap" in normalized.lower() or note_type == "roadmap":
        return "roadmap"
    if note_type in {"session_log", "daily", "log"}:
        return "session_log"
    if note_type == "preference":
        return "preference"
    return "implementation_status"


def _record(path: str, frontmatter: Dict[str, Any], content: str, mtime: str, status: str, reason: str, channel: str) -> Dict[str, Any]:
    return {
        "path": path,
        "status": status,
        "channel": channel,
        "policy": _policy(path, frontmatter),
        "reason": reason,
        "source_hash": _source_hash(content),
        "source_mtime": mtime,
        "frontmatter": {
            key: frontmatter.get(key)
            for key in ("status", "type", "scope", "confidence", "updated", "last_verified_at", "superseded_by")
            if key in frontmatter
        },
    }


def _classify(path: str, frontmatter: Dict[str, Any], content: str, source_mtime: str) -> Dict[str, Any]:
    raw_status = str(frontmatter.get("status") or "active").strip().lower()
    raw_status = STATUS_ALIASES.get(raw_status, raw_status)
    policy = _policy(path, frontmatter)
    verified = _parse_date(frontmatter.get("last_verified_at") or frontmatter.get("updated"))
    age = _age_days(verified)
    normalized = path.replace("\\", "/")

    if raw_status in EXCLUDED_STATUSES:
        return _record(path, frontmatter, content, source_mtime, raw_status, f"Frontmatter status is {raw_status}.", "quarantined")
    if raw_status in NEEDS_REVIEW_STATUSES:
        return _record(path, frontmatter, content, source_mtime, "needs_review", f"Frontmatter status is {raw_status}.", "needs_review")
    if normalized.startswith(REVIEW_QUEUE_PREFIXES):
        return _record(path, frontmatter, content, source_mtime, "needs_review", "Note lives in a review, inbox, or quarantine folder.", "needs_review")
    if frontmatter.get("confidence") == "low":
        return _record(path, frontmatter, content, source_mtime, "needs_review", "Low confidence frontmatter.", "needs_review")
    if policy == "session_log":
        return _record(path, frontmatter, content, source_mtime, "archived", "Session logs are traceability records, not default truth.", "quarantined")
    if policy in {"implementation_status", "roadmap"} and age is None:
        return _record(path, frontmatter, content, source_mtime, "needs_review", "No updated or last_verified_at date for a volatile policy.", "needs_review")
    if policy == "implementation_status" and age is not None and age > 45:
        return _record(path, frontmatter, content, source_mtime, "stale", "Implementation status is older than 45 days.", "quarantined")
    if policy == "roadmap" and age is not None and age > 90:
        return _record(path, frontmatter, content, source_mtime, "needs_review", "Roadmap note is older than 90 days.", "needs_review")
    return _record(path, frontmatter, content, source_mtime, "active", "Freshness policy passed.", "current")


def audit_knowledge(vault_dir: str) -> Dict[str, Any]:
    channels: Dict[str, List[Dict[str, Any]]] = {
        "current": [],
        "needs_review": [],
        "conflicts": [],
        "quarantined": [],
    }
    warnings: List[str] = []
    for path in vault_service.markdown_notes(vault_dir):
        try:
            content = vault_service.read_file(vault_dir, path)
            frontmatter, _body = vault_service.parse_frontmatter(content)
            record = _classify(path, frontmatter, content, _mtime(vault_dir, path))
        except OSError as exc:
            warnings.append(f"Could not audit {path}: {exc}")
            continue
        if record["status"] == "conflict":
            channels["conflicts"].append(record)
        elif record["channel"] == "current":
            channels["current"].append(record)
        elif record["channel"] == "needs_review":
            channels["needs_review"].append(record)
        else:
            channels["quarantined"].append(record)
    for values in channels.values():
        values.sort(key=lambda item: item["path"].lower())
    status_counts = Counter(item["status"] for values in channels.values() for item in values)
    return {
        "enabled": is_enabled("obsidian_freshness_gate_enabled"),
        "flags": all_flags(),
        "channels": channels,
        "summary": {
            "total": sum(len(values) for values in channels.values()),
            "current": len(channels["current"]),
            "needs_review": len(channels["needs_review"]),
            "conflicts": len(channels["conflicts"]),
            "quarantined": len(channels["quarantined"]),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "warnings": warnings,
    }


def quarantine_list(vault_dir: str) -> Dict[str, Any]:
    audit = audit_knowledge(vault_dir)
    items = audit["channels"]["quarantined"] + audit["channels"]["conflicts"]
    items.sort(key=lambda item: item["path"].lower())
    return {
        "enabled": audit["enabled"],
        "items": items,
        "summary": {
            "total": len(items),
            "by_status": dict(sorted(Counter(item["status"] for item in items).items())),
        },
        "warnings": audit["warnings"],
    }
