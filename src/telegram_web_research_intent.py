"""Trusted Telegram intent contract for website research to memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class TelegramWebResearchIntent:
    trusted_channel: str
    operator_id_hash: str
    target_url: str
    target_domain: str
    max_depth: int
    max_pages: int
    dsgvo_mode: bool
    memory_write_policy: str
    live_web_go: bool

    @classmethod
    def create(
        cls,
        *,
        trusted_channel: Any,
        operator_id_hash: Any,
        target_url: Any,
        max_depth: Any = 1,
        max_pages: Any = 20,
        dsgvo_mode: bool = False,
        memory_write_policy: Any = "review",
        live_web_go: bool = False,
    ) -> "TelegramWebResearchIntent":
        url, domain = _url_domain(target_url)
        policy = str(memory_write_policy or "review").strip().lower()
        if policy not in {"review", "auto_abstract", "dry_run"}:
            raise ValueError("unsupported memory write policy")
        return cls(
            trusted_channel=str(trusted_channel or "").strip()[:80],
            operator_id_hash=str(operator_id_hash or "").strip()[:120],
            target_url=url,
            target_domain=domain,
            max_depth=max(0, min(5, int(max_depth or 1))),
            max_pages=max(1, min(500, int(max_pages or 20))),
            dsgvo_mode=bool(dsgvo_mode),
            memory_write_policy=policy,
            live_web_go=bool(live_web_go),
        )

    @property
    def runnable(self) -> bool:
        return bool(self.trusted_channel and self.operator_id_hash and self.live_web_go)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.telegram.web_research_intent.v1",
            "trusted_channel": self.trusted_channel,
            "operator_id_hash": self.operator_id_hash,
            "target_url": self.target_url,
            "target_domain": self.target_domain,
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "dsgvo_mode": self.dsgvo_mode,
            "memory_write_policy": self.memory_write_policy,
            "live_web_go": self.live_web_go,
            "runnable": self.runnable,
            "routing_uses_untrusted_page_text": False,
        }


def _url_domain(value: Any) -> tuple[str, str]:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("target_url must be http(s)")
    if parsed.username or parsed.password:
        raise ValueError("target_url must not include credentials")
    return parsed.geturl(), parsed.hostname.lower().removeprefix("www.")
