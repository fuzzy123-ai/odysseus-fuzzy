"""Per-job network policy for sandbox execution."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


SANDBOX_NETWORK_POLICY_SCHEMA = "odysseus.sandbox_network_policy.v1"


class SandboxNetworkPolicyError(ValueError):
    """Raised when a sandbox network policy is unsafe."""


@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    mode: str
    allowlist: tuple[str, ...]
    allowed: bool
    reasons: tuple[str, ...]
    raw_content_visible: bool = False
    schema: str = SANDBOX_NETWORK_POLICY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode,
            "allowlist": self.allowlist,
            "allowed": self.allowed,
            "reasons": self.reasons,
            "raw_content_visible": False,
        }


def build_sandbox_network_policy(*, mode: Any = "none", allowlist: Iterable[Any] = ()) -> SandboxNetworkPolicy:
    safe_mode = str(mode or "none").strip().lower()
    if safe_mode not in {"none", "allowlist", "fullweb"}:
        raise SandboxNetworkPolicyError("unsupported network mode")
    hosts = tuple(dict.fromkeys(_safe_host(item) for item in allowlist if str(item or "").strip()))
    reasons: list[str] = []
    allowed = True
    if safe_mode == "none" and hosts:
        reasons.append("allowlist_ignored_for_network_none")
    if safe_mode == "allowlist" and not hosts:
        reasons.append("allowlist_required")
        allowed = False
    if safe_mode == "fullweb":
        reasons.append("fullweb_requires_separate_live_gate")
        allowed = False
    return SandboxNetworkPolicy(
        mode=safe_mode,
        allowlist=hosts,
        allowed=allowed,
        reasons=tuple(reasons),
    )


def network_policy_from_job(job: Mapping[str, Any] | Any) -> SandboxNetworkPolicy:
    if isinstance(job, Mapping):
        return build_sandbox_network_policy(
            mode=job.get("network_mode") or "none",
            allowlist=job.get("network_allowlist") or (),
        )
    return build_sandbox_network_policy(
        mode=getattr(job, "network_mode", "none"),
        allowlist=getattr(job, "network_allowlist", ()),
    )


def _safe_host(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/", 1)[0]
    if not re.fullmatch(r"[a-z0-9.-]{1,253}(:[0-9]{1,5})?", text):
        raise SandboxNetworkPolicyError("network allowlist host is unsafe")
    if text in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise SandboxNetworkPolicyError("loopback allowlist requires a separate gate")
    return text
