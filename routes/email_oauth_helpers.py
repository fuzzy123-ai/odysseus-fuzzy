"""Google OAuth helper functions for email routes."""

import time
import urllib.parse


GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"


def google_oauth_redirect_uri(*, configured_uri: str | None, request_host: str) -> str:
    return configured_uri or f"http://{request_host or 'localhost:7000'}/api/email/oauth/google/callback"


def build_google_oauth_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://mail.google.com/ email",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{params}"


def exchange_google_oauth_code(*, code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    import httpx

    resp = httpx.post(
        GOOGLE_OAUTH_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_google_oauth_userinfo(access_token: str) -> dict:
    import httpx

    try:
        resp = httpx.get(
            GOOGLE_OAUTH_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.is_success:
            return resp.json()
    except Exception:
        pass
    return {}


def apply_google_oauth_tokens(
    *,
    account_id: str,
    owner: str,
    token_data: dict,
    userinfo: dict,
    now: int | None = None,
) -> str:
    """Persist encrypted Google OAuth tokens to the intended email account."""
    from core.database import EmailAccount, SessionLocal
    from src.secret_storage import encrypt as _enc

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expiry = str(int(now if now is not None else time.time()) + token_data.get("expires_in", 3600))
    email_addr = userinfo.get("email", "")
    display_name = userinfo.get("name", "")

    db = SessionLocal()
    try:
        row = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
        if not row:
            return "account_not_found"
        if owner and row.owner and row.owner != owner:
            return "ownership_error"

        row.oauth_provider = "google"
        row.oauth_access_token = _enc(access_token)
        if refresh_token:
            row.oauth_refresh_token = _enc(refresh_token)
        row.oauth_token_expiry = expiry

        if not row.imap_host:
            row.imap_host = "imap.gmail.com"
            row.imap_port = 993
            row.imap_starttls = False
        if not row.smtp_host:
            row.smtp_host = "smtp.gmail.com"
            row.smtp_port = 587
        if email_addr:
            if not row.imap_user:
                row.imap_user = email_addr
            if not row.smtp_user:
                row.smtp_user = email_addr
            if not row.from_address:
                row.from_address = email_addr
            if not row.name or row.name == row.id:
                row.name = email_addr
        if display_name and not row.display_name:
            row.display_name = display_name
        db.commit()
        return "success"
    finally:
        db.close()
