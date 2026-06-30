from routes.model_probe_helpers import append_curated_probe_models


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
