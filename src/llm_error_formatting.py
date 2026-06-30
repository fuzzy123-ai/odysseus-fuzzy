"""Provider-aware upstream error message formatting."""

import json
from typing import Callable


ProviderLabelFunc = Callable[[str], str]
UpstreamFormatter = Callable[[int, bytes | str, str], str]


def _format_chatgpt_subscription_error(
    status_code: int,
    text: str,
    *,
    upstream_formatter: UpstreamFormatter,
) -> str:
    if status_code in (401, 403):
        return "ChatGPT Subscription credentials expired or were rejected. Reconnect the provider."
    if status_code == 429:
        return "ChatGPT Subscription quota or rate limit was reached. Retry after the upstream limit resets."
    return upstream_formatter(status_code, text, "https://chatgpt.com/backend-api/codex")


def _format_upstream_error(
    status: int,
    body: bytes | str,
    url: str,
    *,
    provider_label_func: ProviderLabelFunc,
) -> str:
    """Turn an upstream HTTP error into a user-readable sentence."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            body = str(body)

    provider = provider_label_func(url)
    detail = ""
    try:
        parsed = json.loads(body) if body else {}
        if isinstance(parsed, dict):
            err = parsed.get("error") or parsed
            if isinstance(err, dict):
                detail = (err.get("message") or err.get("detail") or "").strip()
            elif isinstance(err, str):
                detail = err.strip()
    except Exception:
        detail = (body or "").strip()[:240]

    if status in (401, 403):
        msg = f"{provider} rejected the API key"
        if status == 403:
            msg = f"{provider} denied access (403)"
        if detail:
            msg += f" — {detail}"
        msg += ". Check Model Endpoints → {} and re-paste the key.".format(provider)
        return msg
    if status == 404:
        return f"{provider} returned 404 — check the base URL and model name." + (f" ({detail})" if detail else "")
    if status == 429:
        return f"{provider} rate-limited the request (429)." + (f" {detail}" if detail else "")
    if status >= 500:
        return f"{provider} is having an outage (HTTP {status})." + (f" {detail}" if detail else "")
    return f"{provider} returned HTTP {status}" + (f": {detail}" if detail else "")
