from src.integrations import _join_integration_url


def test_join_integration_url_preserves_configured_base_path():
    assert (
        _join_integration_url("https://api.example.com/root", "/v1/items?limit=1")
        == "https://api.example.com/root/v1/items?limit=1"
    )


def test_join_integration_url_handles_root_path():
    assert _join_integration_url("https://api.example.com/root", "/") == "https://api.example.com/root/"
