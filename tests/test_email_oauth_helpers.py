import json
import urllib.parse
from unittest import mock

from routes import email_oauth_helpers


def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory(), factory


def _make_account(session, account_id="acct-1", owner="alice", **kwargs):
    from core.database import EmailAccount

    row = EmailAccount(
        id=account_id,
        owner=owner,
        name=kwargs.get("name", "Test"),
        from_address=kwargs.get("from_address", "test@example.test"),
        imap_host=kwargs.get("imap_host", "imap.example.test"),
        imap_port=kwargs.get("imap_port", 993),
        imap_user=kwargs.get("imap_user", "test@example.test"),
        smtp_host=kwargs.get("smtp_host", "smtp.example.test"),
        smtp_port=kwargs.get("smtp_port", 587),
        smtp_user=kwargs.get("smtp_user", "test@example.test"),
    )
    for key, value in kwargs.items():
        if hasattr(row, key):
            setattr(row, key, value)
    session.add(row)
    session.commit()
    return row


def test_google_oauth_redirect_uri_uses_config_or_request_host():
    assert email_oauth_helpers.google_oauth_redirect_uri(
        configured_uri="https://example.test/callback",
        request_host="ignored.test",
    ) == "https://example.test/callback"

    assert email_oauth_helpers.google_oauth_redirect_uri(
        configured_uri="",
        request_host="localhost:7000",
    ) == "http://localhost:7000/api/email/oauth/google/callback"


def test_build_google_oauth_authorize_url_contains_expected_params_without_secrets():
    url = email_oauth_helpers.build_google_oauth_authorize_url(
        client_id="client-id",
        redirect_uri="https://example.test/callback",
        state="signed-state",
    )

    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["https://example.test/callback"]
    assert params["state"] == ["signed-state"]
    assert params["scope"] == ["https://mail.google.com/ email"]
    assert "client_secret" not in params


def test_apply_google_oauth_tokens_rejects_owner_mismatch_without_writing():
    from core.database import EmailAccount

    db, factory = _make_db()
    _make_account(db, account_id="acct-x", owner="alice")
    db.close()

    with mock.patch("core.database.SessionLocal", factory):
        result = email_oauth_helpers.apply_google_oauth_tokens(
            account_id="acct-x",
            owner="bob",
            token_data={"access_token": "ya29.attacker", "refresh_token": "r"},
            userinfo={"email": "bob@example.test", "name": "Bob"},
            now=100,
        )

    verify_db = factory()
    try:
        row = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-x").first()
        assert row.oauth_access_token is None
        assert row.oauth_refresh_token is None
    finally:
        verify_db.close()
    assert result == "ownership_error"


def test_apply_google_oauth_tokens_encrypts_tokens_and_autofills_google_settings():
    from core.database import EmailAccount
    from src.secret_storage import decrypt as _dec

    db, factory = _make_db()
    _make_account(
        db,
        account_id="acct-v",
        owner="alice",
        name="acct-v",
        imap_host="",
        smtp_host="",
        imap_user="",
        smtp_user="",
        from_address="",
    )
    _make_account(db, account_id="acct-other", owner="alice")
    db.close()

    raw_access = "ya29.legit_access_token"
    raw_refresh = "1//legit_refresh_token"
    with mock.patch("core.database.SessionLocal", factory):
        result = email_oauth_helpers.apply_google_oauth_tokens(
            account_id="acct-v",
            owner="alice",
            token_data={"access_token": raw_access, "refresh_token": raw_refresh, "expires_in": 3600},
            userinfo={"email": "alice@example.test", "name": "Alice"},
            now=100,
        )

    verify_db = factory()
    try:
        target = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-v").first()
        other = verify_db.query(EmailAccount).filter(EmailAccount.id == "acct-other").first()
        blob = json.dumps({
            "target_access": target.oauth_access_token,
            "target_refresh": target.oauth_refresh_token,
            "other_access": other.oauth_access_token,
        })
        assert raw_access not in blob
        assert raw_refresh not in blob
        assert _dec(target.oauth_access_token) == raw_access
        assert _dec(target.oauth_refresh_token) == raw_refresh
        assert target.oauth_provider == "google"
        assert target.oauth_token_expiry == "3700"
        assert target.imap_host == "imap.gmail.com"
        assert target.smtp_host == "smtp.gmail.com"
        assert target.imap_user == "alice@example.test"
        assert target.display_name == "Alice"
        assert other.oauth_access_token is None
    finally:
        verify_db.close()
    assert result == "success"
