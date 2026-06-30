from email.mime.text import MIMEText

from routes import email_formatting
from routes import email_routes


def test_email_routes_keep_legacy_formatting_aliases():
    assert email_routes._envelope_recipients is email_formatting.envelope_recipients
    assert email_routes._md_to_email_html is email_formatting.markdown_to_email_html
    assert email_routes._sanitize_email_html is email_formatting.sanitize_email_html


def test_sanitize_email_html_drops_script_and_keeps_safe_formatting():
    html = email_formatting.sanitize_email_html(
        '<script>alert(1)</script><b>ok</b><a href="javascript:bad()">bad</a>'
    )

    assert html == "<html><body><b>ok</b><a>bad</a></body></html>"


def test_markdown_to_email_html_escapes_before_formatting():
    html = email_formatting.markdown_to_email_html("# Hello\n<script>x</script>\n**bold**")

    assert "<h1>Hello</h1>" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "<strong>bold</strong>" in html
    assert "<script>" not in html


def test_apply_odysseus_headers_sanitizes_kind_and_ref():
    msg = MIMEText("body")

    email_formatting.apply_odysseus_headers(msg, "agent draft!", "doc id/42?x")

    assert msg["X-Odysseus-Origin"] == "odysseus-ui"
    assert msg["X-Odysseus-Kind"] == "agent-draft-"
    assert msg["X-Odysseus-Ref"] == "doc-id-42-x"
