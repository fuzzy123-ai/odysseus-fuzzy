"""Deterministic cache keys for LLM response caching."""

import hashlib
import json
from typing import Dict, List


def _get_cache_key(
    url: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
) -> str:
    """Generate a stable cache key for semantically identical LLM requests."""
    hashable_messages = []
    for message in messages:
        hashable_messages.append(tuple(sorted(message.items())))

    content = json.dumps(
        {
            "url": url,
            "model": model,
            "messages": hashable_messages,
            "temp": temperature,
            "max_tokens": max_tokens,
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()
