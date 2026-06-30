from types import SimpleNamespace

from routes.model_probe_helpers import (
    anthropic_model_ids_from_payload,
    append_curated_probe_models,
    curated_probe_fallback_models,
    model_ids_from_listing_payload,
    ollama_native_ping_urls,
    ollama_native_probe_root,
    ollama_tag_model_ids_from_payload,
    ping_result_from_response,
    probe_base_ping_with_models_fallback,
    probe_ollama_native_ping,
    should_try_models_url_after_ping,
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


def test_curated_probe_fallback_models_returns_copy_for_matched_endpoint():
    provider_curated = {"zai-coding": ["glm-5.1", "glm-4.5-air"]}

    curated_key, fallback = curated_probe_fallback_models(
        "https://z.ai/api/coding",
        match_provider_curated_func=_match_provider_curated,
        provider_curated=provider_curated,
    )
    fallback.append("local-mutation")

    assert curated_key == "zai-coding"
    assert fallback == ["glm-5.1", "glm-4.5-air", "local-mutation"]
    assert provider_curated["zai-coding"] == ["glm-5.1", "glm-4.5-air"]


def test_curated_probe_fallback_models_returns_empty_for_unmatched_endpoint():
    curated_key, fallback = curated_probe_fallback_models(
        "https://api.example.com/v1",
        match_provider_curated_func=lambda _base_url, _provider: None,
        provider_curated={"zai-coding": ["glm-4.5-air"]},
    )

    assert curated_key is None
    assert fallback == []


def test_curated_probe_fallback_models_returns_empty_for_missing_curated_list():
    curated_key, fallback = curated_probe_fallback_models(
        "https://z.ai/api/coding",
        match_provider_curated_func=_match_provider_curated,
        provider_curated={},
    )

    assert curated_key == "zai-coding"
    assert fallback == []


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


def test_ollama_native_ping_urls_returns_native_version_and_tags_urls():
    assert ollama_native_ping_urls("http://localhost:11434/") == [
        "http://localhost:11434/api/version",
        "http://localhost:11434/api/tags",
    ]


def test_ollama_native_ping_urls_ignores_empty_root():
    assert ollama_native_ping_urls(None) == []
    assert ollama_native_ping_urls("") == []


def test_probe_ollama_native_ping_returns_first_reachable_result():
    calls = []

    def fake_get(url, timeout=None, verify=None):
        calls.append((url, timeout, verify))
        status = 503 if len(calls) == 1 else 200
        return _response(status)

    result, last_error = probe_ollama_native_ping(
        ["http://localhost:11434/api/version", "http://localhost:11434/api/tags"],
        timeout=1.5,
        http_get_func=fake_get,
        llm_verify_func=lambda: "verify-token",
        ping_result_func=ping_result_from_response,
    )

    assert result == {"reachable": True, "status_code": 200, "error": None}
    assert last_error == "HTTP 503"
    assert calls == [
        ("http://localhost:11434/api/version", 1.5, "verify-token"),
        ("http://localhost:11434/api/tags", 1.5, "verify-token"),
    ]


def test_probe_ollama_native_ping_returns_last_transport_error():
    def fake_get(_url, timeout=None, verify=None):
        raise RuntimeError("native ping failed with a long but safe message")

    result, last_error = probe_ollama_native_ping(
        ["http://localhost:11434/api/version"],
        timeout=1.5,
        http_get_func=fake_get,
        llm_verify_func=lambda: True,
        ping_result_func=ping_result_from_response,
    )

    assert result is None
    assert last_error == "native ping failed with a long but safe message"


def test_probe_ollama_native_ping_ignores_empty_urls():
    result, last_error = probe_ollama_native_ping(
        [],
        timeout=1.5,
        http_get_func=lambda *_args, **_kwargs: _response(200),
        llm_verify_func=lambda: True,
        ping_result_func=ping_result_from_response,
    )

    assert result is None
    assert last_error is None


def test_probe_base_ping_with_models_fallback_returns_base_success():
    calls = []

    def fake_get(url, headers=None, timeout=None, verify=None):
        calls.append((url, headers, timeout, verify))
        return _response(204)

    result, last_error = probe_base_ping_with_models_fallback(
        "https://api.example.com/v1",
        {"Authorization": "Bearer key"},
        timeout=1.5,
        http_get_func=fake_get,
        llm_verify_func=lambda: "verify-token",
        ping_result_func=ping_result_from_response,
        should_try_models_url_func=should_try_models_url_after_ping,
        safe_build_models_url_func=lambda base: base.rstrip("/") + "/models",
    )

    assert result == {"reachable": True, "status_code": 204, "error": None}
    assert last_error is None
    assert calls == [("https://api.example.com/v1", {"Authorization": "Bearer key"}, 1.5, "verify-token")]


def test_probe_base_ping_with_models_fallback_uses_models_url_for_non_auth_4xx():
    calls = []

    def fake_get(url, headers=None, timeout=None, verify=None):
        calls.append(url)
        return _response(404 if len(calls) == 1 else 200)

    result, last_error = probe_base_ping_with_models_fallback(
        "https://api.example.com/v1",
        {},
        timeout=1.5,
        http_get_func=fake_get,
        llm_verify_func=lambda: True,
        ping_result_func=ping_result_from_response,
        should_try_models_url_func=should_try_models_url_after_ping,
        safe_build_models_url_func=lambda base: base.rstrip("/") + "/models",
    )

    assert result == {"reachable": True, "status_code": 200, "error": None}
    assert last_error == "HTTP 404"
    assert calls == ["https://api.example.com/v1", "https://api.example.com/v1/models"]


def test_probe_base_ping_with_models_fallback_returns_auth_failure_without_models_probe():
    calls = []

    def fake_get(url, headers=None, timeout=None, verify=None):
        calls.append(url)
        return _response(401)

    result, last_error = probe_base_ping_with_models_fallback(
        "https://api.example.com/v1",
        {},
        timeout=1.5,
        http_get_func=fake_get,
        llm_verify_func=lambda: True,
        ping_result_func=ping_result_from_response,
        should_try_models_url_func=should_try_models_url_after_ping,
        safe_build_models_url_func=lambda base: base.rstrip("/") + "/models",
    )

    assert result == {"reachable": False, "status_code": 401, "error": "HTTP 401"}
    assert last_error == "HTTP 401"
    assert calls == ["https://api.example.com/v1"]


def test_probe_base_ping_with_models_fallback_returns_transport_error():
    result, last_error = probe_base_ping_with_models_fallback(
        "https://api.example.com/v1",
        {},
        timeout=1.5,
        http_get_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("base ping failed")),
        llm_verify_func=lambda: True,
        ping_result_func=ping_result_from_response,
        should_try_models_url_func=should_try_models_url_after_ping,
        safe_build_models_url_func=lambda base: base.rstrip("/") + "/models",
    )

    assert result is None
    assert last_error == "base ping failed"


def test_ollama_tag_model_ids_from_payload_reads_name_or_model():
    assert ollama_tag_model_ids_from_payload({
        "models": [{"name": "llama3:8b"}, {"model": "qwen3:4b"}, {"id": "ignored"}],
    }) == ["llama3:8b", "qwen3:4b"]


def test_ollama_tag_model_ids_from_payload_returns_empty_for_unknown_shape():
    assert ollama_tag_model_ids_from_payload({"data": [{"id": "ignored"}]}) == []


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


def test_should_try_models_url_after_ping_allows_non_auth_4xx():
    assert should_try_models_url_after_ping(400) is True
    assert should_try_models_url_after_ping(404) is True


def test_should_try_models_url_after_ping_blocks_auth_and_non_4xx():
    assert should_try_models_url_after_ping(401) is False
    assert should_try_models_url_after_ping(403) is False
    assert should_try_models_url_after_ping(500) is False
    assert should_try_models_url_after_ping(None) is False
    assert should_try_models_url_after_ping("not-a-code") is False
