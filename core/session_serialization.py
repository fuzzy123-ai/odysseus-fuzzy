"""Session message and database-row serialization helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .database import ChatMessage as DbChatMessage
from .models import ChatMessage, Session


def message_timestamp_iso(value: Optional[datetime]) -> Optional[str]:
    """Return a stable ISO timestamp for chat message metadata."""
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def parse_msg_content(raw):
    """Parse JSON-array multimodal message content from DB rows."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.startswith("[{") and '"type"' in raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(p, dict) for p in parsed):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return raw


def estimate_message_tokens_dict(message: dict) -> int:
    meta = message.get("metadata")
    if isinstance(meta, dict):
        try:
            cached = int(meta.get("estimated_tokens") or 0)
        except (TypeError, ValueError):
            cached = 0
        if cached > 0:
            return cached
    from src.model_context import estimate_tokens
    estimated = estimate_tokens([message])
    if isinstance(meta, dict):
        meta["estimated_tokens"] = estimated
    return estimated


def parse_session_headers(headers):
    if isinstance(headers, str):
        try:
            return json.loads(headers)
        except json.JSONDecodeError:
            return {}
    return headers


def db_to_session_meta(db_session) -> Optional[Session]:
    """Build a Session with empty history for lazy message hydration."""
    session = Session(
        id=db_session.id,
        name=db_session.name,
        endpoint_url=db_session.endpoint_url,
        model=db_session.model,
        rag=db_session.rag,
        archived=db_session.archived,
        headers=parse_session_headers(db_session.headers),
        history=[],
        owner=getattr(db_session, "owner", None),
        is_important=getattr(db_session, "is_important", False) or False,
    )
    session.message_count = getattr(db_session, "message_count", 0) or 0
    return session


def db_message_to_chat_message(db_msg: DbChatMessage) -> ChatMessage:
    meta = json.loads(db_msg.meta_data) if db_msg.meta_data else {}
    if meta is None:
        meta = {}
    meta["_db_id"] = db_msg.id
    meta.setdefault("timestamp", message_timestamp_iso(db_msg.timestamp))
    return ChatMessage(
        role=db_msg.role,
        content=parse_msg_content(db_msg.content),
        metadata=meta,
    )


def db_to_session(db_session, db) -> Optional[Session]:
    """Convert a database session and its messages to a Session object."""
    history = []

    if db_session.messages:
        history = [db_message_to_chat_message(db_msg) for db_msg in db_session.messages]
    elif db is not None:
        db_messages = db.query(DbChatMessage).filter(
            DbChatMessage.session_id == db_session.id
        ).order_by(DbChatMessage.timestamp).all()
        history = [db_message_to_chat_message(db_msg) for db_msg in db_messages]

    if not history:
        return None

    session = Session(
        id=db_session.id,
        name=db_session.name,
        endpoint_url=db_session.endpoint_url,
        model=db_session.model,
        rag=db_session.rag,
        archived=db_session.archived,
        headers=parse_session_headers(db_session.headers),
        history=history,
        owner=getattr(db_session, "owner", None),
        is_important=getattr(db_session, "is_important", False) or False,
    )
    session.message_count = getattr(db_session, "message_count", len(history))
    return session


def db_message_to_context_dict(db_msg: DbChatMessage) -> dict:
    msg = db_message_to_chat_message(db_msg)
    out = msg.to_dict()
    if isinstance(out.get("metadata"), dict):
        out["metadata"].setdefault("estimated_tokens", estimate_message_tokens_dict(out))
    return out
