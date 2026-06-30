"""Email account configuration and CRUD helpers."""

import uuid


EMAIL_AUTOMATION_FLAGS = [
    "email_auto_summarize",
    "email_auto_reply",
    "email_auto_tag",
    "email_auto_spam",
    "email_auto_calendar",
]


def masked_email_config(owner: str, *, get_config, load_settings) -> dict:
    cfg = get_config(owner=owner)
    cfg["smtp_password"] = "***" if cfg["smtp_password"] else ""
    cfg["imap_password"] = "***" if cfg["imap_password"] else ""
    settings = load_settings()
    for key in EMAIL_AUTOMATION_FLAGS:
        cfg[key] = bool(settings.get(key, False))
    return cfg


def update_default_email_config(
    data: dict,
    *,
    owner: str,
    load_settings,
    save_settings,
    smtp_security_mode,
) -> dict:
    settings = load_settings()
    for key in EMAIL_AUTOMATION_FLAGS:
        if key in data:
            settings[key] = data[key]
    save_settings(settings)

    from core.database import EmailAccount, SessionLocal
    from src.secret_storage import encrypt as _enc

    db = SessionLocal()
    try:
        q = db.query(EmailAccount).filter(EmailAccount.is_default == True)  # noqa: E712
        if owner:
            q = q.filter(EmailAccount.owner == owner)
        row = q.first()
        if row is None:
            row = EmailAccount(id=uuid.uuid4().hex, owner=owner, name="Default", is_default=True, enabled=True)
            db.add(row)
        field_map = {
            "smtp_host": "smtp_host",
            "smtp_port": "smtp_port",
            "smtp_user": "smtp_user",
            "smtp_security": "smtp_security",
            "imap_host": "imap_host",
            "imap_port": "imap_port",
            "imap_user": "imap_user",
            "imap_starttls": "imap_starttls",
            "email_from": "from_address",
        }
        for in_key, col_name in field_map.items():
            if in_key not in data:
                continue
            val = data[in_key]
            if col_name.endswith("_port") and val in (None, ""):
                continue
            if col_name.endswith("_port"):
                val = int(val)
            setattr(row, col_name, val)
        if data.get("imap_password"):
            row.imap_password = _enc(data["imap_password"])
        if data.get("smtp_password"):
            row.smtp_password = _enc(data["smtp_password"])
        clear_q = db.query(EmailAccount).filter(EmailAccount.id != row.id)
        if owner:
            clear_q = clear_q.filter(EmailAccount.owner == owner)
        clear_q.update({EmailAccount.is_default: False})
        db.commit()
    finally:
        db.close()
    return {"success": True}


def list_email_account_rows(owner: str, *, smtp_security_mode) -> list[dict]:
    from core.database import EmailAccount, SessionLocal
    from sqlalchemy import and_, or_

    db = SessionLocal()
    try:
        out = []
        q = db.query(EmailAccount)
        if owner:
            unowned = or_(EmailAccount.owner == None, EmailAccount.owner == "")  # noqa: E711
            same_mailbox = or_(EmailAccount.imap_user == owner, EmailAccount.from_address == owner)
            q = q.filter(or_(EmailAccount.owner == owner, and_(unowned, same_mailbox)))
        for row in q.order_by(EmailAccount.is_default.desc(), EmailAccount.created_at.asc()).all():
            out.append({
                "id": row.id,
                "name": row.name,
                "is_default": bool(row.is_default),
                "enabled": bool(row.enabled),
                "imap_host": row.imap_host or "",
                "imap_port": int(row.imap_port or 993),
                "imap_user": row.imap_user or "",
                "imap_starttls": bool(row.imap_starttls),
                "smtp_host": row.smtp_host or "",
                "smtp_port": int(row.smtp_port or 465),
                "smtp_security": smtp_security_mode({
                    "smtp_security": getattr(row, "smtp_security", ""),
                    "smtp_port": row.smtp_port,
                }),
                "smtp_user": row.smtp_user or "",
                "from_address": row.from_address or "",
                "has_imap_password": bool(row.imap_password),
                "has_smtp_password": bool(row.smtp_password),
                "oauth_provider": row.oauth_provider or "",
                "display_name": row.display_name or "",
            })
        return out
    finally:
        db.close()


def create_email_account_row(data: dict, *, owner: str, smtp_security_mode) -> dict:
    from core.database import EmailAccount, SessionLocal
    from src.secret_storage import encrypt as _enc

    name = (data.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    db = SessionLocal()
    try:
        row = EmailAccount(
            id=uuid.uuid4().hex,
            name=name,
            is_default=bool(data.get("is_default", False)),
            enabled=bool(data.get("enabled", True)),
            imap_host=(data.get("imap_host") or "").strip(),
            imap_port=int(data.get("imap_port") or 993),
            imap_user=(data.get("imap_user") or "").strip(),
            imap_password=_enc(data.get("imap_password") or ""),
            imap_starttls=bool(data.get("imap_starttls", True)),
            smtp_host=(data.get("smtp_host") or "").strip(),
            smtp_port=int(data.get("smtp_port") or 465),
            smtp_security=smtp_security_mode({
                "smtp_security": data.get("smtp_security"),
                "smtp_port": data.get("smtp_port") or 465,
            }),
            smtp_user=(data.get("smtp_user") or "").strip(),
            smtp_password=_enc(data.get("smtp_password") or ""),
            from_address=(data.get("from_address") or "").strip(),
            display_name=(data.get("display_name") or "").strip(),
            owner=owner,
        )
        scope_q = db.query(EmailAccount)
        if owner:
            scope_q = scope_q.filter(EmailAccount.owner == owner)
        existing_count = scope_q.count()
        if row.is_default or existing_count == 0:
            scope_q.update({EmailAccount.is_default: False})
            row.is_default = True
        db.add(row)
        db.commit()
        return {"ok": True, "id": row.id}
    finally:
        db.close()


def update_email_account_row(account_id: str, data: dict, *, smtp_security_mode) -> dict:
    from core.database import EmailAccount, SessionLocal
    from src.secret_storage import encrypt as _enc

    db = SessionLocal()
    try:
        row = db.get(EmailAccount, account_id)
        if not row:
            return {"ok": False, "error": "Account not found"}
        for key in ("name", "imap_host", "imap_user", "smtp_host", "smtp_user", "from_address", "display_name"):
            if key in data:
                setattr(row, key, (data[key] or "").strip())
        for key in ("imap_port", "smtp_port"):
            if data.get(key) not in (None, ""):
                setattr(row, key, int(data[key]))
        if "smtp_security" in data:
            row.smtp_security = smtp_security_mode({
                "smtp_security": data.get("smtp_security"),
                "smtp_port": data.get("smtp_port") or row.smtp_port,
            })
        for key in ("imap_starttls", "enabled"):
            if key in data:
                setattr(row, key, bool(data[key]))
        if data.get("imap_password"):
            row.imap_password = _enc(data["imap_password"])
        if data.get("smtp_password"):
            row.smtp_password = _enc(data["smtp_password"])
        db.commit()
        return {"ok": True, "id": row.id}
    finally:
        db.close()


def delete_email_account_row(account_id: str, *, owner: str) -> dict:
    from core.database import EmailAccount, SessionLocal

    db = SessionLocal()
    try:
        row = db.get(EmailAccount, account_id)
        if not row:
            return {"ok": False, "error": "Account not found"}
        was_default = bool(row.is_default)
        db.delete(row)
        db.commit()
        if was_default:
            promote_q = db.query(EmailAccount).filter(EmailAccount.enabled == True)  # noqa: E712
            if owner:
                promote_q = promote_q.filter(EmailAccount.owner == owner)
            promote = promote_q.order_by(EmailAccount.created_at.asc()).first()
            if promote:
                promote.is_default = True
                db.commit()
        return {"ok": True}
    finally:
        db.close()


def set_default_email_account_row(account_id: str, *, owner: str) -> dict:
    from core.database import EmailAccount, SessionLocal

    db = SessionLocal()
    try:
        row = db.get(EmailAccount, account_id)
        if not row:
            return {"ok": False, "error": "Account not found"}
        clear_q = db.query(EmailAccount)
        if owner:
            clear_q = clear_q.filter(EmailAccount.owner == owner)
        clear_q.update({EmailAccount.is_default: False})
        row.is_default = True
        db.commit()
        return {"ok": True}
    finally:
        db.close()


def saved_account_test_body(account_id: str, incoming: dict, *, smtp_security_mode) -> dict | None:
    from core.database import EmailAccount, SessionLocal
    from src.secret_storage import decrypt as _decrypt

    db = SessionLocal()
    try:
        row = db.get(EmailAccount, account_id)
        if not row:
            return None
        body = {
            "imap_host": row.imap_host or "",
            "imap_port": row.imap_port or 993,
            "imap_user": row.imap_user or "",
            "imap_password": _decrypt(row.imap_password or ""),
            "imap_starttls": bool(row.imap_starttls),
            "smtp_host": row.smtp_host or "",
            "smtp_port": row.smtp_port or 465,
            "smtp_security": smtp_security_mode({
                "smtp_security": getattr(row, "smtp_security", ""),
                "smtp_port": row.smtp_port,
            }),
            "smtp_user": row.smtp_user or "",
            "smtp_password": _decrypt(row.smtp_password or ""),
        }
        for key, value in incoming.items():
            if key == "account_id":
                continue
            if value not in (None, ""):
                body[key] = value
        return body
    finally:
        db.close()
