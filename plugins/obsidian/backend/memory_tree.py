import hashlib
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import vault_service
from .feature_flags import all_flags, is_enabled
from .vault_model import extract_tags


SOMT_INDEX_PATH = ".obsidian/odysseus/somt/index.json"
SOMT_REPORT_PATH = ".obsidian/odysseus/somt/report.json"
CANONICAL_PREFIX = "AI Memory/Canonical/"
DERIVED_PREFIXES = (
    ".obsidian/",
    "AI Memory/Summaries/",
    "AI Memory/Clusters/",
    "AI Memory/Tree/",
)
STATUS_ALIASES = {
    "unresolved conflict": "conflict",
    "unresolved_conflict": "conflict",
    "unresolved-conflict": "conflict",
    "conflicted": "conflict",
}
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")


def _utc_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_hash(content: str) -> str:
    digest = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _title(path: str, body: str, frontmatter: Dict[str, Any]) -> str:
    if frontmatter.get("title"):
        return str(frontmatter["title"])
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return os.path.splitext(os.path.basename(path))[0]


def _kind(path: str, frontmatter: Dict[str, Any]) -> str:
    explicit = str(frontmatter.get("type") or "").strip().lower()
    if explicit:
        return explicit
    normalized = path.replace("\\", "/")
    if normalized.startswith(CANONICAL_PREFIX):
        return "canonical"
    if "/" in normalized:
        return "topic"
    return "episode"


def _truth_level(path: str, frontmatter: Dict[str, Any]) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith(CANONICAL_PREFIX) or frontmatter.get("type") == "canonical":
        return "canonical"
    if frontmatter.get("confidence") == "high":
        return "high"
    if frontmatter.get("confidence") == "low":
        return "low"
    return "working"


def _status(frontmatter: Dict[str, Any]) -> str:
    raw = str(frontmatter.get("status") or "active").strip().lower()
    raw = STATUS_ALIASES.get(raw, raw)
    if raw in {"active", "needs_review", "stale", "superseded", "conflict", "quarantined", "archived"}:
        return raw
    if raw in {"draft", "review", "todo"}:
        return "needs_review"
    return "active"


def _parent_id(path: str, tags: List[str]) -> Optional[str]:
    parts = path.replace("\\", "/").split("/")
    if len(parts) > 1:
        return "branch:" + "/".join(parts[:-1]).lower()
    project_tags = [tag for tag in tags if tag.startswith("project/")]
    if project_tags:
        return "tag:" + project_tags[0]
    return None


def _node_id(path: str) -> str:
    return "note:" + path.replace("\\", "/").lower()


def _read_notes(vault_dir: str) -> tuple[List[Dict[str, Any]], List[str]]:
    notes: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for path in vault_service.markdown_notes(vault_dir):
        normalized = path.replace("\\", "/")
        if normalized.startswith(DERIVED_PREFIXES):
            continue
        try:
            content = vault_service.read_file(vault_dir, path)
            frontmatter, body = vault_service.parse_frontmatter(content)
            tags = extract_tags(content, path)["tags"]
            stat = os.stat(vault_service.secure_path(vault_dir, path))
        except OSError as exc:
            warnings.append(f"Could not read {path}: {exc}")
            continue
        notes.append({
            "id": _node_id(path),
            "parent_id": _parent_id(path, tags),
            "kind": _kind(path, frontmatter),
            "title": _title(path, body, frontmatter),
            "summary": (body.strip().splitlines() or [""])[0][:220],
            "source_paths": [path],
            "child_ids": [],
            "tags": tags,
            "status": _status(frontmatter),
            "truth_level": _truth_level(path, frontmatter),
            "confidence": frontmatter.get("confidence", "medium"),
            "freshness_policy": frontmatter.get("freshness_policy") or _default_policy(path, frontmatter),
            "last_verified_at": frontmatter.get("last_verified_at") or frontmatter.get("updated") or "",
            "indexed_at": _utc_from_timestamp(stat.st_mtime),
            "source_hashes": {path: _source_hash(content)},
            "superseded_by": frontmatter.get("superseded_by"),
            "warnings": [],
            "_body": body,
            "_frontmatter": frontmatter,
            "_path": path,
        })
    return notes, warnings


def _default_policy(path: str, frontmatter: Dict[str, Any]) -> str:
    if path.replace("\\", "/").startswith(CANONICAL_PREFIX) or frontmatter.get("type") == "canonical":
        return "architecture_decision"
    if frontmatter.get("type") in {"session_log", "daily", "log"}:
        return "session_log"
    if frontmatter.get("scope") == "preference" or frontmatter.get("type") == "preference":
        return "preference"
    if frontmatter.get("status") in {"roadmap", "planned"} or "roadmap" in path.lower():
        return "roadmap"
    return "implementation_status"


def _branch_candidates(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    folders: Dict[str, List[str]] = defaultdict(list)
    tags: Dict[str, List[str]] = defaultdict(list)
    for note in notes:
        path = note["_path"]
        folder = "/".join(path.split("/")[:-1]) or "/"
        folders[folder].append(path)
        for tag in note["tags"]:
            if tag.startswith(("project/", "type/", "status/")):
                tags[tag].append(path)
    branches = [
        {"id": f"folder:{folder}", "kind": "folder", "title": folder, "source_paths": paths, "count": len(paths)}
        for folder, paths in sorted(folders.items())
        if len(paths) >= 2
    ]
    branches.extend(
        {"id": f"tag:{tag}", "kind": "tag", "title": tag, "source_paths": paths, "count": len(paths)}
        for tag, paths in sorted(tags.items())
        if len(paths) >= 2
    )
    return sorted(branches, key=lambda item: (-item["count"], item["title"].lower()))


def _issues(notes: List[Dict[str, Any]], graph_edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    by_title: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_hash: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    linked = set()
    for edge in graph_edges:
        linked.add(edge.get("source", ""))
        linked.add(edge.get("target", ""))
    for note in notes:
        by_title[note["title"].strip().lower()].append(note)
        source_hash = next(iter(note["source_hashes"].values()))
        by_hash[source_hash].append(note)
        if note["status"] != "active":
            issues.append({
                "type": "non_active_status",
                "severity": "warning",
                "path": note["_path"],
                "status": note["status"],
                "reason": "Note frontmatter keeps this note out of default-trust memory.",
            })
        if not note["_frontmatter"]:
            issues.append({
                "type": "missing_frontmatter",
                "severity": "info",
                "path": note["_path"],
                "reason": "No YAML frontmatter; policy and freshness are inferred.",
            })
        if note["_path"] not in linked and "/" not in note["_path"]:
            issues.append({
                "type": "loose_note",
                "severity": "info",
                "path": note["_path"],
                "reason": "Top-level note has no graph links in the derived graph.",
            })
    for title, group in by_title.items():
        if title and len(group) > 1:
            issues.append({
                "type": "duplicate_title",
                "severity": "warning",
                "title": title,
                "source_paths": [note["_path"] for note in group],
                "reason": "Multiple notes share the same title.",
            })
    for source_hash, group in by_hash.items():
        if len(group) > 1:
            issues.append({
                "type": "duplicate_content",
                "severity": "warning",
                "source_hash": source_hash,
                "source_paths": [note["_path"] for note in group],
                "reason": "Multiple notes have identical content hashes.",
            })
    return sorted(issues, key=lambda item: (item.get("severity", ""), item.get("path", ""), item.get("type", "")))


def _graph_edges(notes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    paths = {note["_path"] for note in notes}
    stems = {os.path.splitext(os.path.basename(path))[0].lower(): path for path in paths}
    edges: List[Dict[str, str]] = []
    for note in notes:
        for target in _link_targets(note["_body"], note["_path"], stems):
            if target in paths:
                edges.append({"source": note["_path"], "target": target, "type": "wiki_link"})
    return edges


def _link_targets(body: str, source_path: str, stems: Dict[str, str]) -> List[str]:
    targets: List[str] = []
    for raw in WIKI_LINK_RE.findall(body or ""):
        normalized = raw.strip().replace("\\", "/")
        if not normalized.lower().endswith(".md"):
            normalized += ".md"
        targets.append(stems.get(os.path.splitext(os.path.basename(normalized))[0].lower(), normalized))
    for raw in MD_LINK_RE.findall(body or ""):
        normalized = raw.strip().replace("\\", "/")
        if normalized.lower().endswith((".md", ".markdown")):
            targets.append(normalized)
    return sorted(set(targets))


def analyze_memory_tree(vault_dir: str, *, limit: Optional[int] = None) -> Dict[str, Any]:
    notes, warnings = _read_notes(vault_dir)
    if limit is not None:
        notes = notes[: max(0, int(limit))]
    graph_edges = _graph_edges(notes)
    status_counts = Counter(note["status"] for note in notes)
    truth_counts = Counter(note["truth_level"] for note in notes)
    public_nodes = [{k: v for k, v in note.items() if not k.startswith("_")} for note in notes]
    return {
        "enabled": is_enabled("obsidian_somt_enabled"),
        "storage": {
            "index": SOMT_INDEX_PATH,
            "report": SOMT_REPORT_PATH,
            "writes_performed": False,
        },
        "flags": all_flags(),
        "summary": {
            "total_notes": len(notes),
            "status_counts": dict(sorted(status_counts.items())),
            "truth_level_counts": dict(sorted(truth_counts.items())),
            "branch_candidates": len(_branch_candidates(notes)),
        },
        "nodes": public_nodes,
        "branches": _branch_candidates(notes),
        "issues": _issues(notes, graph_edges),
        "warnings": warnings,
    }


def memory_tree_status(vault_dir: str) -> Dict[str, Any]:
    report = analyze_memory_tree(vault_dir, limit=200)
    return {
        "enabled": report["enabled"],
        "storage": report["storage"],
        "summary": report["summary"],
        "issue_counts": dict(Counter(issue["type"] for issue in report["issues"])),
        "flags": report["flags"],
    }
