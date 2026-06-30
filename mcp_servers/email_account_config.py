"""Account and owner-scope helpers for the built-in email MCP server."""

from __future__ import annotations

import json
import os
import sqlite3
from contextvars import ContextVar
from pathlib import Path

from src.constants import APP_DB, DATA_DIR as _DATA_DIR, EMAIL_CACHE_DB, SETTINGS_FILE as _SETTINGS_FILE


DATA_DIR = Path(_DATA_DIR)
_ACCOUNT_CACHE: dict = {}
_MCP_OWNER_ARG = "_odysseus_owner"
_CURRENT_OWNER: ContextVar[str | None] = ContextVar("email_mcp_owner", default=None)


def _db_path() -> Path:
    return Path(APP_DB)


def _current_owner() -> str:
    owner = _CURRENT_OWNER.get()
    return str(owner or "").strip()


def _account_visible_to_owner(row: dict, owner: str) -> bool:
    row_owner = str(row.get("owner") or "").strip()
    if row_owner == owner:
        return True
    if row_owner:
        return False
    owner_l = owner.lower()
    return owner_l in {
        str(row.get("imap_user") or "").strip().lower(),
        str(row.get("from_address") or "").strip().lower(),
    }


def _filter_accounts_for_owner(rows: list[dict]) -> list[dict]:
    owner = _current_owner()
    if owner:
        return [r for r in rows if _account_visible_to_owner(r, owner)]

    owners = {str(r.get("owner") or "").strip() for r in rows if str(r.get("owner") or "").strip()}
    if len(owners) > 1:
        return []
    return rows


def _mcp_owner_required(rows: list[dict] | None = None) -> bool:
    if _current_owner():
        return False
    rows = rows if rows is not None else _read_accounts_from_db()
    owners = {str(r.get("owner") or "").strip() for r in rows if str(r.get("owner") or "").strip()}
    return len(owners) > 1


def _load_email_writing_style() -> str:
    """Return the existing Settings > Email > Writing Style value."""
    try:
        settings_path = DATA_DIR / "settings.json"
        if not settings_path.exists():
            return ""
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        return str(settings.get("email_writing_style") or "").strip()
    except Exception:
        return ""


def _writing_style_guidance() -> str:
    style = _load_email_writing_style()
    if not style:
        return (
            "No saved writing style is configured in Settings > Email > Writing Style. "
            "Use a concise, natural tone and do not invent facts."
        )
    return (
        "Use this saved writing style from Settings > Email > Writing Style when "
        "drafting the body. It overrides generic tone guidance:\n"
        f"{style}"
    )


def _default_document_owner() -> str | None:
    """Best-effort owner for MCP-created documents."""
    owner = os.environ.get("ODYSSEUS_DOCUMENT_OWNER", "").strip()
    if owner:
        return owner
    try:
        auth_path = DATA_DIR / "auth.json"
        if not auth_path.exists():
            return None
        users = (json.loads(auth_path.read_text(encoding="utf-8")).get("users") or {})
        if not isinstance(users, dict) or not users:
            return None
        admins = [name for name, data in users.items() if isinstance(data, dict) and data.get("is_admin")]
        if len(admins) == 1:
            return admins[0]
        if len(users) == 1:
            return next(iter(users))
        return admins[0] if admins else next(iter(users))
    except Exception:
        return None


def _read_accounts_from_db() -> list:
    """Return all enabled email account rows. Empty list if missing. Never raises."""
    path = _db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        columns = {r[1] for r in conn.execute("PRAGMA table_info(email_accounts)").fetchall()}
        owner_select = "owner" if "owner" in columns else "NULL AS owner"
        smtp_security_select = "smtp_security" if "smtp_security" in columns else "'' AS smtp_security"
        rows = conn.execute(f"""
            SELECT id, {owner_select}, name, is_default, enabled,
                   imap_host, imap_port, imap_user, imap_password, imap_starttls,
                   smtp_host, smtp_port, {smtp_security_select}, smtp_user, smtp_password, from_address
            FROM email_accounts WHERE enabled = 1
            ORDER BY is_default DESC, created_at ASC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    except Exception:
        return []


def _list_accounts_raw() -> list:
    """Return owner-visible email account rows for the active MCP call."""
    return _filter_accounts_for_owner(_read_accounts_from_db())


def _resolve_account_from_rows(rows: list[dict], selector: str | None) -> dict | None:
    """Return the matching row or None for a selector."""
    if not rows:
        return None
    if not selector:
        for r in rows:
            if r.get("is_default"):
                return r
        return rows[0]
    sel = selector.strip().lower()
    for r in rows:
        if r["id"] == selector:
            return r
    for r in rows:
        fields = [r.get("name") or "", r.get("imap_user") or "", r.get("from_address") or ""]
        if any(sel in (f or "").lower() for f in fields):
            return r
    try:
        from difflib import get_close_matches

        candidates = []
        by_candidate = {}
        for r in rows:
            for field in (r.get("name"), r.get("imap_user"), r.get("from_address")):
                if field:
                    val = str(field).lower()
                    candidates.append(val)
                    by_candidate[val] = r
        close = get_close_matches(sel, candidates, n=1, cutoff=0.72)
        if close:
            return by_candidate.get(close[0])
    except Exception:
        pass
    return None


def _resolve_account(selector: str | None) -> dict | None:
    return _resolve_account_from_rows(_list_accounts_raw(), selector)


def _load_config(account: str | None = None) -> dict:
    """Return the full config dict for the requested account or default."""
    cache_key = (_current_owner(), (account or "").strip().lower() or "__default__")
    if cache_key in _ACCOUNT_CACHE:
        return _ACCOUNT_CACHE[cache_key]

    cfg = {
        "imap_host": os.environ.get("IMAP_HOST", "localhost"),
        "imap_port": int(os.environ.get("IMAP_PORT", "31143")),
        "imap_user": os.environ.get("IMAP_USER", ""),
        "imap_password": os.environ.get("IMAP_PASSWORD", ""),
        "imap_ssl": os.environ.get("IMAP_SSL", "false").lower() == "true",
        "imap_starttls": os.environ.get("IMAP_STARTTLS", "true").lower() == "true",
        "smtp_host": os.environ.get("SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
        "smtp_security": os.environ.get("SMTP_SECURITY", ""),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "smtp_starttls": os.environ.get("SMTP_STARTTLS", "false").lower() == "true",
        "smtp_ssl": os.environ.get("SMTP_SSL", "true").lower() == "true",
        "from_address": os.environ.get("EMAIL_FROM", ""),
        "archive_folder": os.environ.get("ARCHIVE_FOLDER", "Archive"),
        "trash_folder": os.environ.get("TRASH_FOLDER", "Trash"),
        "cache_db": os.environ.get("EMAIL_CACHE_DB", EMAIL_CACHE_DB),
        "account_id": None,
        "account_name": None,
    }

    raw_rows = _read_accounts_from_db()
    rows = _filter_accounts_for_owner(raw_rows)
    row = _resolve_account_from_rows(rows, account)
    if _current_owner() and raw_rows and not rows:
        raise ValueError("No email account is configured for the authenticated owner")
    if account and rows and not row:
        available = ", ".join(
            f"{r.get('name') or r.get('imap_user')} <{r.get('imap_user') or r.get('from_address') or '?'}>"
            for r in rows
        )
        raise ValueError(f"Email account not found for selector {account!r}. Available accounts: {available}")
    if row:
        cfg["account_id"] = row["id"]
        cfg["account_name"] = row["name"]
        cfg["imap_host"] = row["imap_host"] or cfg["imap_host"]
        cfg["imap_port"] = int(row["imap_port"] or cfg["imap_port"])
        cfg["imap_user"] = row["imap_user"] or cfg["imap_user"]
        try:
            from src.secret_storage import decrypt as _decrypt
        except Exception:
            _decrypt = lambda v: v  # noqa: E731
        cfg["imap_password"] = _decrypt(row["imap_password"]) if row["imap_password"] else cfg["imap_password"]
        cfg["imap_starttls"] = bool(row["imap_starttls"])
        cfg["imap_ssl"] = int(cfg["imap_port"]) == 993 and not cfg["imap_starttls"]
        cfg["smtp_host"] = row["smtp_host"] or cfg["smtp_host"]
        cfg["smtp_port"] = int(row["smtp_port"] or cfg["smtp_port"])
        cfg["smtp_security"] = row["smtp_security"] or cfg["smtp_security"] or ("starttls" if int(cfg["smtp_port"]) == 587 else "ssl")
        cfg["smtp_user"] = row["smtp_user"] or cfg["smtp_user"]
        cfg["smtp_password"] = _decrypt(row["smtp_password"]) if row["smtp_password"] else cfg["smtp_password"]
        cfg["from_address"] = row["from_address"] or row["imap_user"] or cfg["from_address"]
    else:
        try:
            settings_path = Path(_SETTINGS_FILE)
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                for key in (
                    "imap_host", "imap_port", "imap_user", "imap_password",
                    "smtp_host", "smtp_port", "smtp_user", "smtp_password",
                    "from_address", "archive_folder", "trash_folder",
                ):
                    if settings.get(key) not in (None, ""):
                        cfg[key] = int(settings[key]) if key.endswith("_port") else settings[key]
        except Exception:
            pass

    if not cfg["from_address"]:
        cfg["from_address"] = cfg["imap_user"]

    _ACCOUNT_CACHE[cache_key] = cfg
    return cfg
