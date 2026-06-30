"""Provider classification helpers for LLM endpoint handling."""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urlparse

from src.llm_ollama import _is_ollama_native_url


def _host_match(url: str, *domains: str) -> bool:
    """Return True if url's hostname equals any domain or is a subdomain."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def _detect_provider(url: str, *, is_ollama_native_url_func=_is_ollama_native_url) -> str:
    """Detect the API provider from a configured endpoint URL."""
    if is_ollama_native_url_func(url):
        return "ollama"
    if _host_match(url, "anthropic.com"):
        return "anthropic"
    if _host_match(url, "opencode.ai/zen/go"):
        return "opencode-go"
    if _host_match(url, "opencode.ai/zen"):
        return "opencode-zen"
    if _host_match(url, "openrouter.ai"):
        return "openrouter"
    if _host_match(url, "groq.com"):
        return "groq"
    if _host_match(url, "nvidia.com"):
        return "nvidia"
    if _host_match(url, "moonshot.ai") or _host_match(url, "moonshot.cn"):
        return "moonshot"
    if _host_match(url, "mistral.ai"):
        return "mistral"
    from src.chatgpt_subscription import is_chatgpt_subscription_base
    if is_chatgpt_subscription_base(url):
        return "chatgpt-subscription"
    from src.copilot import is_copilot_base
    if is_copilot_base(url):
        return "copilot"
    return "openai"


def _is_self_hosted_openai_compatible(url: str) -> bool:
    """Return true for local/custom OpenAI-compatible endpoints."""
    if _detect_provider(url) != "openai" or _host_match(url, "openai.com"):
        return False
    from src.model_context import is_local_endpoint
    return is_local_endpoint(url)


def _apply_local_cache_affinity(payload: Dict, url: str, session_id: Optional[str]) -> None:
    """Add llama.cpp-server slot-affinity hints to an outgoing payload."""
    if not session_id:
        return
    if not _is_self_hosted_openai_compatible(url):
        return
    payload.setdefault("session_id", str(session_id))
    payload.setdefault("cache_prompt", True)


def _provider_headers(provider: str, headers: Optional[Dict] = None) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if isinstance(headers, dict):
        h.update(headers)
    if provider == "openrouter":
        h.setdefault("HTTP-Referer", "https://github.com/pewdiepie-archdaemon/odysseus")
        h.setdefault("X-OpenRouter-Title", "Odysseus")
    if provider == "copilot":
        from src.copilot import copilot_headers
        for k, v in copilot_headers(None).items():
            h.setdefault(k, v)
    return h


def _provider_label(url: str, *, is_ollama_native_url_func=_is_ollama_native_url) -> str:
    """Human-friendly provider name for error messages."""
    if not url:
        return "provider"
    if _host_match(url, "anthropic.com"):
        return "Anthropic"
    if _host_match(url, "ollama.com"):
        return "Ollama Cloud"
    if _host_match(url, "x.ai"):
        return "xAI"
    if _host_match(url, "openai.com"):
        return "OpenAI"
    if _host_match(url, "openrouter.ai"):
        return "OpenRouter"
    if _host_match(url, "opencode.ai/zen/go"):
        return "OpenCode Go"
    if _host_match(url, "opencode.ai/zen"):
        return "OpenCode Zen"
    if _host_match(url, "groq.com"):
        return "Groq"
    from src.chatgpt_subscription import is_chatgpt_subscription_base
    if is_chatgpt_subscription_base(url):
        return "ChatGPT Subscription"
    from src.copilot import is_copilot_base
    if is_copilot_base(url):
        return "GitHub Copilot"
    if _host_match(url, "mistral.ai"):
        return "Mistral"
    if _host_match(url, "deepseek.com"):
        return "DeepSeek"
    if _host_match(url, "nvidia.com"):
        return "NVIDIA"
    if _host_match(url, "googleapis.com"):
        return "Google"
    if _host_match(url, "together.xyz", "together.ai"):
        return "Together"
    if _host_match(url, "fireworks.ai"):
        return "Fireworks"
    if _host_match(url, "kimi.com"):
        try:
            if "/coding" in (urlparse(url).path or ""):
                return "Kimi Code"
        except Exception:
            pass
    if is_ollama_native_url_func(url):
        return "Ollama"
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "provider"
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return "local endpoint"
    return host or "provider"
