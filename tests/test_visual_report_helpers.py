from src.visual_report_helpers import (
    _apply_heading_ids,
    _extract_headings,
    _extract_report_title,
    _is_icon_or_logo_url,
    _md_to_html,
)


def test_md_to_html_sanitizes_active_content_and_autolinks() -> None:
    rendered = _md_to_html(
        'See https://example.com\n\n'
        '<script>alert(1)</script><img src="x" onerror="alert(2)">'
    )

    assert "<script" not in rendered
    assert "onerror" not in rendered
    assert 'href="https://example.com"' in rendered
    assert 'target="_blank"' in rendered


def test_heading_extraction_and_id_application_share_slugs() -> None:
    headings = _extract_headings("## One Thing\n\n### One Thing\n\n## A [Link](https://x.test)")
    html = _apply_heading_ids("<h2>One Thing</h2><h3>One Thing</h3><h2>A Link</h2>", headings)

    assert [h["slug"] for h in headings] == ["one-thing", "one-thing-1", "a-link"]
    assert 'id="one-thing"' in html
    assert 'id="one-thing-1"' in html
    assert 'id="a-link"' in html


def test_report_title_skips_generic_headings_and_strips_selected_heading() -> None:
    title, body = _extract_report_title("# Executive Summary\n\n## Specific Finding\n\nBody", "fallback")

    assert title == "Specific Finding"
    assert "## Specific Finding" not in body
    assert "# Executive Summary" in body


def test_icon_logo_filter_uses_path_boundaries() -> None:
    assert _is_icon_or_logo_url("https://cdn.example.com/assets/logo.svg")
    assert _is_icon_or_logo_url("https://cdn.example.com/favicon.ico")
    assert not _is_icon_or_logo_url("https://cdn.example.com/photos/iconic-moment.jpg")
    assert not _is_icon_or_logo_url("https://cdn.example.com/logos-history.png")
