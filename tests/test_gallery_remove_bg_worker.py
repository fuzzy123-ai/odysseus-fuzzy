import base64

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.gallery_routes as gallery_routes
from src.image_tools_worker import (
    ImageToolsWorkerError,
    ImageToolsWorkerErrorCode,
    ImageToolsWorkerResult,
    ImageToolsWorkerSettings,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\nroute-png"


def _worker_settings(*, mode: str = "docker", url: str = "http://127.0.0.1:8123/remove-background", legacy_fallback: bool = False):
    return ImageToolsWorkerSettings.from_env(
        {
            "IMAGE_TOOLS_WORKER_MODE": mode,
            "IMAGE_TOOLS_WORKER_URL": url,
            "IMAGE_TOOLS_WORKER_TIMEOUT_SEC": "8",
            "IMAGE_TOOLS_WORKER_MAX_MB": "5",
            "IMAGE_TOOLS_WORKER_LEGACY_FALLBACK": "true" if legacy_fallback else "false",
        }
    )


def _result_error(code: ImageToolsWorkerErrorCode, message: str) -> ImageToolsWorkerResult:
    return ImageToolsWorkerResult(ok=False, error=ImageToolsWorkerError(code=code, message=message))


def _client(monkeypatch, *, settings, client_class, privilege=None):
    monkeypatch.setattr(
        gallery_routes,
        "ImageToolsWorkerSettings",
        type("SettingsFactory", (), {"from_env": staticmethod(lambda: settings)}),
    )
    monkeypatch.setattr(gallery_routes, "ImageToolsWorkerClient", client_class)
    monkeypatch.setattr(gallery_routes, "require_privilege", privilege or (lambda request, name: "alice"))
    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    return TestClient(app)


def test_remove_bg_uses_worker_when_configured_and_keeps_editor_payload(monkeypatch):
    calls = {}

    class FakeClient:
        def __init__(self, settings):
            calls["settings"] = settings

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            calls["image_bytes"] = image_bytes
            calls["hint_mask_bytes"] = hint_mask_bytes
            return ImageToolsWorkerResult(ok=True, image_bytes=PNG_BYTES)

    client = _client(monkeypatch, settings=_worker_settings(), client_class=FakeClient)
    response = client.post(
        "/api/image/remove-bg",
        json={
            "image": base64.b64encode(b"source-image").decode("ascii"),
            "hint_mask": base64.b64encode(b"mask-image").decode("ascii"),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"image": base64.b64encode(PNG_BYTES).decode("ascii")}
    assert calls["image_bytes"] == b"source-image"
    assert calls["hint_mask_bytes"] == b"mask-image"


def test_remove_bg_returns_structured_not_configured_when_disabled(monkeypatch):
    class FakeClient:
        def __init__(self, _settings):
            pass

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            return _result_error(ImageToolsWorkerErrorCode.NOT_CONFIGURED, "Background removal worker is not configured.")

    monkeypatch.setattr(
        gallery_routes,
        "_legacy_remove_background_response",
        lambda image_bytes, hint_bytes=None: (_ for _ in ()).throw(AssertionError("legacy fallback should stay off by default")),
    )
    client = _client(monkeypatch, settings=_worker_settings(mode="disabled", url="", legacy_fallback=False), client_class=FakeClient)
    response = client.post("/api/image/remove-bg", json={"image": base64.b64encode(b"img").decode("ascii")})

    assert response.status_code == 503
    assert response.json()["error_code"] == "not_configured"


def test_remove_bg_maps_worker_unreachable_cleanly(monkeypatch):
    class FakeClient:
        def __init__(self, _settings):
            pass

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            return _result_error(ImageToolsWorkerErrorCode.WORKER_UNREACHABLE, "worker unreachable")

    client = _client(monkeypatch, settings=_worker_settings(), client_class=FakeClient)
    response = client.post("/api/image/remove-bg", json={"image": base64.b64encode(b"img").decode("ascii")})

    assert response.status_code == 502
    assert response.json() == {"error": "worker unreachable", "error_code": "worker_unreachable"}


def test_remove_bg_keeps_permission_gate_before_worker(monkeypatch):
    calls = {"count": 0}

    class FakeClient:
        def __init__(self, _settings):
            calls["count"] += 1

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            return ImageToolsWorkerResult(ok=True, image_bytes=PNG_BYTES)

    def deny(_request, _name):
        raise HTTPException(403, "denied")

    client = _client(monkeypatch, settings=_worker_settings(), client_class=FakeClient, privilege=deny)
    response = client.post("/api/image/remove-bg", json={"image": base64.b64encode(b"img").decode("ascii")})

    assert response.status_code == 403
    assert calls["count"] == 0


def test_remove_bg_maps_payload_too_large(monkeypatch):
    class FakeClient:
        def __init__(self, _settings):
            pass

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            return _result_error(ImageToolsWorkerErrorCode.PAYLOAD_TOO_LARGE, "too large")

    client = _client(monkeypatch, settings=_worker_settings(), client_class=FakeClient)
    response = client.post("/api/image/remove-bg", json={"image": base64.b64encode(b"img").decode("ascii")})

    assert response.status_code == 413
    assert response.json()["error_code"] == "payload_too_large"


def test_remove_bg_does_not_use_legacy_fallback_by_default(monkeypatch):
    calls = {"legacy": 0}

    class FakeClient:
        def __init__(self, _settings):
            pass

        def remove_background(self, image_bytes, hint_mask_bytes=None):
            return _result_error(ImageToolsWorkerErrorCode.DEPENDENCY_MISSING, "dependency missing")

    def forbidden_legacy(image_bytes, hint_bytes=None):
        calls["legacy"] += 1
        return {"image": "should-not-happen"}

    monkeypatch.setattr(gallery_routes, "_legacy_remove_background_response", forbidden_legacy)
    client = _client(monkeypatch, settings=_worker_settings(legacy_fallback=False), client_class=FakeClient)
    response = client.post("/api/image/remove-bg", json={"image": base64.b64encode(b"img").decode("ascii")})

    assert response.status_code == 503
    assert response.json()["error_code"] == "dependency_missing"
    assert calls["legacy"] == 0
