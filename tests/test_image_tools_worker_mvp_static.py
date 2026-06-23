from pathlib import Path
import base64
import json

from workers.image_tools_worker import app as worker_app


ROOT = Path(__file__).resolve().parents[1]
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-worker-smoke"


def test_image_tools_worker_mvp_contract_markers() -> None:
    app_py = (ROOT / "workers" / "image_tools_worker" / "app.py").read_text(encoding="utf-8")
    readme = (ROOT / "workers" / "image_tools_worker" / "README.md").read_text(encoding="utf-8")
    requirements = (ROOT / "workers" / "image_tools_worker" / "requirements.txt").read_text(encoding="utf-8")

    assert '"/remove-background"' in app_py
    assert '"error_code"' in app_py
    assert '"image_base64"' in app_py
    assert '"hint_mask_base64"' in app_py
    assert "dependency_missing" in app_py
    assert "payload_too_large" in app_py
    assert "invalid_image" in app_py
    assert "from rembg import new_session, remove" in app_py
    assert "if __name__ == \"__main__\":" in app_py
    assert "python app.py" in readme
    assert "Python `3.12`" in readme
    assert "rembg[cpu]" in requirements
    assert "onnxruntime" in requirements


def test_image_tools_worker_mvp_isolated_from_core_client() -> None:
    app_py = (ROOT / "workers" / "image_tools_worker" / "app.py").read_text(encoding="utf-8")

    assert "from src.image_tools_worker" not in app_py
    assert "routes/gallery_routes.py" not in app_py


def test_image_tools_worker_fake_remove_background_smoke(monkeypatch) -> None:
    calls = {}

    def fake_remove_background_bytes(image_bytes: bytes) -> bytes:
        calls["image_bytes"] = image_bytes
        return PNG_BYTES

    monkeypatch.setattr(worker_app, "remove_background_bytes", fake_remove_background_bytes)
    status_code, body = worker_app.build_remove_background_response(
        {
            "image_base64": base64.b64encode(b"input-image").decode("ascii"),
            "hint_mask_base64": base64.b64encode(b"mask-image").decode("ascii"),
        }
    )

    payload = json.loads(body.decode("utf-8"))
    assert status_code == 200
    assert base64.b64decode(payload["image_base64"]) == PNG_BYTES
    assert payload["mime_type"] == "image/png"
    assert payload["hint_mask_accepted"] is True
    assert payload["hint_mask_applied"] is False
    assert calls["image_bytes"] == b"input-image"


def test_image_tools_worker_fake_smoke_keeps_structured_invalid_image_error() -> None:
    status_code, body = worker_app.build_remove_background_response({"image_base64": "not base64"})

    payload = json.loads(body.decode("utf-8"))
    assert status_code == 400
    assert payload["error_code"] == "invalid_image"
