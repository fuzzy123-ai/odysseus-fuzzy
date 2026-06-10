import hashlib
import json
import os
import re
from itertools import combinations
from typing import Any, Dict, List, Optional


_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
_TAG_RE = re.compile(r"(?<![\w/#])#([A-Za-z0-9][A-Za-z0-9_/-]*[A-Za-z0-9_-]?)")
_URL_RE = re.compile(r"https?://\S+")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
RELATIONSHIP_TYPES = {"manual", "relates_to", "depends_on", "blocks", "supports"}


def slugify_tag(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled"


def normalize_tag_name(value: str) -> str:
    parts = [slugify_tag(part) for part in str(value or "").strip().strip("#/").split("/") if part.strip()]
    return "/".join(parts) or "untitled"


def tag_color(tag: str) -> str:
    digest = hashlib.sha1(tag.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    return f"hsl({hue} 64% 48%)"


def file_tag_for_path(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return slugify_tag(stem)


def markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for file in files:
            if file.lower().endswith(".md"):
                abs_path = os.path.join(root, file)
                notes.append(os.path.relpath(abs_path, vault_dir).replace("\\", "/"))
    notes.sort(key=str.lower)
    return notes


def relationships_path(vault_dir: str) -> str:
    obsidian_dir = os.path.join(vault_dir, ".obsidian")
    os.makedirs(obsidian_dir, exist_ok=True)
    return os.path.join(obsidian_dir, "relationships.json")


def load_manual_relationships(vault_dir: str) -> List[Dict[str, Any]]:
    path = relationships_path(vault_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            return []
        return [_normalize_relationship(item) for item in data if isinstance(item, dict)]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def save_manual_relationships(vault_dir: str, relationships: List[Dict[str, Any]]) -> None:
    normalized = [_normalize_relationship(item) for item in relationships]
    with open(relationships_path(vault_dir), "w", encoding="utf-8") as fh:
        json.dump(normalized, fh, ensure_ascii=False, indent=2)


def _normalize_relationship(item: Dict[str, Any]) -> Dict[str, Any]:
    source = str(item.get("source", "")).replace("\\", "/").strip("/")
    target = str(item.get("target", "")).replace("\\", "/").strip("/")
    relation_type = str(item.get("type") or "manual").strip().lower()
    if relation_type not in RELATIONSHIP_TYPES:
        relation_type = "manual"
    reason = str(item.get("reason") or _relationship_label(relation_type)).strip()
    return {
        "source": source,
        "target": target,
        "type": relation_type,
        "reason": reason,
    }


def _relationship_key(item: Dict[str, Any]) -> tuple[str, str, str]:
    normalized = _normalize_relationship(item)
    return normalized["source"], normalized["target"], normalized["type"]


def _relationship_label(relation_type: str) -> str:
    return relation_type.replace("_", " ")


def add_manual_relationship(vault_dir: str, relationship: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_relationship(relationship)
    _validate_relationship_notes(vault_dir, normalized)
    relationships = load_manual_relationships(vault_dir)
    keys = {_relationship_key(item) for item in relationships}
    if _relationship_key(normalized) not in keys:
        relationships.append(normalized)
        save_manual_relationships(vault_dir, relationships)
    return normalized


def remove_manual_relationship(vault_dir: str, relationship: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    target_key = _relationship_key(relationship)
    relationships = load_manual_relationships(vault_dir)
    kept = []
    removed = None
    for item in relationships:
        if _relationship_key(item) == target_key and removed is None:
            removed = item
            continue
        kept.append(item)
    if removed is not None:
        save_manual_relationships(vault_dir, kept)
    return removed


def _validate_relationship_notes(vault_dir: str, relationship: Dict[str, Any]) -> None:
    existing = set(markdown_notes(vault_dir))
    if relationship["source"] not in existing:
        raise ValueError(f"Relationship source does not exist: {relationship['source']}")
    if relationship["target"] not in existing:
        raise ValueError(f"Relationship target does not exist: {relationship['target']}")
    if relationship["source"] == relationship["target"]:
        raise ValueError("Relationship source and target must differ")


def content_without_code(content: str) -> str:
    lines: List[str] = []
    in_fence = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lines.append(_INLINE_CODE_RE.sub("", line))
    return "\n".join(lines)


def extract_tags(content: str, path: str) -> Dict[str, Any]:
    searchable = _URL_RE.sub("", content_without_code(content))
    explicit = sorted({normalize_tag_name(match.group(1)) for match in _TAG_RE.finditer(searchable)})
    file_tag = file_tag_for_path(path)
    tags = sorted(set(explicit) | {file_tag})
    return {
        "path": path,
        "file_tag": file_tag,
        "explicit_tags": explicit,
        "tags": tags,
    }


def _normalize_link_target(raw: str, source_path: str) -> str:
    target = raw.strip().replace("\\", "/")
    if not target.lower().endswith(".md"):
        target += ".md"
    if "/" not in target and "/" in source_path:
        local = f"{source_path.rsplit('/', 1)[0]}/{target}"
        return local
    return target


def _read_note(vault_dir: str, path: str) -> str:
    with open(os.path.join(vault_dir, path), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def build_vault_index(vault_dir: str) -> Dict[str, Any]:
    notes: List[Dict[str, Any]] = []
    tag_map: Dict[str, Dict[str, Any]] = {}
    path_lookup: Dict[str, str] = {}
    stem_lookup: Dict[str, str] = {}

    for path in markdown_notes(vault_dir):
        path_lookup[path.lower()] = path
        stem_lookup[slugify_tag(os.path.splitext(os.path.basename(path))[0])] = path

    for path in markdown_notes(vault_dir):
        content = _read_note(vault_dir, path)
        tag_info = extract_tags(content, path)
        links = sorted({
            _normalize_link_target(match.group(1), path)
            for match in _WIKI_LINK_RE.finditer(content_without_code(content))
        })
        md_links = sorted({
            _normalize_link_target(match.group(1), path)
            for match in _MD_LINK_RE.finditer(content_without_code(content))
            if match.group(1).lower().endswith((".md", ".markdown"))
        })
        note = {
            "path": path,
            "title": os.path.splitext(os.path.basename(path))[0],
            "tags": tag_info["tags"],
            "explicit_tags": tag_info["explicit_tags"],
            "file_tag": tag_info["file_tag"],
            "links": sorted(set(links) | set(md_links)),
            "_content": content_without_code(content),
        }
        notes.append(note)
        for tag in tag_info["tags"]:
            entry = tag_map.setdefault(tag, {"name": tag, "color": tag_color(tag), "files": []})
            entry["files"].append(path)

    for tag in tag_map.values():
        tag["files"].sort(key=str.lower)

    edges = _build_edges(notes, path_lookup, stem_lookup, load_manual_relationships(vault_dir))
    public_notes = [{k: v for k, v in note.items() if not k.startswith("_")} for note in notes]
    return {
        "notes": public_notes,
        "tags": sorted(tag_map.values(), key=lambda item: item["name"]),
        "graph": {
            "nodes": _build_nodes(public_notes, tag_map),
            "edges": edges,
        },
    }


def _resolve_target(raw_target: str, path_lookup: Dict[str, str], stem_lookup: Dict[str, str]) -> Optional[str]:
    normalized = raw_target.replace("\\", "/").lower()
    if normalized in path_lookup:
        return path_lookup[normalized]
    return stem_lookup.get(slugify_tag(os.path.splitext(os.path.basename(raw_target))[0]))


def _add_edge(edges: Dict[tuple, Dict[str, Any]], source: str, target: str, kind: str, reason: str, weight: str) -> None:
    if source == target:
        return
    key = (source, target, kind, reason)
    edges[key] = {
        "source": source,
        "target": target,
        "type": kind,
        "reason": reason,
        "weight": weight,
    }


def _build_edges(
    notes: List[Dict[str, Any]],
    path_lookup: Dict[str, str],
    stem_lookup: Dict[str, str],
    manual_relationships: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    edge_map: Dict[tuple, Dict[str, Any]] = {}
    existing_paths = {note["path"] for note in notes}

    for note in notes:
        for link in note["links"]:
            target = _resolve_target(link, path_lookup, stem_lookup) or link
            _add_edge(edge_map, note["path"], target, "wiki_link", f"Links to {link}", "strong")

        content = note["_content"].lower()
        for tag, target_path in stem_lookup.items():
            if target_path == note["path"]:
                continue
            pattern = re.compile(rf"(?<![\w-]){re.escape(tag.replace('-', ' '))}(?![\w-])", re.IGNORECASE)
            stem = os.path.splitext(os.path.basename(target_path))[0]
            if pattern.search(content) or re.search(rf"(?<![\w-]){re.escape(stem.lower())}(?![\w-])", content):
                _add_edge(edge_map, note["path"], target_path, "filename_mention", f"Mentions {stem}", "medium")

    for left, right in combinations(notes, 2):
        shared = sorted((set(left["tags"]) & set(right["tags"])) - {left["file_tag"], right["file_tag"]})
        if shared:
            reason = "Shared tags: " + ", ".join(shared[:5])
            _add_edge(edge_map, left["path"], right["path"], "shared_tag", reason, "weak")
            _add_edge(edge_map, right["path"], left["path"], "shared_tag", reason, "weak")

    for relationship in manual_relationships or []:
        source = relationship.get("source", "")
        target = relationship.get("target", "")
        if source not in existing_paths or target not in existing_paths:
            continue
        kind = relationship.get("type", "manual")
        reason = relationship.get("reason") or _relationship_label(kind)
        _add_edge(edge_map, source, target, kind, reason, "manual")

    return sorted(
        edge_map.values(),
        key=lambda edge: (edge["source"].lower(), edge["target"].lower(), edge["type"]),
    )


def _build_nodes(notes: List[Dict[str, Any]], tag_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    folders = sorted({
        part
        for note in notes
        for part in _folder_paths(note["path"])
    }, key=str.lower)
    for folder in folders:
        nodes.append({"id": folder, "label": os.path.basename(folder), "type": "folder"})
    for note in notes:
        nodes.append({
            "id": note["path"],
            "label": note["title"],
            "type": "markdown",
            "tags": note["tags"],
        })
    return nodes


def _folder_paths(path: str) -> List[str]:
    parts = path.split("/")[:-1]
    folders: List[str] = []
    for index in range(len(parts)):
        folders.append("/".join(parts[: index + 1]))
    return folders


def graph_payload(vault_dir: str, focus: Optional[str] = None, tag: Optional[str] = None) -> Dict[str, Any]:
    index = build_vault_index(vault_dir)
    graph = index["graph"]
    if tag:
        normalized_tag = normalize_tag_name(tag)
        allowed = {note["path"] for note in index["notes"] if normalized_tag in note["tags"]}
        graph = _filter_graph(graph, allowed)
    if focus:
        allowed = {focus}
        allowed.update(edge["source"] for edge in graph["edges"] if edge["target"] == focus)
        allowed.update(edge["target"] for edge in graph["edges"] if edge["source"] == focus)
        graph = _filter_graph(graph, allowed)
    return {"graph": graph, "tags": index["tags"]}


def _filter_graph(graph: Dict[str, Any], allowed: set[str]) -> Dict[str, Any]:
    edges = [
        edge for edge in graph["edges"]
        if edge["source"] in allowed and edge["target"] in allowed
    ]
    node_ids = allowed | {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    nodes = [node for node in graph["nodes"] if node["id"] in node_ids]
    return {"nodes": nodes, "edges": edges}
