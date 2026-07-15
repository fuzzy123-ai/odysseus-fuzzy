"""Deterministic, offline-only token estimator routing.

No provider tokenizer assets are bundled with this slice.  Provider-family
hints therefore select distinct *conservative UTF-8 upper-bound fallbacks*, not
provider-exact or empirically measured tokenizers.  The routing metadata is
useful provenance, but makes no provider-accuracy claim and never downloads or
discovers assets at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


LEGACY_ADAPTER_ID = "model_context_chars_x_0_3"
ADAPTER_REVISION = "1"


PROVIDER_TOKENIZER_ASSET_GATE = {
    "state": "unresolved_no_bundled_provider_tokenizer_assets",
    "safe_default": "conservative_utf8_byte_upper_bound",
    "required_before_provider_exact_claim": (
        "bundle and license-review immutable tokenizer assets",
        "verify frozen provider-tokenizer ground truth offline",
        "prove runtime initialization performs no download or provider call",
    ),
}

# End-to-end propagation is a separately testable contract from provider-exact
# tokenizer assets. The latter remains unresolved above; this gate records only
# that productive hard-limit call sites now carry the selected model into the
# deterministic offline estimator.
MODEL_HINT_PROPAGATION_GATE = {
    "state": "satisfied_focused_and_compatibility_green",
    "compatible_api_hook": "src.model_context.estimate_tokens(messages, model_hint=None)",
    "required_callsites": (
        "src/context_compactor.py: pass model_hint through trim_for_context, maybe_compact, truncation helpers, and every estimate_tokens call",
        "src/agent_loop.py: pass selected model into before/after estimates and trim_for_context",
        "routes/chat_routes.py: pass the answered/requested model into fallback usage estimation",
        "routes/chat_helpers.py: pass the session model into preface estimates and compaction entrypoints",
        "src/context_orchestrator.py: accept and propagate model_hint through message-budget trimming",
        "routes/history_routes.py: pass the session model into before/after history usage estimates",
    ),
    "required_tests": (
        "tests/test_context_compactor_model_hint_propagation.py",
        "tests/test_agent_loop_model_hint_budget.py",
        "tests/test_chat_routes_model_hint_usage.py",
        "tests/test_context_orchestrator_model_hint_budget.py",
        "tests/test_history_routes_model_hint_usage.py",
        "tests/test_chat_helpers_model_hint_budget.py",
        "tests/test_session_manager_model_hint_budget.py",
        "tests/test_context_provider_model_hint_budget.py",
        "plugins/obsidian/tests/test_context_provider_model_hint_budget.py",
    ),
    "evidence": {
        "focused": "19 passed",
        "compatibility": "264 passed",
        "no_hint_allowlist_count": 3,
        "network_or_provider_calls": False,
    },
}


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """Content-free metadata for one deterministic token estimate."""

    count: int
    adapter_id: str
    adapter_revision: str
    exact: bool
    fallback: bool
    confidence: str

    def __post_init__(self) -> None:
        if self.exact == self.fallback:
            raise ValueError("a token estimate must be exactly one of exact or fallback")

    @property
    def exact_or_fallback(self) -> str:
        return "exact" if self.exact else "fallback"

    def to_dict(self) -> dict[str, int | str | bool]:
        return {
            "count": self.count,
            "adapter_id": self.adapter_id,
            "adapter_revision": self.adapter_revision,
            "exact": self.exact,
            "fallback": self.fallback,
            "exact_or_fallback": self.exact_or_fallback,
            "confidence": self.confidence,
        }


class TokenEstimator(Protocol):
    """Minimal protocol implemented by every local estimator adapter."""

    adapter_id: str
    adapter_revision: str
    exact: bool
    fallback: bool
    confidence: str
    conservative_chars_per_token: float

    def count(self, text: str) -> int:
        """Return the deterministic content-token count for ``text``."""


@dataclass(frozen=True, slots=True)
class EstimatorRoute:
    """One ordered, case-insensitive model-pattern route."""

    route_id: str
    patterns: tuple[re.Pattern[str], ...]
    estimator: TokenEstimator

    def matches(self, normalized_model_hint: str) -> bool:
        return any(pattern.search(normalized_model_hint) for pattern in self.patterns)


@dataclass(frozen=True, slots=True)
class _LegacyCharacterEstimator:
    adapter_id: str = LEGACY_ADAPTER_ID
    adapter_revision: str = ADAPTER_REVISION
    exact: bool = False
    fallback: bool = True
    confidence: str = "legacy_compatibility_only"
    conservative_chars_per_token: float = 1 / 0.3

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) * 0.3))


@dataclass(frozen=True, slots=True)
class _Utf8ByteExactEstimator:
    """Exact adapter for the explicit ``odysseus-utf8-byte-v1`` scheme."""

    adapter_id: str = "utf8_byte_exact"
    adapter_revision: str = ADAPTER_REVISION
    exact: bool = True
    fallback: bool = False
    confidence: str = "exact_for_declared_utf8_byte_scheme"
    conservative_chars_per_token: float = 1.0

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class _ConservativeUtf8Estimator:
    """Offline upper bound used when no real local tokenizer asset exists.

    Modern text tokenizers ultimately have a byte/character fallback.  Counting
    every UTF-8 byte as one unit deliberately spends budget early instead of
    risking an overflow.  It is not presented as an exact provider token count.
    """

    adapter_id: str
    adapter_revision: str = ADAPTER_REVISION
    exact: bool = False
    fallback: bool = True
    confidence: str = "conservative_upper_bound"
    conservative_chars_per_token: float = 1.0

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


_LEGACY_ESTIMATOR = _LegacyCharacterEstimator()
_EXACT_BYTE_ESTIMATOR = _Utf8ByteExactEstimator()
_OPENAI_FAMILY_FALLBACK = _ConservativeUtf8Estimator(
    adapter_id="openai_family_conservative_utf8_upper_bound",
)
_ANTHROPIC_FAMILY_FALLBACK = _ConservativeUtf8Estimator(
    adapter_id="anthropic_family_conservative_utf8_upper_bound",
)
_SENTENCEPIECE_FAMILY_FALLBACK = _ConservativeUtf8Estimator(
    adapter_id="sentencepiece_family_conservative_utf8_upper_bound",
)
_CONSERVATIVE_FALLBACK = _ConservativeUtf8Estimator(
    adapter_id="unknown_model_conservative_utf8_upper_bound",
)


# Order is part of the compatibility contract: the explicit reference scheme
# wins, followed by uniquely identified conservative no-asset family fallbacks.
MODEL_ESTIMATOR_ROUTES: tuple[EstimatorRoute, ...] = (
    EstimatorRoute(
        route_id="explicit_utf8_byte_v1",
        patterns=(re.compile(r"(?:^|[/_.:-])odysseus-utf8-byte-v1$"),),
        estimator=_EXACT_BYTE_ESTIMATOR,
    ),
    EstimatorRoute(
        route_id="openai_family_no_asset_fallback",
        patterns=(re.compile(r"(?:^|[/_.:-])(?:gpt-|chatgpt-|codex|o[134](?:-|$))"),),
        estimator=_OPENAI_FAMILY_FALLBACK,
    ),
    EstimatorRoute(
        route_id="anthropic_family_no_asset_fallback",
        patterns=(re.compile(r"(?:^|[/_.:-])claude(?:-|$)"),),
        estimator=_ANTHROPIC_FAMILY_FALLBACK,
    ),
    EstimatorRoute(
        route_id="sentencepiece_family_no_asset_fallback",
        patterns=(
            re.compile(
                r"(?:^|[/_.:-])(?:llama|gemma|gemini|mistral|mixtral|qwen|deepseek|phi)(?:[-:.]|$)"
            ),
        ),
        estimator=_SENTENCEPIECE_FAMILY_FALLBACK,
    ),
)


def estimator_for_model(model_hint: str | None) -> TokenEstimator:
    """Select a local estimator without importing or discovering assets."""

    if model_hint is None or not str(model_hint).strip():
        return _LEGACY_ESTIMATOR
    normalized = str(model_hint).strip().lower()
    for route in MODEL_ESTIMATOR_ROUTES:
        if route.matches(normalized):
            return route.estimator
    return _CONSERVATIVE_FALLBACK


def estimate_text_tokens(text: str, model_hint: str | None = None) -> TokenEstimate:
    """Estimate plain-text tokens and disclose the selected adapter semantics."""

    estimator = estimator_for_model(model_hint)
    count = estimator.count(str(text or ""))
    return TokenEstimate(
        count=count,
        adapter_id=estimator.adapter_id,
        adapter_revision=estimator.adapter_revision,
        exact=estimator.exact,
        fallback=estimator.fallback,
        confidence=estimator.confidence,
    )


def estimate_character_capacity(max_tokens: int, model_hint: str | None = None) -> int:
    """Conservative initial character window for token-aware splitters."""

    estimator = estimator_for_model(model_hint)
    return max(1, int(max_tokens * estimator.conservative_chars_per_token))
