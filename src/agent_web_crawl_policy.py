"""Bounded website crawl policy for agent browser work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


class AgentWebCrawlPolicyError(ValueError):
    """Raised when a crawl policy is invalid."""


@dataclass(frozen=True, slots=True)
class CrawlDecision:
    allowed: bool
    reason: str
    normalized_url: str
    depth: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentWebCrawlPolicy:
    allowed_domains: tuple[str, ...]
    max_depth: int
    max_pages: int
    max_seconds: int
    external_network_go: bool = False
    allow_login_pages: bool = False
    respect_robots: bool = True
    max_bytes_per_page: int = 1_000_000

    @classmethod
    def create(
        cls,
        *,
        allowed_domains: Iterable[Any],
        max_depth: Any = 1,
        max_pages: Any = 20,
        max_seconds: Any = 120,
        external_network_go: bool = False,
        allow_login_pages: bool = False,
        respect_robots: bool = True,
        max_bytes_per_page: Any = 1_000_000,
    ) -> "AgentWebCrawlPolicy":
        domains = tuple(dict.fromkeys(_domain(domain) for domain in allowed_domains))
        if not domains:
            raise AgentWebCrawlPolicyError("allowed_domains must not be empty")
        depth = _bounded_int(max_depth, field_name="max_depth", minimum=0, maximum=5)
        pages = _bounded_int(max_pages, field_name="max_pages", minimum=1, maximum=500)
        seconds = _bounded_int(max_seconds, field_name="max_seconds", minimum=1, maximum=3600)
        max_bytes = _bounded_int(max_bytes_per_page, field_name="max_bytes_per_page", minimum=1024, maximum=10_000_000)
        return cls(
            allowed_domains=domains,
            max_depth=depth,
            max_pages=pages,
            max_seconds=seconds,
            external_network_go=bool(external_network_go),
            allow_login_pages=bool(allow_login_pages),
            respect_robots=bool(respect_robots),
            max_bytes_per_page=max_bytes,
        )

    def decide_url(self, url: Any, *, depth: Any, pages_seen: Any) -> CrawlDecision:
        normalized_url, host, path = _url_parts(url)
        warnings: list[str] = []
        current_depth = _bounded_int(depth, field_name="depth", minimum=0, maximum=10_000)
        seen = _bounded_int(pages_seen, field_name="pages_seen", minimum=0, maximum=10_000_000)
        if not self.external_network_go:
            return CrawlDecision(False, "external_network_go_required", normalized_url, current_depth, ())
        if not any(host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains):
            return CrawlDecision(False, "domain_not_allowed", normalized_url, current_depth, ())
        if current_depth > self.max_depth:
            return CrawlDecision(False, "depth_limit", normalized_url, current_depth, ())
        if seen >= self.max_pages:
            return CrawlDecision(False, "page_limit", normalized_url, current_depth, ())
        if _looks_like_login(path) and not self.allow_login_pages:
            return CrawlDecision(False, "login_page_blocked", normalized_url, current_depth, ())
        if not self.respect_robots:
            warnings.append("robots_policy_disabled")
        return CrawlDecision(True, "allowed", normalized_url, current_depth, tuple(warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_domains": self.allowed_domains,
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "max_seconds": self.max_seconds,
            "external_network_go": self.external_network_go,
            "allow_login_pages": self.allow_login_pages,
            "respect_robots": self.respect_robots,
            "max_bytes_per_page": self.max_bytes_per_page,
        }


def _domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.removeprefix("www.")
    if not text or "/" in text or "\\" in text or ".." in text:
        raise AgentWebCrawlPolicyError("domain is invalid")
    return text


def _url_parts(value: Any) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentWebCrawlPolicyError("url must be http(s) with a host")
    if parsed.username or parsed.password:
        raise AgentWebCrawlPolicyError("url must not contain credentials")
    return parsed.geturl(), parsed.hostname.lower().removeprefix("www."), parsed.path.lower()


def _looks_like_login(path: str) -> bool:
    return any(part in path for part in ("/login", "/signin", "/auth", "/account", "/admin"))


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentWebCrawlPolicyError(f"{field_name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise AgentWebCrawlPolicyError(f"{field_name} out of range")
    return number
