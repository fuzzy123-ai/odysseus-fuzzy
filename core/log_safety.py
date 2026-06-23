"""Helpers for keeping sensitive data out of logs."""

from urllib.parse import urlparse, urlunparse


def redact_url(url: str) -> str:
    """Return a URL safe for logs by removing userinfo and query/fragment."""
    try:
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    except Exception:
        return "<endpoint>"
