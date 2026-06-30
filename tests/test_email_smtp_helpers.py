from routes import email_routes
from routes import email_smtp_helpers


def _cfg():
    return {
        "from_address": "bot@example.test",
        "display_name": "Odysseus",
        "smtp_host": "smtp.example.test",
        "smtp_user": "bot@example.test",
        "smtp_password": "not-a-real-secret",
    }


def _html_payload(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
    return ""


def test_email_routes_keep_legacy_smtp_aliases():
    assert email_routes._smtp_ready is email_smtp_helpers.smtp_ready
    assert email_routes._resolve_send_config is email_smtp_helpers.resolve_send_config
    assert email_routes._build_outbound_email_message is email_smtp_helpers.build_outbound_email_message
    assert email_routes._build_draft_message is email_smtp_helpers.build_draft_message


def test_smtp_ready_accepts_password_or_oauth_accounts():
    base = {
        "from_address": "bot@example.test",
        "smtp_host": "smtp.example.test",
        "smtp_user": "bot@example.test",
    }

    assert email_smtp_helpers.smtp_ready({**base, "smtp_password": "x"}) is True
    assert email_smtp_helpers.smtp_ready({**base, "oauth_provider": "google"}) is True
    assert email_smtp_helpers.smtp_ready(base) is False
    assert email_smtp_helpers.smtp_ready({**base, "smtp_host": ""}) is False


def test_build_outbound_message_sanitizes_html_and_returns_envelope_recipients():
    msg, recipients = email_smtp_helpers.build_outbound_email_message(
        _cfg(),
        to='"Smith, John" <john@example.test>',
        cc="jane@example.test",
        bcc="hidden@example.test",
        subject="Hello",
        body="plain",
        body_html='<script>alert(1)</script><b>safe</b>',
        in_reply_to="<old@example.test>",
        references="<old@example.test>",
        odysseus_kind="agent draft!",
        odysseus_ref="doc id/42?x",
        include_message_id=True,
    )

    html = _html_payload(msg)
    assert recipients == ["john@example.test", "jane@example.test", "hidden@example.test"]
    assert msg["From"] == "Odysseus <bot@example.test>"
    assert msg["To"] == '"Smith, John" <john@example.test>'
    assert msg["Cc"] == "jane@example.test"
    assert msg["Message-ID"]
    assert msg["X-Odysseus-Origin"] == "odysseus-ui"
    assert msg["X-Odysseus-Kind"] == "agent-draft-"
    assert msg["X-Odysseus-Ref"] == "doc-id-42-x"
    assert "<script>" not in html
    assert "<b>safe</b>" in html


def test_build_outbound_message_uses_mixed_container_for_attachments():
    msg, recipients = email_smtp_helpers.build_outbound_email_message(
        _cfg(),
        to="john@example.test",
        body="body",
        attachments=[{"name": "stub.txt"}],
    )

    assert recipients == ["john@example.test"]
    assert msg.get_content_type() == "multipart/mixed"
    assert msg.get_payload()[0].get_content_type() == "multipart/alternative"


def test_build_draft_message_sanitizes_html_and_keeps_bcc_header():
    msg = email_smtp_helpers.build_draft_message(
        _cfg(),
        to="john@example.test",
        cc="jane@example.test",
        bcc="hidden@example.test",
        subject="Draft",
        body="plain",
        body_html='<script>alert(1)</script><b>safe</b>',
        in_reply_to="<old@example.test>",
        references="<old@example.test>",
    )

    html = _html_payload(msg)
    assert msg.get_content_type() == "multipart/alternative"
    assert msg["Bcc"] == "hidden@example.test"
    assert msg["In-Reply-To"] == "<old@example.test>"
    assert msg["References"] == "<old@example.test>"
    assert "<script>" not in html
    assert "<b>safe</b>" in html
