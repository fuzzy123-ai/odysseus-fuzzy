import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import vault_service
from .vault_model import extract_tags
from .vault_security import VaultSecurityError


PROVIDER_ID = "obsidian.vault_context"
SNIPPET_CHARS = 700


def retrieve_vault_context(owner: Optional[str], query: str, budget: int, mode: str = "chat") -> Dict[str, Any]:
    warnings: List[str] = []
    try:
        vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
    except VaultSecurityError:
        payload = {
            "structured_state": {},
            "snippets": [],
            "sources": [],
            "warnings": ["Obsidian vault is locked; no vault context was loaded."],
            "cache_key": "",
        }
        payload["cache_key"] = _cache_key(payload)
        return payload

    query_terms = _query_terms(query)
    max_chars = max(0, int(budget or 0) * 4)
    notes = []
    for path in vault_service.markdown_notes(vault_dir):
        try:
            content = vault_service.read_file(vault_dir, path)
        except OSError as exc:
            warnings.append(f"Could not read {path}: {exc}")
            continue
        frontmatter, body = parse_frontmatter(content)
        tags = extract_tags(content, path)["tags"]
        title = str(frontmatter.get("title") or _title_from_body(body) or _stem(path))
        score, reasons = _score_note(path, title, tags, frontmatter, body, query_terms)
        if score <= 0 and query_terms:
            continue
        notes.append({
            "path": path,
            "title": title,
            "tags": tags,
            "frontmatter": frontmatter,
            "body": body,
            "score": score,
            "reason": ", ".join(reasons) if reasons else "stable vault note",
        })

    notes.sort(key=lambda item: (-item["score"], item["path"].lower()))
    structured_state: Dict[str, Any] = {}
    snippets: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    used_chars = 0

    for note in notes:
        if note["frontmatter"]:
            structured_state[note["path"]] = note["frontmatter"]
        snippet = _best_snippet(note["body"], query_terms)
        if snippet and max_chars > 0:
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            snippet = snippet[: min(SNIPPET_CHARS, remaining)].rstrip()
            used_chars += len(snippet)
            snippets.append({
                "path": note["path"],
                "title": note["title"],
                "text": snippet,
                "untrusted": True,
            })
        sources.append({
            "path": note["path"],
            "title": note["title"],
            "tags": note["tags"],
            "score": note["score"],
            "reason": note["reason"],
        })

    payload = {
        "structured_state": structured_state,
        "snippets": snippets,
        "sources": sources,
        "warnings": warnings,
        "cache_key": "",
    }
    payload["cache_key"] = _cache_key(payload)
    return payload


def provider_spec() -> Dict[str, Any]:
    return {
        "id": PROVIDER_ID,
        "label": "Obsidian Vault Context",
        "priority": 50,
        "capabilities": ["chat", "agent", "vault", "markdown"],
        "retrieve": retrieve_vault_context,
    }


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    text = str(content or "")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[text.find("\n", end + 1) + 1:]
    return _parse_simple_yaml(raw), body


def _parse_simple_yaml(raw: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_key:
            result.setdefault(current_key, [])
            if isinstance(result[current_key], list):
                result[current_key].append(_clean_scalar(stripped[1:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        current_key = key
        value = value.strip()
        if value == "":
            result[key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [_clean_scalar(part.strip()) for part in value[1:-1].split(",") if part.strip()]
        else:
            result[key] = _clean_scalar(value)
    return result


def _clean_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _query_terms(query: str) -> List[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{1,}", str(query or ""))]


def _score_note(
    path: str,
    title: str,
    tags: List[str],
    frontmatter: Dict[str, Any],
    body: str,
    query_terms: List[str],
) -> Tuple[int, List[str]]:
    if not query_terms:
        return 1, ["no query filter"]
    haystacks = {
        "path": path.lower(),
        "title": title.lower(),
        "tags": " ".join(tags).lower(),
        "frontmatter": json.dumps(frontmatter, sort_keys=True).lower(),
        "body": body.lower(),
    }
    score = 0
    reasons: List[str] = []
    weights = {"title": 8, "path": 6, "tags": 5, "frontmatter": 4, "body": 1}
    for term in query_terms:
        for name, haystack in haystacks.items():
            if term in haystack:
                score += weights[name]
                reasons.append(f"{term} in {name}")
                break
    return score, sorted(set(reasons))


def _best_snippet(body: str, query_terms: List[str]) -> str:
    clean = str(body or "").strip()
    if not clean:
        return ""
    lower = clean.lower()
    positions = [lower.find(term) for term in query_terms if term and lower.find(term) >= 0]
    if not positions:
        return clean[:SNIPPET_CHARS]
    start = max(0, min(positions) - 180)
    return clean[start:start + SNIPPET_CHARS]


def _title_from_body(body: str) -> str:
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _cache_key(payload: Dict[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "cache_key"}
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
