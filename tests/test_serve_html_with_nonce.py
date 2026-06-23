import types

import pytest
from fastapi import HTTPException

from src.app_helpers import serve_html_with_nonce


def _request_with_nonce(nonce: str = ""):
    return types.SimpleNamespace(state=types.SimpleNamespace(csp_nonce=nonce))


def test_missing_fixed_template_returns_500_not_404(tmp_path):
    missing = tmp_path / "does_not_exist.html"
    with pytest.raises(HTTPException) as exc_info:
        serve_html_with_nonce(_request_with_nonce(), str(missing))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"


def test_unreadable_template_returns_500(tmp_path):
    a_dir = tmp_path / "a_dir.html"
    a_dir.mkdir()
    with pytest.raises(HTTPException) as exc_info:
        serve_html_with_nonce(_request_with_nonce(), str(a_dir))
    assert exc_info.value.status_code == 500


def test_readable_template_injects_nonce(tmp_path):
    page = tmp_path / "page.html"
    page.write_text('<script nonce="{{CSP_NONCE}}">x</script>', encoding="utf-8")
    resp = serve_html_with_nonce(_request_with_nonce("nonce-abc"), str(page))
    body = resp.body.decode("utf-8")
    assert resp.status_code == 200
    assert "nonce-abc" in body
    assert "{{CSP_NONCE}}" not in body
