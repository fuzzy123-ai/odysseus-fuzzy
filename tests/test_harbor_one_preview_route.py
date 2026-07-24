from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_harbor_one_preview_route_serves_frontpage_v3_without_root_cutover() -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert '@app.get("/harbor-one")' in app_source
    assert '@app.get("/harbor-one/{path:path}")' in app_source
    assert "static/frontpage-v3/index.html" in app_source
    assert "async def serve_index" in app_source
    assert 'static/index.html"' in app_source
    assert (ROOT / "static" / "frontpage-v3" / "index.html").is_file()
