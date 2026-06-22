import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_ALLOWLIST = ("specs/roadmaps", "docs/plans")
DEFAULT_EXTENSIONS = (".json", ".md", ".markdown")
DEFAULT_PREVIEW_CHARS = 240
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
)


def build_planning_source_inventory(
    repo_root: str,
    *,
    allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> Dict[str, Any]:
    """Return a read-only inventory of repo planning documents.

    Stable IDs are path based, content hashes are content based, and previews
    are bounded/redacted so the payload can be used as memory-ingestion input.
    """
    root = os.path.abspath(repo_root)
    allowed_roots = [_safe_join(root, item) for item in allowlist]
    sources: List[Dict[str, Any]] = []
    for allowed in allowed_roots:
        if not os.path.isdir(allowed):
            continue
        for dirpath, dirnames, filenames in os.walk(allowed):
            dirnames[:] = [name for name in dirnames if name not in {".git", "__pycache__"}]
            for filename in filenames:
                ext = os.path.splitext(filename.lower())[1]
                if ext not in DEFAULT_EXTENSIONS:
                    continue
                abs_path = os.path.join(dirpath, filename)
                rel_path = _normalize_path(os.path.relpath(abs_path, root))
                sources.append(_source_record(abs_path, rel_path, preview_chars=preview_chars))
    sources.sort(key=lambda item: item["path"].lower())
    by_kind: Dict[str, int] = {}
    by_extension: Dict[str, int] = {}
    for item in sources:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_extension[item["extension"]] = by_extension.get(item["extension"], 0) + 1
    preview_limit = max(0, int(preview_chars or 0))
    return {
        "schema": "planning-source-inventory-v1",
        "read_only": True,
        "writes_supported": False,
        "allowlist": [_normalize_path(os.path.relpath(path, root)) for path in allowed_roots],
        "preview_chars": preview_limit,
        "summary": {
            "total_sources": len(sources),
            "by_kind": dict(sorted(by_kind.items())),
            "by_extension": dict(sorted(by_extension.items())),
            "stable_ids": len({item["source_id"] for item in sources}) == len(sources),
            "content_hashes": len([item for item in sources if item["source_hash"]]),
            "raw_content_bounded": all(len(item.get("preview", "")) <= preview_limit for item in sources),
        },
        "sources": sources,
    }


def diff_planning_source_inventories(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two inventory payloads using stable source IDs."""
    before = _sources_by_id(previous)
    after = _sources_by_id(current)
    created = sorted(source_id for source_id in after if source_id not in before)
    deleted = sorted(source_id for source_id in before if source_id not in after)
    changed = sorted(
        source_id
        for source_id in before.keys() & after.keys()
        if before[source_id].get("source_hash") != after[source_id].get("source_hash")
    )
    unchanged = sorted(
        source_id
        for source_id in before.keys() & after.keys()
        if before[source_id].get("source_hash") == after[source_id].get("source_hash")
    )
    return {
        "schema": "planning-source-inventory-diff-v1",
        "read_only": True,
        "writes_supported": False,
        "created": created,
        "changed": changed,
        "deleted": deleted,
        "unchanged": unchanged,
        "summary": {
            "created": len(created),
            "changed": len(changed),
            "deleted": len(deleted),
            "unchanged": len(unchanged),
        },
    }


def _source_record(abs_path: str, rel_path: str, *, preview_chars: int) -> Dict[str, Any]:
    raw = _read_text(abs_path)
    parsed_json: Optional[Dict[str, Any]] = None
    if rel_path.lower().endswith(".json"):
        try:
            loaded = json.loads(raw)
            parsed_json = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            parsed_json = None
    stat = os.stat(abs_path)
    title, plan_id = _title_and_plan_id(rel_path, raw, parsed_json)
    dependencies, source_refs = _dependency_hints(parsed_json)
    ext = os.path.splitext(rel_path.lower())[1]
    return {
        "source_id": _stable_source_id(rel_path),
        "path": rel_path,
        "kind": _classify_kind(rel_path, parsed_json),
        "extension": ext.lstrip("."),
        "title": title,
        "plan_id": plan_id,
        "source_hash": _sha256(raw),
        "size_bytes": int(stat.st_size),
        "mtime": _mtime_iso(stat.st_mtime),
        "dependency_hints": dependencies,
        "source_refs": source_refs,
        "preview": _bounded_preview(raw, preview_chars),
        "repo_relative": True,
        "absolute_path_recorded": False,
    }


def _safe_join(root: str, rel_path: str) -> str:
    candidate = os.path.abspath(os.path.join(root, rel_path.replace("/", os.sep)))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ValueError(f"Planning source path escapes repo root: {rel_path}")
    return candidate


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _stable_source_id(path: str) -> str:
    normalized = _normalize_path(path).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"repo-plan:{digest}"


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _mtime_iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _title_and_plan_id(path: str, raw: str, parsed: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if parsed:
        title = str(parsed.get("title") or parsed.get("name") or parsed.get("plan_id") or "").strip()
        plan_id = str(parsed.get("plan_id") or parsed.get("id") or "").strip()
        return title or _stem(path), plan_id
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or _stem(path), ""
    return _stem(path), ""


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _classify_kind(path: str, parsed: Optional[Dict[str, Any]]) -> str:
    normalized = _normalize_path(path).lower()
    if normalized.startswith("specs/roadmaps/"):
        return "roadmap_json" if normalized.endswith(".json") else "roadmap_doc"
    if normalized.startswith("docs/plans/"):
        return "planning_doc_json" if normalized.endswith(".json") else "planning_doc"
    if parsed and parsed.get("plan_id"):
        return "planning_json"
    return "planning_source"


def _dependency_hints(parsed: Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    if not parsed:
        return [], []
    dependencies: List[str] = []
    source_refs: List[str] = []
    for value in _walk_dict_values(parsed, {"depends_on", "unlocks", "parent_plan_id"}):
        if isinstance(value, list):
            dependencies.extend(str(item) for item in value if str(item or "").strip())
        elif str(value or "").strip():
            dependencies.append(str(value))
    for value in _walk_dict_values(parsed, {"source_refs"}):
        if isinstance(value, list):
            source_refs.extend(str(item) for item in value if str(item or "").strip())
    return sorted(set(dependencies))[:50], sorted(set(source_refs))[:50]


def _walk_dict_values(value: Any, keys: set[str]) -> Iterable[Any]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in keys:
                yield nested
            yield from _walk_dict_values(nested, keys)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dict_values(nested, keys)


def _bounded_preview(raw: str, preview_chars: int) -> str:
    limit = max(0, int(preview_chars or 0))
    if limit <= 0:
        return ""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _sources_by_id(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sources = payload.get("sources") if isinstance(payload, dict) else []
    return {
        str(item.get("source_id")): item
        for item in sources or []
        if isinstance(item, dict) and str(item.get("source_id") or "").strip()
    }
