from types import SimpleNamespace

from routes import model_routes


def test_local_endpoint_refresh_timeout_defaults_to_ten_seconds():
    assert model_routes._endpoint_refresh_timeout(SimpleNamespace(model_refresh_timeout=None), "local") == 10.0


def test_endpoint_refresh_timeout_accepts_up_to_sixty_seconds():
    ep = SimpleNamespace(model_refresh_timeout=55)
    assert model_routes._endpoint_refresh_timeout(ep, "local") == 55.0


def test_explicit_model_list_timeout_allows_local_warmup():
    assert model_routes._explicit_model_list_timeout("http://127.0.0.1:8080/v1", "auto") == 15.0
