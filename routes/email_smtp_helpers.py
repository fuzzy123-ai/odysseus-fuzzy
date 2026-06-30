"""SMTP account resolution and MIME message builders for email routes."""

import email.utils
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from routes.email_formatting import (
    apply_odysseus_headers,
    envelope_recipients,
    markdown_to_email_html,
    sanitize_email_html,
)
from routes.email_helpers import _get_email_config

logger = logging.getLogger(__name__)


def smtp_ready(cfg: dict) -> bool:
    if not cfg.get("smtp_host") or not cfg.get("smtp_user"):
        return False
    return bool(cfg.get("smtp_password") or cfg.get("oauth_provider"))


def resolve_send_config(account_id: str | None = None, owner: str = "") -> dict:
    """Resolve an account for outbound SMTP.

    If the caller explicitly picked an account, use only that account and
    return a clear error when it cannot send. If no account was picked and
    the default is receive-only, fall back to the first SMTP-capable account
    owned by the same user.
    """
    cfg = _get_email_config(account_id, owner=owner)
    if smtp_ready(cfg):
        return cfg
    if account_id:
        raise ValueError(f"Email account {cfg.get('account_name') or account_id} has no SMTP configured")
    try:
        from core.database import EmailAccount as _EA
        from core.database import SessionLocal as _SL
        from sqlalchemy import and_, or_

        db = _SL()
        try:
            q = db.query(_EA).filter(_EA.enabled == True)  # noqa: E712
            if owner:
                unowned = or_(_EA.owner == None, _EA.owner == "")  # noqa: E711
                same_mailbox = or_(_EA.imap_user == owner, _EA.from_address == owner)
                q = q.filter(or_(_EA.owner == owner, and_(unowned, same_mailbox)))
            for row in q.order_by(_EA.is_default.desc(), _EA.created_at.asc()).all():
                trial = _get_email_config(account_id=row.id, owner=owner)
                if smtp_ready(trial):
                    return trial
        finally:
            db.close()
    except Exception as exc:
        logger.debug("SMTP-capable account fallback failed: %s", exc)
    raise ValueError("No SMTP-capable email account configured")


def build_outbound_email_message(
    cfg: dict,
    *,
    to: str,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str = "",
    body: str = "",
    body_html: str | None = None,
    attachments: list | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    odysseus_kind: str | None = None,
    odysseus_ref: str | None = None,
    include_message_id: bool = False,
):
    """Build an outbound MIME message and SMTP envelope recipients."""
    has_attachments = bool(attachments)
    if has_attachments:
        outer = MIMEMultipart("mixed")
        body_container = MIMEMultipart("alternative")
    else:
        outer = MIMEMultipart("alternative")
        body_container = outer

    outer["From"] = email.utils.formataddr((cfg.get("display_name") or "", cfg["from_address"]))
    outer["To"] = to
    if cc:
        outer["Cc"] = cc
    outer["Subject"] = subject or ""
    outer["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    if include_message_id:
        outer["Message-ID"] = email.utils.make_msgid(domain="odysseus.local")
    if odysseus_kind or odysseus_ref:
        apply_odysseus_headers(outer, odysseus_kind, odysseus_ref)
    if in_reply_to:
        outer["In-Reply-To"] = in_reply_to
    if references:
        outer["References"] = references

    body_container.attach(MIMEText(body or "", "plain", "utf-8"))
    html_part = (sanitize_email_html(body_html) if body_html else None) or markdown_to_email_html(body or "")
    body_container.attach(MIMEText(html_part, "html", "utf-8"))

    if has_attachments:
        outer.attach(body_container)

    return outer, envelope_recipients(to, cc, bcc)


def build_draft_message(
    cfg: dict,
    *,
    to: str,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str = "",
    body: str = "",
    body_html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
):
    """Build the MIME message stored into the provider Drafts folder."""
    draft_html = sanitize_email_html(body_html) if body_html else None
    if draft_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body or "", "plain", "utf-8"))
        msg.attach(MIMEText(draft_html, "html", "utf-8"))
    else:
        msg = MIMEText(body or "", "plain", "utf-8")

    msg["From"] = email.utils.formataddr((cfg.get("display_name") or "", cfg["from_address"]))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject or ""
    msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    return msg
