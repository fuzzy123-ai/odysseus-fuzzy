import hashlib
import json
import os
import re
from copy import deepcopy
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
_SUPERSEDED_STATUSES = frozenset({"superseded", "historical", "archived", "deprecated", "retired", "obsolete"})


def build_planning_source_inventory(
    repo_root: str,
    *,
    allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    migration_batch: Optional[Dict[str, Any]] = None,
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
                try:
                    _safe_join(root, rel_path)
                except ValueError:
                    # A file symlink must not turn the allowlisted walk into
                    # an authority over bytes outside the repository root.
                    continue
                sources.append(_source_record(abs_path, rel_path, preview_chars=preview_chars))
    sources.sort(key=lambda item: item["path"].lower())
    by_kind: Dict[str, int] = {}
    by_extension: Dict[str, int] = {}
    for item in sources:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_extension[item["extension"]] = by_extension.get(item["extension"], 0) + 1
    preview_limit = max(0, int(preview_chars or 0))
    inventory = {
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
    return project_planning_migration_inventory(inventory, migration_batch) if migration_batch is not None else inventory


def migration_primary_digest_matches(item: Dict[str, Any], source_hash: str) -> bool:
    """Match exact bytes or an already declared frozen metadata variant.

    Frozen command identity retains its historical digest.  Only the existing
    immutable member declaration and predeclared raw-byte variants may bridge
    that digest to current archival metadata; arbitrary content never can.
    """
    if type(item) is not dict or type(source_hash) is not str:
        return False
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", source_hash):
        return False
    expected = _manifest_sha256(item.get("sha256"))
    if expected is None:
        return False
    from src.planning_roadmap_migration import FROZEN_MEMBERS, FROZEN_MEMBER_SHA256_VARIANTS
    for path, adapter, roadmap_id, blob, digest in FROZEN_MEMBERS:
        if item.get("path") == path:
            return (
                item.get("role") == "primary"
                and item.get("adapter") == adapter
                and item.get("roadmap_id") == roadmap_id
                and item.get("blob") == blob
                and item.get("sha256") == digest
                and source_hash[7:] in FROZEN_MEMBER_SHA256_VARIANTS[path]
            )
    return source_hash == expected


def project_planning_migration_inventory(
    inventory: Dict[str, Any], migration_batch: Dict[str, Any]
) -> Dict[str, Any]:
    """Return a detached read-only classification projection for one batch.

    This never changes source bytes.  Supporting-relation drift is represented
    independently and cannot promote, remove, or otherwise alter a Planning
    head.
    """
    if type(inventory) is not dict or type(migration_batch) is not dict:
        raise ValueError("inventory and migration batch must be exact objects")
    required = {
        "batch_id", "manifest_digest", "owner_id", "project_id", "state",
        "source_items", "supporting_relations", "expected_subjects", "accepted_subjects",
        "normalized_payloads", "prepared_receipt_id", "accepted_receipt_id", "terminal_receipt_id",
    }
    prior_modern = required | {"census_observation", "structural_census", "principal_identity"}
    current_modern = prior_modern | {"manifest_identity"}
    if set(migration_batch) not in (required, prior_modern, current_modern) or migration_batch["state"] not in {
        "prepared", "accepted", "aborted", "rolled_back"
    }:
        raise ValueError("migration batch shape is invalid")
    if "manifest_identity" in migration_batch:
        from src.planning_roadmap_migration import resolve_migration_manifest_identity
        try:
            resolve_migration_manifest_identity(migration_batch["manifest_identity"])
        except (TypeError, ValueError):
            raise ValueError("migration manifest identity shape is invalid") from None
    projected = deepcopy(inventory)
    sources = projected.get("sources")
    if type(sources) is not list or any(type(item) is not dict for item in sources):
        raise ValueError("inventory sources are invalid")
    primary_items = [item for item in migration_batch["source_items"]
                     if type(item) is dict and type(item.get("path")) is str]
    relation_items = [item for item in migration_batch["supporting_relations"]
                      if type(item) is dict and type(item.get("path")) is str]
    primary_paths = [item["path"] for item in primary_items]
    relation_paths = [item["path"] for item in relation_items]
    if len(primary_paths) != len(set(primary_paths)) or len(relation_paths) != len(set(relation_paths)):
        raise ValueError("migration source paths must be unique")
    primary = {item["path"]: item for item in primary_items}
    relations = {item["path"]: item for item in relation_items}
    state = migration_batch["state"]
    present_paths = {item.get("path") for item in sources if type(item.get("path")) is str}
    relation_valid = all(path in present_paths for path in relations)
    primary_valid = all(path in present_paths for path in primary)
    migrated_count = 0
    for item in sources:
        path = item.get("path")
        if path in primary:
            inventory_hash = item.get("source_hash")
            if not migration_primary_digest_matches(primary[path], inventory_hash):
                primary_valid = False
                item["authority_disposition"] = "primary_source_drift"
            elif state == "accepted":
                item["authority_disposition"] = "migrated_read_only"
                migrated_count += 1
            else:
                item["authority_disposition"] = "active_import_candidate"
        elif path in relations:
            expected = "sha256:" + str(relations[path].get("sha256", ""))
            if item.get("source_hash") == expected:
                item["authority_disposition"] = "supporting_reference"
            else:
                item["authority_disposition"] = "supporting_relation_invalid"
                relation_valid = False
        elif str(path or "").lower().endswith((".md", ".markdown")):
            item["authority_disposition"] = "historical_evidence"
    counts: Dict[str, int] = {}
    for item in sources:
        disposition = item.get("authority_disposition")
        if type(disposition) is str:
            counts[disposition] = counts.get(disposition, 0) + 1
    projected["migration_projection"] = {
        "schema": "odysseus.harbour.planning_migration_inventory.v1",
        "batch_id": migration_batch["batch_id"],
        "batch_state": state,
        "migrated_read_only_count": migrated_count,
        "primary_source_valid": primary_valid,
        "supporting_relation_valid": relation_valid,
        "authority_disposition_counts": dict(sorted(counts.items())),
        "read_only": True,
        "writes_supported": False,
    }
    return projected


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
    raw_bytes = _read_bytes(abs_path)
    stat = os.stat(abs_path)
    return build_planning_source_record_from_bytes(
        rel_path,
        raw_bytes,
        preview_chars=preview_chars,
        size_bytes=int(stat.st_size),
        mtime=stat.st_mtime,
    )


def build_planning_source_record_from_bytes(
    rel_path: str,
    raw_bytes: bytes,
    *,
    preview_chars: int,
    size_bytes: int,
    mtime: float,
) -> Dict[str, Any]:
    """Build the established inventory record from one caller-owned snapshot."""
    if type(raw_bytes) is not bytes:
        raise TypeError("raw_bytes must be exact bytes")
    normalized_path = _normalize_path(rel_path)
    raw = raw_bytes.decode("utf-8", errors="replace")
    parsed_json: Optional[Dict[str, Any]] = None
    if normalized_path.lower().endswith(".json"):
        parsed_json = _load_json_object(raw)
    title, plan_id = _title_and_plan_id(normalized_path, raw, parsed_json)
    dependencies, source_refs = _dependency_hints(parsed_json)
    ext = os.path.splitext(normalized_path.lower())[1]
    return {
        "source_id": planning_source_id(normalized_path),
        "path": normalized_path,
        "kind": _classify_kind(normalized_path, parsed_json),
        "extension": ext.lstrip("."),
        "title": title,
        "plan_id": plan_id,
        "source_hash": _sha256_bytes(raw_bytes),
        "size_bytes": int(size_bytes),
        "mtime": _mtime_iso(float(mtime)),
        "dependency_hints": dependencies,
        "source_refs": source_refs,
        "authority_disposition": _authority_disposition(normalized_path, parsed_json),
        "preview": _bounded_preview(raw, preview_chars),
        "repo_relative": True,
        "absolute_path_recorded": False,
    }


def _load_json_object(raw: str) -> Optional[Dict[str, Any]]:
    def reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        loaded = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return loaded if type(loaded) is dict else None


def _authority_disposition(path: str, parsed: Optional[Dict[str, Any]]) -> str:
    normalized = _normalize_path(path).lower()
    if not normalized.startswith("specs/roadmaps/") or not normalized.endswith(".json"):
        return "supporting_plan_source"
    if parsed is None:
        return "invalid_plan_source"

    if type(parsed.get("plan_id")) is not str or not parsed["plan_id"].strip():
        return "invalid_plan_source"
    if "status" in parsed:
        status = parsed["status"]
        if type(status) is not str or not status.strip():
            return "invalid_plan_source"
        status = status.strip().lower()
        if status in _SUPERSEDED_STATUSES:
            return "superseded_plan_source"
    if "superseded_by" in parsed:
        successor = parsed["superseded_by"]
        if type(successor) is not str or not successor.strip():
            return "invalid_plan_source"
        return "superseded_plan_source"
    return "current_source_of_truth"


def _safe_join(root: str, rel_path: str) -> str:
    canonical_root = os.path.realpath(os.path.abspath(root))
    candidate = os.path.realpath(os.path.abspath(os.path.join(root, rel_path.replace("/", os.sep))))
    if os.path.normcase(candidate) != os.path.normcase(canonical_root) and not os.path.normcase(candidate).startswith(os.path.normcase(canonical_root + os.sep)):
        raise ValueError(f"Planning source path escapes repo root: {rel_path}")
    return candidate


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def planning_source_id(path: str) -> str:
    """Return the established path-derived Planning source identifier."""
    normalized = _normalize_path(path).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"repo-plan:{digest}"


def _stable_source_id(path: str) -> str:
    return planning_source_id(path)


def _sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _manifest_sha256(value: Any) -> Optional[str]:
    if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        return None
    return "sha256:" + value.lower()


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
