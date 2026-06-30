import json
from types import SimpleNamespace

from routes.chat_endpoint_helpers import (
    _endpoint_cache_contains_model,
    _is_image_model_name,
    _session_url_matches_endpoint,
)


def test_session_url_matches_endpoint_variants():
    assert _session_url_matches_endpoint(
        "http://localhost:11434/v1/chat/completions",
        "http://localhost:11434/v1",
    )
    assert _session_url_matches_endpoint(
        "http://localhost:11434/v1/models",
        "http://localhost:11434/v1",
    )
    assert not _session_url_matches_endpoint("", "http://localhost:11434/v1")


def test_endpoint_cache_contains_selected_model_only_when_known():
    endpoint = SimpleNamespace(cached_models=json.dumps(["sdxl-local", "qwen3"]))

    assert _endpoint_cache_contains_model(endpoint, "sdxl-local")
    assert not _endpoint_cache_contains_model(endpoint, "missing")


def test_endpoint_cache_malformed_or_empty_is_unknown_not_negative():
    assert _endpoint_cache_contains_model(SimpleNamespace(cached_models=None), "anything")
    assert _endpoint_cache_contains_model(SimpleNamespace(cached_models="not json"), "anything")
    assert _endpoint_cache_contains_model(SimpleNamespace(cached_models=[]), "anything")


def test_image_model_name_prefixes():
    assert _is_image_model_name("dall-e-3")
    assert _is_image_model_name("gpt-image-1")
    assert not _is_image_model_name("qwen3.5")
