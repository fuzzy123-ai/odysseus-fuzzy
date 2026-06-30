import json
from unittest import mock

from routes import email_account_helpers


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
        smtp_port=kwargs.get("smtp_port", 465),
        smtp_user=kwargs.get("smtp_user", "test@example.test"),
        enabled=kwargs.get("enabled", True),
        is_default=kwargs.get("is_default", False),
    )
    for key, value in kwargs.items():
        if hasattr(row, key):
            setattr(row, key, value)
    session.add(row)
    session.commit()
    return row


def _smtp_security_mode(cfg):
    value = cfg.get("smtp_security")
    return value or ("ssl" if int(cfg.get("smtp_port") or 465) == 465 else "starttls")


def test_masked_email_config_hides_passwords_and_adds_automation_flags():
    cfg = email_account_helpers.masked_email_config(
        "alice",
        get_config=lambda owner: {
            "smtp_password": "smtp-secret",
            "imap_password": "imap-secret",
            "imap_host": "imap.example.test",
        },
        load_settings=lambda: {"email_auto_reply": True},
    )

    assert cfg["smtp_password"] == "***"
    assert cfg["imap_password"] == "***"
    assert cfg["email_auto_reply"] is True
    assert cfg["email_auto_summarize"] is False


def test_list_email_account_rows_does_not_expose_oauth_tokens():
    from src.secret_storage import encrypt as _enc

    db, factory = _make_db()
    raw_access = "ya29.private-access-token"
    raw_refresh = "1//private-refresh-token"
    _make_account(
        db,
        account_id="acct-list",
        owner="alice",
        oauth_provider="google",
        oauth_access_token=_enc(raw_access),
        oauth_refresh_token=_enc(raw_refresh),
    )
    db.close()

    with mock.patch("core.database.SessionLocal", factory):
        rows = email_account_helpers.list_email_account_rows("alice", smtp_security_mode=_smtp_security_mode)

    blob = json.dumps(rows)
    assert raw_access not in blob
    assert raw_refresh not in blob
    assert _enc(raw_access) not in blob
    assert rows[0]["oauth_provider"] == "google"
    assert "oauth_access_token" not in rows[0]
    assert "oauth_refresh_token" not in rows[0]


def test_create_and_set_default_are_owner_scoped():
    db, factory = _make_db()
    _make_account(db, account_id="alice-old", owner="alice", is_default=True)
    _make_account(db, account_id="bob-default", owner="bob", is_default=True)
    db.close()

    with mock.patch("core.database.SessionLocal", factory):
        created = email_account_helpers.create_email_account_row(
            {"name": "Alice New", "is_default": True},
            owner="alice",
            smtp_security_mode=_smtp_security_mode,
        )

    from core.database import EmailAccount

    db = factory()
    try:
        rows = {row.id: row.is_default for row in db.query(EmailAccount).all()}
    finally:
        db.close()

    assert created["ok"] is True
    assert rows["alice-old"] is False
    assert rows["bob-default"] is True
    assert rows[created["id"]] is True


def test_saved_account_test_body_decrypts_passwords_and_keeps_request_overrides():
    from src.secret_storage import encrypt as _enc

    db, factory = _make_db()
    _make_account(
        db,
        account_id="acct-test",
        owner="alice",
        imap_password=_enc("imap-secret"),
        smtp_password=_enc("smtp-secret"),
        smtp_port=587,
        smtp_security="",
    )
    db.close()

    with mock.patch("core.database.SessionLocal", factory):
        body = email_account_helpers.saved_account_test_body(
            "acct-test",
            {"account_id": "acct-test", "imap_host": "imap.override.test", "smtp_password": ""},
            smtp_security_mode=_smtp_security_mode,
        )

    assert body["imap_host"] == "imap.override.test"
    assert body["imap_password"] == "imap-secret"
    assert body["smtp_password"] == "smtp-secret"
    assert body["smtp_security"] == "starttls"
