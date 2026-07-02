"""Bounded website research scope planning."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from src.agent_web_crawl_policy import AgentWebCrawlPolicy


WEB_RESEARCH_SCOPE_SCHEMA = "odysseus.web_research_scope.v1"


class WebResearchScopeError(ValueError):
    """Raised when a website research scope would be unsafe."""


@dataclass(frozen=True, slots=True)
class WebResearchScope:
    scope_id: str
    target_ref: str
    seed_url: str
    allowed_domains: tuple[str, ...]
    max_pages: int
    max_depth: int
    rate_limit_seconds: int
    respect_robots: bool
    allow_login_pages: bool
    external_network_go: bool
    gates_required: tuple[str, ...]
    raw_content_persistence: bool = False
    schema: str = WEB_RESEARCH_SCOPE_SCHEMA

    def to_policy(self) -> AgentWebCrawlPolicy:
        return AgentWebCrawlPolicy.create(
            allowed_domains=self.allowed_domains,
            max_depth=self.max_depth,
            max_pages=self.max_pages,
            max_seconds=max(self.max_pages * max(self.rate_limit_seconds, 1), 30),
            external_network_go=self.external_network_go,
            allow_login_pages=self.allow_login_pages,
            respect_robots=self.respect_robots,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "scope_id": self.scope_id,
            "target_ref": self.target_ref,
            "seed_url": self.seed_url,
            "allowed_domains": self.allowed_domains,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "rate_limit_seconds": self.rate_limit_seconds,
            "respect_robots": self.respect_robots,
            "allow_login_pages": self.allow_login_pages,
            "external_network_go": self.external_network_go,
            "gates_required": self.gates_required,
            "raw_content_persistence": self.raw_content_persistence,
        }
        _reject_unsafe_payload(payload)
        return payload


def build_web_research_scope(
    task_intent: Mapping[str, Any],
    *,
    max_pages: Any = 50,
    max_depth: Any = 3,
    rate_limit_seconds: Any = 2,
    external_network_go: bool = False,
    respect_robots: bool = True,
    allow_login_pages: bool = False,
) -> WebResearchScope:
    """Create a bounded crawl scope from a redacted Telegram task intent."""

    if not isinstance(task_intent, Mapping):
        raise WebResearchScopeError("task_intent must be a mapping")
    _reject_unsafe_payload(task_intent)
    task_type = str(task_intent.get("task_type") or "").strip().lower()
    if task_type not in {"website_research", "website_research_to_memory"}:
        raise WebResearchScopeError("task_intent is not a website research task")
    target_ref = str(task_intent.get("target_ref") or "").strip().lower()
    seed_url, domain = _seed_and_domain(target_ref)
    page_cap = _bounded_int(max_pages, field="max_pages", minimum=1, maximum=500)
    depth_cap = _bounded_int(max_depth, field="max_depth", minimum=0, maximum=5)
    rate_limit = _bounded_int(rate_limit_seconds, field="rate_limit_seconds", minimum=1, maximum=60)
    gates = ["live_web_target_approval"]
    if task_type == "website_research_to_memory":
        gates.append("memory_write_policy")
    if not external_network_go:
        gates.append("external_network_go")
    return WebResearchScope(
        scope_id=_scope_id(target_ref, page_cap, depth_cap),
        target_ref=target_ref,
        seed_url=seed_url,
        allowed_domains=(domain,),
        max_pages=page_cap,
        max_depth=depth_cap,
        rate_limit_seconds=rate_limit,
        respect_robots=bool(respect_robots),
        allow_login_pages=bool(allow_login_pages),
        external_network_go=bool(external_network_go),
        gates_required=tuple(dict.fromkeys(gates)),
    )


def _seed_and_domain(target_ref: str) -> tuple[str, str]:
    if target_ref.startswith("domain:"):
        domain = target_ref.removeprefix("domain:")
        _validate_domain(domain)
        return f"https://{domain}/", domain.removeprefix("www.")
    parsed = urlparse(target_ref)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebResearchScopeError("target_ref must be an http(s) URL or domain: reference")
    if parsed.username or parsed.password:
        raise WebResearchScopeError("target_ref must not contain credentials")
    domain = parsed.hostname.lower().removeprefix("www.")
    _validate_domain(domain)
    return f"{parsed.scheme}://{parsed.netloc.lower()}/", domain


def _validate_domain(domain: str) -> None:
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain) or ".." in domain or domain.startswith("."):
        raise WebResearchScopeError("domain is invalid")


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WebResearchScopeError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise WebResearchScopeError(f"{field} out of range")
    return parsed


def _scope_id(target_ref: str, max_pages: int, max_depth: int) -> str:
    encoded = f"{target_ref}|{max_pages}|{max_depth}".encode("utf-8", errors="replace")
    return "web_scope_" + hashlib.sha256(encoded).hexdigest()[:16]


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    forbidden = ("authorization", "bearer ", "api_key", "password", "cookie", "chat_id", "file_id", "raw_text")
    if any(marker in encoded for marker in forbidden):
        raise WebResearchScopeError("web research scope payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise WebResearchScopeError("web research scope payload contains host path")
