from types import SimpleNamespace

from routes.model_probe_helpers import (
    anthropic_model_ids_from_payload,
    append_curated_probe_models,
    model_ids_from_listing_payload,
    ollama_native_probe_root,
    ping_result_from_response,
)


def _host_match(base_url, domain):
    return domain in base_url


def _match_provider_curated(base_url, provider):
    if "z.ai" in base_url and "/api/coding" in base_url:
        return "zai-coding"
    if "kimi.com" in base_url and "/coding" in base_url:
        return "kimi-code"
    return provider


def test_append_curated_probe_models_adds_zai_coding_omissions():
    result = append_curated_probe_models(
        "https://z.ai/api/coding",
        ["glm-5.1"],
        host_match_func=_host_match,
        match_provider_curated_func=_match_provider_curated,
        provider_curated={"zai-coding": ["glm-5.1", "glm-4.5-air"]},
    )

    assert result == ["glm-5.1", "glm-4.5-air"]


def test_append_curated_probe_models_keeps_prefix_variants_unique():
    result = append_curated_probe_models(
        "https://z.ai/api/coding",
        ["glm-4.5-air-preview"],
        host_match_func=_host_match,
        match_provider_curated_func=_match_provider_curated,
        provider_curated={"zai-coding": ["glm-4.5-air"]},
    )

    assert result == ["glm-4.5-air-preview"]


def test_append_curated_probe_models_ignores_unmatched_endpoint():
    result = append_curated_probe_models(
        "https://api.example.com/v1",
        ["custom-model"],
        host_match_func=_host_match,
        match_provider_curated_func=_match_provider_curated,
        provider_curated={"zai-coding": ["glm-4.5-air"]},
    )

    assert result == ["custom-model"]


def _response(status_code, headers=None):
    return SimpleNamespace(status_code=status_code, headers=headers or {})


def test_ping_result_from_response_reports_success():
    assert ping_result_from_response(_response(204)) == {
        "reachable": True,
        "status_code": 204,
        "error": None,
    }


def test_ping_result_from_response_detects_odysseus_login_redirect():
    result = ping_result_from_response(_response(302, {"location": "/login?next=/"}))

    assert result["reachable"] is False
    assert result["status_code"] == 302
    assert "not a model server" in result["error"]


def test_ping_result_from_response_reports_generic_redirect():
    assert ping_result_from_response(_response(301, {"location": "https://elsewhere.example/"})) == {
        "reachable": False,
        "status_code": 301,
        "error": "HTTP 301 redirect",
    }


def test_ping_result_from_response_reports_http_error():
    assert ping_result_from_response(_response(503)) == {
        "reachable": False,
        "status_code": 503,
        "error": "HTTP 503",
    }


def test_ollama_native_probe_root_detects_default_port_and_strips_v1():
    assert ollama_native_probe_root("http://localhost:11434/v1") == "http://localhost:11434"


def test_ollama_native_probe_root_strips_api_suffix():
    assert ollama_native_probe_root("https://ollama.example.com/api") == "https://ollama.example.com"


def test_ollama_native_probe_root_ignores_openai_style_proxy():
    assert ollama_native_probe_root("https://api.example.com/v1") is None


def test_model_ids_from_listing_payload_reads_openai_data_ids():
    assert model_ids_from_listing_payload({
        "data": [{"id": "gpt-4o"}, {"id": ""}, {"name": "ignored"}],
    }) == ["gpt-4o"]


def test_model_ids_from_listing_payload_reads_ollama_name_or_model():
    assert model_ids_from_listing_payload({
        "models": [{"name": "llama3:8b"}, {"model": "qwen3:4b"}, {"id": "ignored"}],
    }) == ["llama3:8b", "qwen3:4b"]


def test_model_ids_from_listing_payload_returns_empty_for_unknown_shape():
    assert model_ids_from_listing_payload({"items": [{"id": "ignored"}]}) == []


def test_anthropic_model_ids_from_payload_reads_data_ids():
    assert anthropic_model_ids_from_payload({
        "data": [{"id": "claude-sonnet-4-5"}, {"name": "ignored"}, {"id": ""}],
    }) == ["claude-sonnet-4-5"]


def test_anthropic_model_ids_from_payload_returns_empty_for_unknown_shape():
    assert anthropic_model_ids_from_payload({"models": [{"id": "ignored"}]}) == []
