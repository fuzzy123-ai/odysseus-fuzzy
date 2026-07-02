"""Redacted source inventory for website research runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse, urlunparse


WEB_RESEARCH_INVENTORY_SCHEMA = "odysseus.web_research_inventory.v1"


class WebResearchInventoryError(ValueError):
    """Raised when a source inventory item would be unsafe."""


@dataclass(frozen=True, slots=True)
class WebResearchSource:
    url: str
    canonical_url: str
    title: str
    content_hash: str
    text_chars: int
    heading_count: int
    link_count: int
    internal_links: tuple[str, ...]
    external_hosts: tuple[str, ...]
    gaps: tuple[str, ...]
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "content_hash": self.content_hash,
            "text_chars": self.text_chars,
            "heading_count": self.heading_count,
            "link_count": self.link_count,
            "internal_links": self.internal_links,
            "external_hosts": self.external_hosts,
            "gaps": self.gaps,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_unsafe_payload(payload)
        return payload


@dataclass(frozen=True, slots=True)
class WebResearchInventory:
    scope_id: str
    sources: tuple[WebResearchSource, ...]
    skipped: tuple[dict[str, str], ...]
    schema: str = WEB_RESEARCH_INVENTORY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "scope_id": self.scope_id,
            "source_count": len(self.sources),
            "sources": tuple(source.to_dict() for source in self.sources),
            "skipped": self.skipped,
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


def build_web_research_inventory(
    scope: Mapping[str, Any],
    pages: Iterable[Mapping[str, Any]],
) -> WebResearchInventory:
    """Build a metadata-only source inventory from fetched page summaries."""

    if not isinstance(scope, Mapping):
        raise WebResearchInventoryError("scope must be a mapping")
    _reject_unsafe_payload(scope)
    scope_id = _safe_label(scope.get("scope_id") or "", field="scope_id")
    allowed_domains = tuple(str(item).lower() for item in scope.get("allowed_domains") or ())
    if not allowed_domains:
        raise WebResearchInventoryError("scope allowed_domains must not be empty")
    max_pages = _safe_int(scope.get("max_pages"), default=50, minimum=1, maximum=500)
    sources: list[WebResearchSource] = []
    skipped: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    for page in pages:
        if len(sources) >= max_pages:
            skipped.append({"url": "", "reason": "page_limit"})
            break
        try:
            source = _source_from_page(page, allowed_domains=allowed_domains)
        except WebResearchInventoryError as exc:
            skipped.append({
                "url": _safe_url(page.get("url") if isinstance(page, Mapping) else ""),
                "reason": _safe_skip_reason(str(exc)),
            })
            continue
        if source.content_hash in seen_hashes:
            skipped.append({"url": source.url, "reason": "duplicate_content"})
            continue
        seen_hashes.add(source.content_hash)
        sources.append(source)
    inventory = WebResearchInventory(scope_id=scope_id, sources=tuple(sources), skipped=tuple(skipped))
    inventory.to_dict()
    return inventory


def _source_from_page(page: Mapping[str, Any], *, allowed_domains: tuple[str, ...]) -> WebResearchSource:
    if not isinstance(page, Mapping):
        raise WebResearchInventoryError("page must be a mapping")
    _reject_unsafe_payload(page, allow_text_fields=True)
    url = _safe_url(page.get("url"))
    canonical = _safe_url(page.get("canonical_url") or url)
    host = _host(canonical)
    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        raise WebResearchInventoryError("domain_not_allowed")
    text = str(page.get("text") or "")
    title = _safe_title(page.get("title") or "")
    headings = tuple(str(item or "").strip() for item in page.get("headings") or () if str(item or "").strip())
    links = tuple(str(item or "").strip() for item in page.get("links") or () if str(item or "").strip())
    internal, external = _classify_links(links, base_url=canonical, allowed_domains=allowed_domains)
    gaps: list[str] = []
    if not text.strip():
        gaps.append("empty_text")
    if not title:
        gaps.append("missing_title")
    if not headings:
        gaps.append("missing_headings")
    return WebResearchSource(
        url=url,
        canonical_url=canonical,
        title=title,
        content_hash=_content_hash(text),
        text_chars=len(text),
        heading_count=len(headings),
        link_count=len(links),
        internal_links=internal[:50],
        external_hosts=external[:50],
        gaps=tuple(gaps),
    )


def _classify_links(links: tuple[str, ...], *, base_url: str, allowed_domains: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    internal: list[str] = []
    external_hosts: list[str] = []
    for link in links:
        joined = _safe_url(urljoin(base_url, link))
        host = _host(joined)
        if any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
            if joined not in internal:
                internal.append(joined)
        elif host and host not in external_hosts:
            external_hosts.append(host)
    return tuple(internal), tuple(external_hosts)


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebResearchInventoryError("invalid_url")
    if parsed.username or parsed.password:
        raise WebResearchInventoryError("url_contains_credentials")
    host = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        raise WebResearchInventoryError("invalid_host")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, host, path, "", "", ""))


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _safe_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise WebResearchInventoryError("title_contains_secret_marker")
    return text[:160]


def _safe_skip_reason(reason: str) -> str:
    text = str(reason or "").lower()
    if "unsafe field" in text:
        return "unsafe_field"
    if "forbidden marker" in text or "secret" in text:
        return "forbidden_marker"
    if "host path" in text:
        return "host_path"
    if "credential" in text:
        return "credentials"
    if re.fullmatch(r"[a-z0-9_.:-]{1,80}", text):
        return text
    return "skipped"


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"^[A-Za-z0-9_.:-]{1,120}$", text):
        raise WebResearchInventoryError(f"{field} is invalid")
    return text


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _reject_unsafe_payload(payload: Mapping[str, Any], *, allow_text_fields: bool = False) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret"}
    if allow_text_fields:
        forbidden_keys = forbidden_keys - {"body"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise WebResearchInventoryError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value, allow_text_fields=allow_text_fields)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise WebResearchInventoryError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise WebResearchInventoryError("payload contains host path")
