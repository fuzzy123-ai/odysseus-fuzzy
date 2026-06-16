from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
