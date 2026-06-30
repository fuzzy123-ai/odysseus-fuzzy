"""Native Ollama URL and payload helpers.

The orchestration paths stay in ``src.llm_core``.  This module only contains
pure URL detection, message normalization and payload parsing/building helpers
that are also re-exported from ``src.llm_core`` for compatibility.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

from src.model_context import DEFAULT_CONTEXT

logger = logging.getLogger(__name__)


def _host_match(url: str, *domains: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_ollama_native_url(url: str) -> bool:
    """Return True for native Ollama API URLs, including Ollama Cloud."""
    try:
        parsed = urlparse(url or "")
    except Exception as e:
        logger.warning("Failed to parse URL for Ollama detection", exc_info=e)
        return False
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if _host_match(url, "ollama.com"):
        return True
    if path.startswith("/v1"):
        return False
    local_ollama_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or parsed.port == 11434
    return local_ollama_host and (path == "" or path == "/api" or path.startswith("/api/"))


def _is_ollama_openai_compat_url(url: str) -> bool:
    """Return True for local Ollama's OpenAI-compatible /v1 surface."""
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    local_ollama_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or parsed.port == 11434
    return local_ollama_host and (path == "/v1" or path.startswith("/v1/"))


def _ollama_api_root(url: str) -> str:
    """Return a native Ollama API root such as https://ollama.com/api."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api/chat"):
        return url[: -len("/chat")]
    if path.endswith("/api/tags"):
        return url[: -len("/tags")]
    if path.endswith("/api/generate"):
        return url[: -len("/generate")]
    if path.endswith("/api"):
        return url
    if path == "":
        return url + "/api"
    if _host_match(url, "ollama.com"):
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://ollama.com"
        return root.rstrip("/") + "/api"
    return url


def _normalize_ollama_url(url: str) -> str:
    """Ensure a native Ollama URL points at /api/chat."""
    base = _ollama_api_root(url)
    return base.rstrip("/") + "/chat"


def _ollama_normalize_messages(messages: List[Dict]) -> List[Dict]:
    """Adapt OpenAI-style messages to native Ollama ``/api/chat``."""
    out: List[Dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        nm = dict(m)
        tcs = nm.get("tool_calls")
        if tcs:
            new_calls = []
            for tc in tcs:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                call: Dict = {"function": {"name": fn.get("name", ""), "arguments": args or {}}}
                if tc.get("id"):
                    call["id"] = tc["id"]
                new_calls.append(call)
            nm["tool_calls"] = new_calls
        content = nm.get("content")
        images = list(nm.get("images") or [])
        if isinstance(content, list):
            text_parts: List[str] = []
            for block in content:
                if not isinstance(block, dict):
                    if block:
                        text_parts.append(str(block))
                    continue
                if block.get("type") == "text":
                    text = block.get("text")
                    if text:
                        text_parts.append(str(text))
                elif block.get("type") == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        _, _, b64 = url.partition(",")
                        if b64:
                            images.append(b64)
                    elif url:
                        logger.warning("Skipping non-data image_url for native Ollama images[]: %s", url[:80])
            nm["content"] = "\n".join(text_parts).strip()
        if images:
            nm["images"] = images
        out.append(nm)
    return out


_ollama_normalize_tool_messages = _ollama_normalize_messages


def _build_ollama_payload(
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
    stream: bool = False,
    tools: Optional[List[Dict]] = None,
    num_ctx: Optional[int] = None,
) -> Dict:
    """Build the JSON payload for Ollama's /api/chat endpoint."""
    payload: Dict = {
        "model": model,
        "messages": _ollama_normalize_messages(messages),
        "stream": stream,
    }
    options: Dict = {}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens and max_tokens > 0:
        options["num_predict"] = max_tokens
    if num_ctx is not None and num_ctx > 0 and num_ctx != DEFAULT_CONTEXT:
        options["num_ctx"] = num_ctx
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    return payload


def _parse_ollama_response(data: dict) -> str:
    message = data.get("message") or {}
    return message.get("content") or data.get("response") or ""
