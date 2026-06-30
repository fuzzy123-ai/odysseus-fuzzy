"""SMTP readiness, send-config resolution and connection helpers."""

from __future__ import annotations

from typing import Callable


def smtp_ready(cfg: dict) -> bool:
    return bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))


def resolve_send_config(
    account=None,
    *,
    load_config: Callable,
    list_accounts_raw: Callable,
    smtp_ready_func: Callable[[dict], bool],
):
    cfg = load_config(account)
    if smtp_ready_func(cfg):
        return account, cfg
    if account:
        raise ValueError(f"Email account {cfg.get('account_name') or account} has no SMTP configured")
    for row in list_accounts_raw():
        selector = row.get("id") or row.get("name") or row.get("imap_user")
        trial = load_config(selector)
        if smtp_ready_func(trial):
            return selector, trial
    raise ValueError("No SMTP-capable email account configured")


def connect_smtp(
    account=None,
    *,
    cfg=None,
    load_config: Callable,
    smtp_ready_func: Callable[[dict], bool],
    smtp_module,
    timeout: float,
):
    """Connect to SMTP server, returns logged-in connection."""
    cfg = cfg or load_config(account)
    if not smtp_ready_func(cfg):
        raise ValueError(f"Email account {cfg.get('account_name') or account or 'default'} has no SMTP configured")
    port = int(cfg.get("smtp_port") or 465)
    security = str(cfg.get("smtp_security") or "").strip().lower()
    if security not in {"ssl", "starttls", "none"}:
        security = "starttls" if port == 587 else "ssl"
    if security == "starttls":
        conn = smtp_module.SMTP(
            cfg["smtp_host"],
            port,
            timeout=timeout,
        )
        try:
            conn.starttls()
        except Exception:
            # Don't leak the open plain socket on a rejected STARTTLS. SMTP has
            # no shutdown(); close() is the low-level socket close (no QUIT). (#3174)
            try:
                conn.close()
            except Exception:
                pass
            raise
    elif security == "ssl":
        conn = smtp_module.SMTP_SSL(
            cfg["smtp_host"],
            port,
            timeout=timeout,
        )
    else:
        conn = smtp_module.SMTP(
            cfg["smtp_host"],
            port,
            timeout=timeout,
        )
    if cfg["smtp_user"] and cfg["smtp_password"]:
        try:
            conn.login(cfg["smtp_user"], cfg["smtp_password"])
        except Exception:
            # A failed login otherwise orphans the connected socket; close it
            # before propagating (SMTP has no shutdown(); close() = socket close). (#3174)
            try:
                conn.close()
            except Exception:
                pass
            raise
    return conn
