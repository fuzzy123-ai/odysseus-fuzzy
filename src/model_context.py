"""
model_context.py

Query and cache model context window sizes from OpenAI-compatible APIs.
Provides token estimation for context usage tracking.
"""

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import math
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple
from typing import Any, Awaitable, Mapping

from urllib.parse import urlparse
from urllib.parse import urlunparse

import httpx

from src.maintenance_model_policy import DEFAULT_MAINTENANCE_MODEL

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

# Tailscale uses the CGNAT range 100.64.0.0/10, NOT all of 100.0.0.0/8.
# A bare "100." prefix would classify public addresses (e.g. AWS ranges
# under 100.x outside the CGNAT block) as local; routes/model_routes.py
# already narrows this the same way for endpoint classification.
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _in_tailscale_range(host: str) -> bool:
    try:
        return ipaddress.ip_address(host) in _TAILSCALE_CGNAT
    except ValueError:
        return False


def _is_private_ip_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS)


def _normalize_base_for_compare(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models", "/completions", "/v1/messages"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _configured_endpoint_kind(url: str) -> Optional[str]:
    """Return configured endpoint kind for a chat/base URL when available."""
    target = _normalize_base_for_compare(url)
    if not target:
        return None
    if "core.database" not in sys.modules:
        return None
    try:
        from core.database import SessionLocal, ModelEndpoint
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            for ep in rows:
                base = _normalize_base_for_compare(getattr(ep, "base_url", "") or "")
                if not base:
                    continue
                if target != base and not target.startswith(base + "/"):
                    continue
                kind = (getattr(ep, "endpoint_kind", None) or "auto").strip().lower()
                if kind in ("local", "api", "proxy"):
                    return kind
                if getattr(ep, "api_key", None):
                    parsed = urlparse(base)
                    host = (parsed.hostname or "").lower()
                    path = (parsed.path or "").rstrip("/")
                    if parsed.port != 11434 and "ollama" not in host and (path.endswith("/v1") or "/openai" in path):
                        return "proxy"
                return "auto"
        finally:
            db.close()
    except Exception:
        return None


def is_local_endpoint(url: str) -> bool:
    """Check if URL points to a local/private/tailscale address."""
    kind = _configured_endpoint_kind(url)
    if kind in ("api", "proxy"):
        return False
    if kind == "local":
        return True
    try:
        host = urlparse(url).hostname or ""
        return host in _LOCAL_HOSTS or _is_private_ip_literal(host) or _in_tailscale_range(host)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CONTEXT = 128000
REQUEST_TIMEOUT = 5

# Known context windows for major API models (used as fallback when /models
# endpoint doesn't report context_length).
# Substring matching — use the shortest unique prefix so variants get caught.
KNOWN_CONTEXT_WINDOWS = {
    # --- Anthropic ---
    'claude-sonnet-4-5': 200000,
    'claude-sonnet-4-6': 200000,
    'claude-sonnet-4': 200000,
    'claude-opus-4': 200000,
    'claude-haiku-4': 200000,
    'claude-haiku-3-5': 200000,
    'claude-3-5-sonnet': 200000,
    'claude-3-5-haiku': 200000,
    'claude-3-opus': 200000,
    'claude-3-sonnet': 200000,
    'claude-3-haiku': 200000,

    # --- OpenAI ---
    'gpt-5': 400000,
    'gpt-4.1': 1047576,
    'gpt-4.1-mini': 1047576,
    'gpt-4.1-nano': 1047576,
    'gpt-4o': 128000,
    'gpt-4o-mini': 128000,
    'gpt-4-turbo': 128000,
    'gpt-4': 8192,
    'gpt-3.5-turbo': 16385,
    'o1': 200000,
    'o1-mini': 128000,
    'o1-pro': 200000,
    'o3': 200000,
    'o3-mini': 200000,
    'o4-mini': 200000,

    # --- DeepSeek ---
    'deepseek-v4-flash': 1000000,
    'deepseek-v4-pro': 1000000,
    'deepseek-v4': 1000000,
    'deepseek-chat': 64000,
    'deepseek-coder': 64000,
    'deepseek-reasoner': 64000,
    'deepseek-r1': 64000,
    'deepseek-v3': 64000,
    'deepseek-v2': 64000,

    # --- Google ---
    'gemini-2.5-pro': 1048576,
    'gemini-2.5-flash': 1048576,
    'gemini-2.0-flash': 1048576,
    'gemini-1.5-pro': 1048576,
    'gemini-1.5-flash': 1048576,
    'gemma-4': 262144,
    'gemma-3': 128000,
    'gemma-2': 8192,

    # --- Mistral ---
    'mistral-large': 128000,
    'mistral-medium': 32000,
    'mistral-small': 32000,
    'mistral-nemo': 128000,
    'mistral-7b': 32000,
    'mixtral': 32000,
    'codestral': 32000,
    'pixtral': 128000,

    # --- xAI ---
    'grok-4': 131072,
    'grok-3': 131072,
    'grok-2': 131072,

    # --- Meta / Llama ---
    'llama-4': 1048576,
    'llama-3.3': 131072,
    'llama-3.2': 131072,
    'llama-3.1': 131072,
    'llama-3': 131072,

    # --- Qwen ---
    'qwen3': 131072,
    'qwen2.5': 131072,
    'qwen2': 32768,
    'qwq': 32768,

    # --- Cohere ---
    'command-r-plus': 128000,
    'command-r': 128000,
    'command-a': 256000,

    # --- Perplexity ---
    'sonar-pro': 200000,
    'sonar': 128000,

    # --- MiniMax ---
    'minimax': 1000000,

    # --- Moonshot / Kimi ---
    'moonshot': 128000,
    'kimi': 128000,

    # --- Microsoft ---
    'phi-4': 16000,
    'phi-3': 128000,

    # --- Nvidia ---
    'nemotron': 131072,

    # --- Yi ---
    'yi-large': 32768,
    'yi-1.5': 16384,

    # --- 01.ai ---
    'yi-lightning': 16384,

    # --- Nous ---
    'hermes': 131072,
    'nous-hermes': 131072,

    # --- Open community ---
    'dolphin': 32768,
    'mythomax': 4096,
    'wizard': 32768,
    'openchat': 8192,
    'solar': 32768,
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_context_cache: Dict[Tuple[str, str], Tuple[int, bool]] = {}


def _get_context_length_cached(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Return (context_length, known). ``known`` is False only when the value is a
    bare DEFAULT_CONTEXT fallback (no endpoint report and not in the known table)."""
    configured_kind = _configured_endpoint_kind(endpoint_url)
    is_local = is_local_endpoint(endpoint_url)
    # Key on (endpoint_url, model): the same model id can be served by two
    # different remote endpoints with different real context windows (e.g. a
    # capped proxy vs. the full provider), so caching by model id alone would
    # serve one endpoint's window for the other (issue #2603).
    cache_key = (endpoint_url, model)
    if not is_local and cache_key in _context_cache:
        return _context_cache[cache_key]

    ctx, known = _query_context_length(endpoint_url, model)
    # Only cache non-default values to allow retry on next request.
    # Local endpoints can restart with a different --max-model-len while keeping
    # the same model id, so always re-query them instead of serving stale cache.
    if not is_local and (ctx != DEFAULT_CONTEXT or configured_kind in ("api", "proxy")):
        _context_cache[cache_key] = (ctx, known)
    logger.info(f"Context length for {model}: {ctx}")
    return ctx, known


def get_context_length(endpoint_url: str, model: str) -> int:
    """Get the context window size for a model.

    Queries /v1/models on the endpoint and looks for context_length
    or context_window fields. Caches result per (endpoint, model).
    Falls back to DEFAULT_CONTEXT if unavailable.
    """
    return _get_context_length_cached(endpoint_url, model)[0]


def get_context_length_known(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Like ``get_context_length`` but also returns whether the window was actually
    discovered (endpoint-reported or in the known-models table) rather than the bare
    DEFAULT_CONTEXT fallback. Callers that *scale* a budget off the window must not
    trust an unknown value — a fallback 128K isn't proof the model holds 128K
    (review on #4122)."""
    return _get_context_length_cached(endpoint_url, model)


def budget_context_for_model(endpoint_url: str, model: str, *, fallback: int = 0) -> int:
    """Context window to scale the agent input budget against.

    Returns the *freshly discovered* window when it was actually proven
    (endpoint-reported / known table), else 0 so auto-scaling stays conservative.
    Crucially this binds the ``known`` flag to the value it proves — callers must
    not pair this flag with a context length from a *different* lookup (a stale
    local re-query, or a caller that didn't pass one), which would budget off an
    unproven number (review on #4122). On probe error, returns ``fallback`` (the
    caller's best-known value) to preserve prior behaviour."""
    try:
        ctx, known = get_context_length_known(endpoint_url, model)
        return ctx if known else 0
    except Exception:
        return fallback


def _lookup_known(model: str) -> Optional[int]:
    """Check known context windows by substring match.

    Picks the LONGEST matching key so a short key never shadows a more specific
    one. Without this, 'o1' (200k) precedes 'o1-mini' (128k) in the table and a
    first-match return would report o1-mini's window as 200k.
    """
    name = model.lower()
    basename = name.split("/")[-1] if "/" in name else name
    basename = basename.split(":")[0]  # strip :free, :extended etc.
    best_key: Optional[str] = None
    best_ctx: Optional[int] = None
    for key, ctx in KNOWN_CONTEXT_WINDOWS.items():
        if key in basename or key in name:
            if best_key is None or len(key) > len(best_key):
                best_key, best_ctx = key, ctx
    return best_ctx


def _query_context_length(endpoint_url: str, model: str) -> Tuple[int, bool]:
    """Query the model API for context length. Returns (context_length, known) where
    ``known`` is False only for the bare DEFAULT_CONTEXT fallback."""
    known = _lookup_known(model)
    api_ctx = None
    configured_kind = _configured_endpoint_kind(endpoint_url)

    # Large OpenAI-compatible proxies can make /models expensive. If the
    # endpoint is explicitly configured as API/proxy, prefer known context
    # metadata (or the default) over downloading the full catalog.
    if configured_kind in ("api", "proxy"):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        return DEFAULT_CONTEXT, False

    # Try llama.cpp /slots endpoint first — reports actual serving context
    if is_local_endpoint(endpoint_url):
        try:
            base = endpoint_url.split("/v1")[0] if "/v1" in endpoint_url else endpoint_url.rsplit("/", 1)[0]
            r = httpx.get(f"{base}/slots", timeout=REQUEST_TIMEOUT)
            if r.is_success:
                slots = r.json()
                if isinstance(slots, list) and slots:
                    n_ctx = slots[0].get("n_ctx")
                    if n_ctx and isinstance(n_ctx, int) and n_ctx > 0:
                        logger.info(f"llama.cpp /slots reports n_ctx={n_ctx} for {model}")
                        return n_ctx, True
        except Exception:
            pass

    # GitHub Copilot's /models requires auth + X-GitHub-Api-Version headers that
    # aren't available here; an unauthenticated probe just 400s. All Copilot
    # picker models are major API models covered by the known-context table, so
    # rely on that instead of a doomed network call.
    from src.copilot import is_copilot_base
    if is_copilot_base(endpoint_url):
        if known:
            logger.info(f"Using known context window for {model}: {known}")
            return known, True
        return DEFAULT_CONTEXT, False

    from src.endpoint_resolver import build_models_url

    models_url = build_models_url(endpoint_url)
    try:
        r = httpx.get(models_url, timeout=REQUEST_TIMEOUT)
        if r.is_success:
            data = r.json()
            models_list = data.get("data") or []

            for m in models_list:
                mid = m.get("id", "")
                if mid == model or mid.split("/")[-1] == model.split("/")[-1]:
                    for field in (
                        "context_length",
                        "context_window",
                        "max_model_len",
                        "max_context_length",
                        "max_seq_len",
                    ):
                        val = m.get(field)
                        if val and isinstance(val, (int, float)) and val > 0:
                            api_ctx = int(val)
                            break

                    if not api_ctx:
                        meta = m.get("meta") or m.get("model_extra") or {}
                        if isinstance(meta, dict):
                            # n_ctx is the actual serving context (set via -c flag in llama.cpp)
                            for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
                                val = meta.get(field)
                                if val and isinstance(val, (int, float)) and val > 0:
                                    api_ctx = int(val)
                                    break
                    break
    except Exception as e:
        logger.debug(f"Failed to query context length for {model}: {e}")

    # For local/self-hosted endpoints, trust the API value (user set --max-model-len)
    # For cloud APIs, use the larger value (API can report low defaults)
    if api_ctx and known:
        _is_local = is_local_endpoint(endpoint_url)
        if _is_local and api_ctx < known:
            logger.info(f"Local endpoint reports {api_ctx} for {model} (known max: {known}) — using API value")
            return api_ctx, True
        result = max(api_ctx, known)
        if api_ctx < known:
            logger.info(f"API reported {api_ctx} for {model}, using known {known} instead")
        return result, True
    if api_ctx:
        return api_ctx, True
    if known:
        logger.info(f"Using known context window for {model}: {known}")
        return known, True

    return DEFAULT_CONTEXT, False


# ---------------------------------------------------------------------------
# Async TTL context service
# ---------------------------------------------------------------------------

ASYNC_CONTEXT_SNAPSHOT_SCHEMA = "odysseus.async_model_context_snapshot.v1"
ASYNC_CONTEXT_REGISTRY_SCHEMA = "odysseus.async_model_context_registry.v1"
DEFAULT_CONTEXT_FRESH_TTL_SECONDS = 300.0
DEFAULT_CONTEXT_STALE_TTL_SECONDS = 3_600.0
DEFAULT_CONTEXT_NEGATIVE_TTL_SECONDS = 30.0
DEFAULT_CONTEXT_REGISTRY_MAX_ENTRIES = 128


@dataclass(frozen=True, slots=True)
class ContextProbeHTTPResponse:
    status_code: int
    payload: Any = None


@dataclass(frozen=True, slots=True)
class ContextLengthSnapshot:
    context_length: int
    known: bool
    cache_status: str
    endpoint_generation: int
    schema: str = ASYNC_CONTEXT_SNAPSHOT_SCHEMA

    def audit_dict(self) -> Dict[str, Any]:
        """Return content-free state without endpoint or model identifiers."""

        return {
            "schema": self.schema,
            "context_length": self.context_length,
            "known": self.known,
            "cache_status": self.cache_status,
            "endpoint_generation": self.endpoint_generation,
        }


@dataclass(frozen=True, slots=True)
class _ContextProbeResult:
    context_length: int
    known: bool
    source: str


@dataclass(slots=True)
class _AsyncContextCacheEntry:
    context_length: int
    known: bool
    negative: bool
    fresh_until: float
    stale_until: float
    last_access: int


AsyncContextTransport = Callable[[str, float], Awaitable[ContextProbeHTTPResponse]]
_AsyncContextKey = Tuple[str, int, str]


def _record_gmi_context_metric(
    event: str,
    *,
    status: str,
    value: float | int = 1,
) -> None:
    """Best-effort closed metrics; context discovery must never depend on them."""

    try:
        from src.observability_metrics import record_gmi_runtime_event

        record_gmi_runtime_event(event, status=status, value=value)
    except Exception:
        pass


class AsyncModelContextService:
    """Bounded async context discovery with TTL and per-key single-flight."""

    def __init__(
        self,
        *,
        fresh_ttl_seconds: float = DEFAULT_CONTEXT_FRESH_TTL_SECONDS,
        stale_ttl_seconds: float = DEFAULT_CONTEXT_STALE_TTL_SECONDS,
        negative_ttl_seconds: float = DEFAULT_CONTEXT_NEGATIVE_TTL_SECONDS,
        max_entries: int = DEFAULT_CONTEXT_REGISTRY_MAX_ENTRIES,
        request_timeout_seconds: float = REQUEST_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
        transport: AsyncContextTransport | None = None,
    ) -> None:
        self.fresh_ttl_seconds = _positive_float("fresh_ttl_seconds", fresh_ttl_seconds)
        self.stale_ttl_seconds = _nonnegative_float("stale_ttl_seconds", stale_ttl_seconds)
        self.negative_ttl_seconds = _positive_float(
            "negative_ttl_seconds", negative_ttl_seconds
        )
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = max_entries
        self.request_timeout_seconds = _positive_float(
            "request_timeout_seconds", request_timeout_seconds
        )
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock
        self._transport = transport or _default_async_context_transport
        self._condition = asyncio.Condition()
        self._entries: Dict[_AsyncContextKey, _AsyncContextCacheEntry] = {}
        self._inflight: Dict[_AsyncContextKey, asyncio.Task[ContextLengthSnapshot]] = {}
        self._generations: Dict[str, int] = {}
        self._access_sequence = 0
        self._stats = {
            "probes_total": 0,
            "fresh_hits_total": 0,
            "stale_hits_total": 0,
            "negative_hits_total": 0,
            "singleflight_joins_total": 0,
            "evictions_total": 0,
            "invalidations_total": 0,
        }

    async def get_snapshot(self, endpoint_url: str, model: str) -> ContextLengthSnapshot:
        endpoint_key = _async_context_endpoint_key(endpoint_url)
        model_key = str(model or "").strip()
        if not model_key:
            raise ValueError("model must not be empty")
        track_gmi = model_key == DEFAULT_MAINTENANCE_MODEL

        while True:
            now = float(self._clock())
            async with self._condition:
                generation = self._generations.get(endpoint_key, 0)
                key = (endpoint_key, generation, model_key)
                entry = self._entries.get(key)
                if entry is not None and now < entry.fresh_until:
                    entry.last_access = self._touch_locked()
                    if entry.negative:
                        self._stats["negative_hits_total"] += 1
                        status = "negative_cache"
                        if track_gmi:
                            _record_gmi_context_metric(
                                "context_cache", status="negative"
                            )
                    else:
                        self._stats["fresh_hits_total"] += 1
                        status = "fresh_cache"
                        if track_gmi:
                            _record_gmi_context_metric(
                                "context_cache", status="hit"
                            )
                    return _snapshot_from_entry(entry, status, generation)

                if entry is not None and entry.known and now < entry.stale_until:
                    entry.last_access = self._touch_locked()
                    self._stats["stale_hits_total"] += 1
                    if track_gmi:
                        _record_gmi_context_metric(
                            "context_cache", status="stale"
                        )
                    if key not in self._inflight:
                        self._start_probe_locked(key, endpoint_url, model_key)
                    return _snapshot_from_entry(entry, "stale_cache", generation)

                if entry is not None and key not in self._inflight:
                    del self._entries[key]

                task = self._inflight.get(key)
                if task is not None:
                    self._stats["singleflight_joins_total"] += 1
                else:
                    if not self._ensure_capacity_locked(key):
                        await self._condition.wait()
                        continue
                    if track_gmi:
                        _record_gmi_context_metric(
                            "context_cache", status="miss"
                        )
                    task = self._start_probe_locked(key, endpoint_url, model_key)

            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if track_gmi:
                    _record_gmi_context_metric(
                        "cancellation", status="context_wait"
                    )
                raise

    async def get_context_length(self, endpoint_url: str, model: str) -> int:
        return (await self.get_snapshot(endpoint_url, model)).context_length

    async def get_context_length_known(
        self, endpoint_url: str, model: str
    ) -> Tuple[int, bool]:
        snapshot = await self.get_snapshot(endpoint_url, model)
        return snapshot.context_length, snapshot.known

    async def invalidate_endpoint(self, endpoint_url: str) -> int:
        """Advance an endpoint generation and fence old cached/in-flight work."""

        endpoint_key = _async_context_endpoint_key(endpoint_url)
        async with self._condition:
            generation = self._generations.get(endpoint_key, 0) + 1
            self._generations[endpoint_key] = generation
            for key in [key for key in self._entries if key[0] == endpoint_key]:
                del self._entries[key]
            self._stats["invalidations_total"] += 1
            self._condition.notify_all()
            return generation

    async def clear(self) -> None:
        async with self._condition:
            endpoints = set(self._generations)
            endpoints.update(key[0] for key in self._entries)
            endpoints.update(key[0] for key in self._inflight)
            for endpoint_key in endpoints:
                self._generations[endpoint_key] = self._generations.get(endpoint_key, 0) + 1
            self._entries.clear()
            self._stats["invalidations_total"] += 1
            self._condition.notify_all()

    async def wait_for_idle(self) -> None:
        """Wait for background stale refreshes using cancellation-safe joins."""

        while True:
            async with self._condition:
                tasks = tuple(self._inflight.values())
            if not tasks:
                return
            await asyncio.gather(
                *(asyncio.shield(task) for task in tasks), return_exceptions=True
            )

    async def registry_snapshot(self) -> Dict[str, int | str]:
        async with self._condition:
            positive = sum(1 for entry in self._entries.values() if not entry.negative)
            negative = len(self._entries) - positive
            keys = set(self._entries) | set(self._inflight)
            return {
                "schema": ASYNC_CONTEXT_REGISTRY_SCHEMA,
                "registry_key_count": len(keys),
                "entry_count": len(self._entries),
                "positive_entry_count": positive,
                "negative_entry_count": negative,
                "inflight_count": len(self._inflight),
                "endpoint_generation_count": len(self._generations),
                "max_entries": self.max_entries,
                **self._stats,
            }

    def _start_probe_locked(
        self,
        key: _AsyncContextKey,
        endpoint_url: str,
        model: str,
    ) -> asyncio.Task[ContextLengthSnapshot]:
        self._stats["probes_total"] += 1
        task = asyncio.create_task(
            self._run_probe(key, endpoint_url, model),
            name="odysseus-model-context-probe",
        )
        self._inflight[key] = task
        return task

    async def _run_probe(
        self,
        key: _AsyncContextKey,
        endpoint_url: str,
        model: str,
    ) -> ContextLengthSnapshot:
        generation = key[1]
        track_gmi = model == DEFAULT_MAINTENANCE_MODEL
        probe_started = time.monotonic()
        probe_status = "failure"
        try:
            try:
                result = await _probe_context_length_async(
                    endpoint_url,
                    model,
                    transport=self._transport,
                    timeout_seconds=self.request_timeout_seconds,
                )
            except asyncio.CancelledError:
                probe_status = "cancelled"
                if track_gmi:
                    _record_gmi_context_metric(
                        "cancellation", status="context_probe"
                    )
                raise
            except Exception:
                result = _ContextProbeResult(DEFAULT_CONTEXT, False, "probe_error")
            if result.known:
                probe_status = "success"

            now = float(self._clock())
            async with self._condition:
                current_generation = self._generations.get(key[0], 0)
                existing = self._entries.get(key)
                if current_generation != generation:
                    return ContextLengthSnapshot(
                        result.context_length,
                        result.known,
                        "generation_superseded",
                        generation,
                    )

                if not result.known and existing is not None and existing.known and now < existing.stale_until:
                    existing.fresh_until = min(
                        existing.stale_until,
                        now + self.negative_ttl_seconds,
                    )
                    existing.last_access = self._touch_locked()
                    return _snapshot_from_entry(existing, "stale_if_error", generation)

                negative = not result.known
                fresh_ttl = (
                    self.negative_ttl_seconds if negative else self.fresh_ttl_seconds
                )
                fresh_until = now + fresh_ttl
                stale_until = (
                    fresh_until if negative else fresh_until + self.stale_ttl_seconds
                )
                entry = _AsyncContextCacheEntry(
                    context_length=result.context_length,
                    known=result.known,
                    negative=negative,
                    fresh_until=fresh_until,
                    stale_until=stale_until,
                    last_access=self._touch_locked(),
                )
                self._entries[key] = entry
                return _snapshot_from_entry(entry, "probe", generation)
        finally:
            if track_gmi:
                _record_gmi_context_metric(
                    "context_probe",
                    status=probe_status,
                    value=time.monotonic() - probe_started,
                )
            async with self._condition:
                current_task = asyncio.current_task()
                if self._inflight.get(key) is current_task:
                    del self._inflight[key]
                self._condition.notify_all()

    def _ensure_capacity_locked(self, key: _AsyncContextKey) -> bool:
        keys = set(self._entries) | set(self._inflight)
        if key in keys:
            return True
        while len(keys) >= self.max_entries:
            candidates = [
                (entry.last_access, existing_key)
                for existing_key, entry in self._entries.items()
                if existing_key not in self._inflight
            ]
            if not candidates:
                return False
            _, oldest_key = min(candidates, key=lambda item: (item[0], item[1]))
            del self._entries[oldest_key]
            self._stats["evictions_total"] += 1
            keys = set(self._entries) | set(self._inflight)
        return True

    def _touch_locked(self) -> int:
        self._access_sequence += 1
        return self._access_sequence


async def _default_async_context_transport(
    url: str, timeout_seconds: float
) -> ContextProbeHTTPResponse:
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        response = await client.get(url, timeout=timeout_seconds)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return ContextProbeHTTPResponse(response.status_code, payload)


async def _probe_context_length_async(
    endpoint_url: str,
    model: str,
    *,
    transport: AsyncContextTransport,
    timeout_seconds: float,
) -> _ContextProbeResult:
    """Async equivalent of context discovery without calling the sync probe."""

    known = _lookup_known(model)
    configured_kind = _configured_endpoint_kind(endpoint_url)
    if configured_kind in ("api", "proxy"):
        if known:
            return _ContextProbeResult(known, True, "known_table")
        return _ContextProbeResult(DEFAULT_CONTEXT, False, "configured_remote_default")

    local = is_local_endpoint(endpoint_url)
    if local:
        try:
            response = await transport(
                _async_slots_url(endpoint_url), timeout_seconds
            )
            if 200 <= response.status_code < 300 and isinstance(response.payload, list):
                if response.payload and isinstance(response.payload[0], Mapping):
                    n_ctx = response.payload[0].get("n_ctx")
                    if isinstance(n_ctx, int) and not isinstance(n_ctx, bool) and n_ctx > 0:
                        return _ContextProbeResult(n_ctx, True, "slots")
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    from src.copilot import is_copilot_base

    if is_copilot_base(endpoint_url):
        if known:
            return _ContextProbeResult(known, True, "known_table")
        return _ContextProbeResult(DEFAULT_CONTEXT, False, "copilot_default")

    from src.endpoint_resolver import build_models_url

    api_ctx: int | None = None
    try:
        response = await transport(build_models_url(endpoint_url), timeout_seconds)
        if 200 <= response.status_code < 300 and isinstance(response.payload, Mapping):
            models_list = response.payload.get("data") or []
            if isinstance(models_list, list):
                api_ctx = _context_from_models_payload(models_list, model)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass

    if api_ctx and known:
        if local and api_ctx < known:
            return _ContextProbeResult(api_ctx, True, "models")
        return _ContextProbeResult(max(api_ctx, known), True, "models_and_known")
    if api_ctx:
        return _ContextProbeResult(api_ctx, True, "models")
    if known:
        return _ContextProbeResult(known, True, "known_table")
    return _ContextProbeResult(DEFAULT_CONTEXT, False, "default")


def _context_from_models_payload(models_list: List[Any], model: str) -> int | None:
    for item in models_list:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id") or "")
        if model_id != model and model_id.split("/")[-1] != model.split("/")[-1]:
            continue
        for field in (
            "context_length",
            "context_window",
            "max_model_len",
            "max_context_length",
            "max_seq_len",
        ):
            value = item.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return int(value)
        metadata = item.get("meta") or item.get("model_extra") or {}
        if isinstance(metadata, Mapping):
            for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
                value = metadata.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    return int(value)
        return None
    return None


def _async_context_endpoint_key(endpoint_url: str) -> str:
    normalized = _normalize_base_for_compare(endpoint_url)
    try:
        parsed = urlparse(normalized)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid context endpoint") from exc
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not host:
        raise ValueError("context endpoint requires an http(s) host")
    default_port = 443 if scheme == "https" else 80
    port_part = "" if port in {None, default_port} else f":{port}"
    host_label = f"[{host}]" if ":" in host else host
    return urlunparse((scheme, f"{host_label}{port_part}", parsed.path.rstrip("/"), "", "", ""))


def _async_slots_url(endpoint_url: str) -> str:
    parsed = urlparse(_normalize_base_for_compare(endpoint_url))
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunparse((parsed.scheme, parsed.netloc, f"{path}/slots", "", "", ""))


def _snapshot_from_entry(
    entry: _AsyncContextCacheEntry, status: str, generation: int
) -> ContextLengthSnapshot:
    return ContextLengthSnapshot(
        entry.context_length,
        entry.known,
        status,
        generation,
    )


def _positive_float(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"{name} must be > 0")
    return float(value)


def _nonnegative_float(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{name} must be >= 0")
    return float(value)


_async_context_service = AsyncModelContextService()


async def get_context_snapshot_async(endpoint_url: str, model: str) -> ContextLengthSnapshot:
    return await _async_context_service.get_snapshot(endpoint_url, model)


async def get_context_length_async(endpoint_url: str, model: str) -> int:
    return await _async_context_service.get_context_length(endpoint_url, model)


async def get_context_length_known_async(
    endpoint_url: str, model: str
) -> Tuple[int, bool]:
    return await _async_context_service.get_context_length_known(endpoint_url, model)


async def invalidate_context_endpoint_async(endpoint_url: str) -> int:
    return await _async_context_service.invalidate_endpoint(endpoint_url)


def estimate_tokens(messages: List[Dict], model_hint: Optional[str] = None) -> int:
    """Rough token estimate for a list of messages.

    Without ``model_hint`` this preserves the historical chars * 0.3 behavior.
    With a hint, message content and tool-call payloads use the deterministic
    offline adapter registry in :mod:`src.token_estimator`.
    Also adds ~4 tokens per message for role/formatting overhead, and counts
    assistant tool_calls (name + arguments) — a tool-only turn carries
    content=None with the real payload in tool_calls, so ignoring them made the
    estimate (and the compaction/trim gates that rely on it) blind to large
    tool arguments.
    """
    estimate_content: Optional[Callable[[str], int]] = None
    if model_hint is not None and str(model_hint).strip():
        from src.token_estimator import estimate_text_tokens

        def routed_content_estimate(value: str) -> int:
            return estimate_text_tokens(value, model_hint).count

        estimate_content = routed_content_estimate

    total = 0
    for msg in messages:
        total += 4  # per-message overhead (role, separators)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_content(content) if estimate_content else int(len(content) * 0.3)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text", "") or "")
                    total += estimate_content(text) if estimate_content else int(len(text) * 0.3)
        # Tool calls carry real payload too: a tool-only assistant turn is stored
        # with content=None and the actual args (e.g. a create_document body) in
        # tool_calls[].function.arguments. Ignoring them made large tool arguments
        # read as ~0 tokens, so the compaction/trim gates missed genuine overflow.
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                name = fn.get("name", "") or ""
                args = fn.get("arguments", "") or ""
                if not isinstance(args, str):
                    args = str(args)  # some shapes store arguments as a dict
                total += 4  # per tool-call overhead (id, type, wrapper)
                payload = str(name) + args
                total += estimate_content(payload) if estimate_content else int(len(payload) * 0.3)
    return total
