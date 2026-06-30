# core/session_manager.py
"""
Session management — all session business logic and DB operations.

This is the single place that handles:
- Loading/saving sessions to database
- Adding messages to sessions
- Session lifecycle (create, archive, delete)
"""

import json
import uuid
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from .database import Session as DbSession, ChatMessage as DbChatMessage, Document as DbDocument, SessionLocal, utcnow_naive
from .models import Session, ChatMessage
from .session_serialization import (
    db_message_to_context_dict,
    db_to_session,
    db_to_session_meta,
    estimate_message_tokens_dict,
    message_timestamp_iso,
    parse_msg_content,
)

# Re-export singleton accessors from models for convenience
from .models import set_session_manager_instance, get_session_manager_instance

logger = logging.getLogger(__name__)


_message_timestamp_iso = message_timestamp_iso
_parse_msg_content = parse_msg_content
_estimate_message_tokens_dict = estimate_message_tokens_dict


class SessionManager:
    """
    Manages chat sessions with database persistence.

    Usage:
        manager = SessionManager()
        session = manager.create_session(id, name, url, model)
        manager.add_message(session.id, ChatMessage("user", "hello"))
        session = manager.get_session(session_id)
    """

    def __init__(self, sessions_file: str = None):
        # sessions_file kept for backward compat, not used
        self.sessions: Dict[str, Session] = OrderedDict()
        self._last_touch_at: Dict[str, float] = {}
        self.load_sessions()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_sessions(self):
        """Load recent session METADATA from the database — messages are
        hydrated on demand by `get_session`. Previously this walked every
        message of every session into RAM at boot, which on a long-running
        personal-server box could be tens of thousands of rows held forever
        in `self.sessions`.
        """
        db = SessionLocal()
        try:
            db_sessions = db.query(DbSession).filter(
                DbSession.archived == False,
                DbSession.message_count > 0,
            ).order_by(DbSession.last_accessed.desc()).limit(100).all()

            loaded_count = 0
            for db_session in db_sessions:
                try:
                    session = self._db_to_session_meta(db_session)
                    if session is not None:
                        self._cache_session(db_session.id, session)
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Error loading session {db_session.id}: {e}")
                    continue

            logger.info(f"Loaded {loaded_count} session(s) (metadata only)")

        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            self.sessions = OrderedDict()
        finally:
            db.close()

    def _session_cache_max(self) -> int:
        try:
            from src.settings import get_setting
            value = int(get_setting("session_cache_max", 100) or 0)
        except (TypeError, ValueError):
            value = 100
        return max(1, value)

    def _context_message_limit(self) -> int:
        try:
            from src.settings import get_setting
            value = int(get_setting("session_context_message_limit", 200) or 0)
        except (TypeError, ValueError):
            value = 200
        return max(1, value)

    def _cache_session(self, session_id: str, session: Session) -> None:
        self.sessions[session_id] = session
        self._mark_session_cache_used(session_id)
        self._enforce_session_cache_limit()

    def _mark_session_cache_used(self, session_id: str) -> None:
        move_to_end = getattr(self.sessions, "move_to_end", None)
        if callable(move_to_end) and session_id in self.sessions:
            move_to_end(session_id)

    def _enforce_session_cache_limit(self) -> None:
        limit = self._session_cache_max()
        while len(self.sessions) > limit:
            if isinstance(self.sessions, OrderedDict):
                session_id, _session = self.sessions.popitem(last=False)
            else:
                session_id = next(iter(self.sessions))
                self.sessions.pop(session_id, None)
            last_touches = getattr(self, "_last_touch_at", None)
            if last_touches is not None:
                last_touches.pop(session_id, None)

    def _db_to_session_meta(self, db_session: DbSession) -> Optional[Session]:
        """Build a Session with empty history. `get_session` hydrates on demand."""
        return db_to_session_meta(db_session)

    def _db_to_session(self, db_session: DbSession, db=None) -> Optional[Session]:
        """Convert a database session to a Session object."""
        return db_to_session(db_session, db)

    def _db_message_to_context_dict(self, db_msg: DbChatMessage) -> dict:
        return db_message_to_context_dict(db_msg)

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, message: ChatMessage):
        """
        Add a message to a session and persist to database.

        Updates the authoritative history list and persists through this
        manager directly so tests and temporary managers do not depend on the
        process-wide session-manager singleton.

        Args:
            session_id: Session ID
            message: ChatMessage to add
        """
        session = self.get_session(session_id)
        session.history.append(message)
        session._history = session.history
        session.message_count = len(session.history)

        self._persist_message(session_id, message)

    def _persist_message(self, session_id: str, message: ChatMessage):
        """Persist a single message to the database."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                # A stream/tool callback can outlive a session delete. Do not
                # create a chat_messages row with no parent session; also drop
                # any stale cached session so later writes fail closed too.
                self.sessions.pop(session_id, None)
                logger.warning("Dropping message for deleted session %s", session_id)
                return

            msg_id = str(uuid.uuid4())
            msg_time = datetime.utcnow()
            if message.metadata is None:
                message.metadata = {}
            message.metadata.setdefault('timestamp', _message_timestamp_iso(msg_time))
            message.metadata.setdefault('estimated_tokens', _estimate_message_tokens_dict(message.to_dict()))
            # Multimodal content (image/audio attachments) is a list — serialize
            # to JSON so the Text column can store it.  On reload, _db_to_session
            # detects the JSON-array prefix and parses it back.
            _content = message.content
            if isinstance(_content, list):
                _content = json.dumps(_content)
            db_message = DbChatMessage(
                id=msg_id,
                session_id=session_id,
                role=message.role,
                content=_content,
                meta_data=json.dumps(message.metadata) if message.metadata else None,
                timestamp=msg_time,
            )
            db.add(db_message)

            if session_id in self.sessions:
                db_session.message_count = len(self.sessions[session_id].history)
            else:
                db_session.message_count = 0
            _now = datetime.now(timezone.utc)
            db_session.last_accessed = _now
            # Clean "last conversation" timestamp — only bumped here on a
            # real message persist, so it powers an accurate "Last active"
            # sort that ignores renames / model swaps / mere opens.
            db_session.last_message_at = _now

            db.commit()

            # Store DB ID on the in-memory message for edit/delete by ID
            message.metadata['_db_id'] = msg_id

            logger.debug(f"Persisted message to session {session_id}")

        except Exception as e:
            logger.error(f"Error persisting message: {e}")
            db.rollback()
        finally:
            db.close()

    def truncate_messages(self, session_id: str, keep_count: int) -> bool:
        """Truncate session history, keeping only the first `keep_count` messages."""
        session = self.get_session(session_id)

        if keep_count < 0:
            return False

        db = SessionLocal()
        try:
            db_messages = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session_id
            ).order_by(DbChatMessage.timestamp).all()

            deleted = 0
            for msg in db_messages[keep_count:]:
                db.delete(msg)
                deleted += 1

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                # keep_count can exceed the real message total (e.g. the AI tool
                # defaults to keep_count=10 on a short session); message_count must
                # track the rows that actually remain, not the requested cap.
                db_session.message_count = min(keep_count, len(db_messages))
                db_session.updated_at = datetime.now(timezone.utc)

            db.commit()

            # Update in-memory
            session.history = session.history[:keep_count]
            session._history = session.history

            logger.info(f"Truncated session {session_id} to {keep_count} messages")
            return True

        except Exception as e:
            logger.error(f"Error truncating session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def replace_messages(self, session_id: str, messages: list) -> bool:
        """Replace a session's persisted and in-memory history atomically."""
        session = self.get_session(session_id)
        db = SessionLocal()
        try:
            db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()
            now = datetime.now(timezone.utc)
            for i, message in enumerate(messages):
                msg_id = str(uuid.uuid4())
                if message.metadata is None:
                    message.metadata = {}
                message.metadata.setdefault("estimated_tokens", _estimate_message_tokens_dict(message.to_dict()))
                db_message = DbChatMessage(
                    id=msg_id,
                    session_id=session_id,
                    role=message.role,
                    # Multimodal content (image/audio attachments) is a list;
                    # serialize to JSON so the Text column round-trips via
                    # _parse_msg_content. Storing the raw list let SQLAlchemy
                    # bind its single-quoted repr, which _parse_msg_content
                    # cannot parse (it looks for double-quoted "type"), so the
                    # attachment was destroyed on reload. Mirrors _persist_message.
                    content=(json.dumps(message.content)
                             if isinstance(message.content, list)
                             else message.content),
                    meta_data=json.dumps(message.metadata) if message.metadata else None,
                    timestamp=now + timedelta(microseconds=i),
                )
                db.add(db_message)
                message.metadata["_db_id"] = msg_id

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.message_count = len(messages)
                db_session.updated_at = now
                db_session.last_accessed = now
                db_session.last_message_at = now

            db.commit()
            session.history = list(messages)
            session._history = session.history
            session.message_count = len(messages)
            logger.info("Replaced session %s history with %d messages", session_id, len(messages))
            return True
        except Exception as e:
            logger.error("Error replacing session history: %s", e)
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID, loading from DB if needed.

        Sessions seeded by `load_sessions` start with empty history. The
        first read here hydrates them with the message rows.
        """
        if session_id not in self.sessions:
            self._load_session_from_db(session_id)
        else:
            self._mark_session_cache_used(session_id)
            cached = self.sessions[session_id]
            # Lazy hydrate: metadata-only entries get their messages on first read.
            if not cached.history and getattr(cached, "message_count", 0) > 0:
                self._load_session_from_db(session_id)

        # Keep model/endpoint metadata fresh. Endpoint deletion can clear the
        # DB row while a session object is still cached in RAM.
        self.sync_session_metadata(session_id)

        # Update last_accessed
        self._touch_session(session_id)

        return self.sessions[session_id]

    def sync_session_metadata(self, session_id: str) -> bool:
        """Refresh non-message session fields from the DB into the cached object."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                return False
            headers = db_session.headers
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except json.JSONDecodeError:
                    headers = {}
            session.name = db_session.name
            session.endpoint_url = db_session.endpoint_url or ""
            session.model = db_session.model or ""
            session.headers = headers or {}
            session.rag = db_session.rag
            session.archived = db_session.archived
            session.owner = getattr(db_session, "owner", None)
            session.is_important = getattr(db_session, "is_important", False) or False
            session.message_count = getattr(db_session, "message_count", session.message_count) or 0
            return True
        except Exception as e:
            logger.error(f"Error syncing session metadata {session_id}: {e}")
            return False
        finally:
            db.close()

    def _load_session_from_db(self, session_id: str):
        """Hydrate a single session (with messages) from the database."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                raise KeyError(f"Session {session_id} not found")

            session = self._db_to_session(db_session, db)
            if session:
                self._cache_session(session_id, session)
            else:
                # No messages — fall back to metadata-only entry so callers
                # don't crash on KeyError for empty sessions.
                meta = self._db_to_session_meta(db_session)
                if meta is None:
                    raise KeyError(f"Session {session_id} could not be loaded")
                self._cache_session(session_id, meta)

        except KeyError:
            raise
        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
            raise
        finally:
            db.close()

    def _touch_session(self, session_id: str):
        """Update last_accessed timestamp."""
        try:
            from src.settings import get_setting
            interval = float(get_setting("session_touch_interval_seconds", 60) or 0)
        except (TypeError, ValueError):
            interval = 60.0
        now_monotonic = time.monotonic()
        last_touches = getattr(self, "_last_touch_at", None)
        if last_touches is None:
            last_touches = {}
            self._last_touch_at = last_touches
        last_touch = last_touches.get(session_id)
        if interval > 0 and last_touch is not None and (now_monotonic - last_touch) < interval:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.last_accessed = datetime.now(timezone.utc)
                db.commit()
                last_touches[session_id] = now_monotonic
        except Exception as e:
            logger.error(f"Error updating last_accessed: {e}")
            db.rollback()
        finally:
            db.close()

    def create_session(
        self,
        session_id: str,
        name: str,
        endpoint_url: str,
        model: str,
        rag: bool = False,
        owner: str = None
    ) -> Session:
        """Create a new session and save to database."""
        db = SessionLocal()
        try:
            db_session = DbSession(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers={},
                owner=owner,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(db_session)
            db.commit()

            session = Session(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers={},
                owner=owner,
            )

            self._cache_session(session_id, session)
            return session

        except Exception as e:
            db.rollback()
            logger.error(f"Error creating session: {e}")
            raise
        finally:
            db.close()

    def delete_session(self, session_id: str) -> bool:
        """Permanently delete a session and all its messages."""
        db = SessionLocal()
        try:
            # Detach documents so they survive as orphans in the library
            db.query(DbDocument).filter(DbDocument.session_id == session_id).update(
                {DbDocument.session_id: None}, synchronize_session=False
            )

            # Delete messages
            db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()

            # Delete session
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db.delete(db_session)

            # Drop the in-memory copy even when there is no DB row. A "ghost"
            # session lives only here (never persisted, or its row was removed
            # out-of-band); without this it can never be cleared and keeps
            # 404ing on every operation (issue #1044).
            removed_in_memory = self.sessions.pop(session_id, None) is not None

            if db_session or removed_in_memory:
                # Commit the document-detach / message-delete above (a no-op when
                # the ghost had no rows) together with the session delete.
                db.commit()
                logger.info(f"Deleted session {session_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session updates
    # ------------------------------------------------------------------

    def update_session_name(self, session_id: str, name: str):
        """Update session name."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.name = name
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].name = name
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating session name: {e}")
            raise
        finally:
            db.close()

    def archive_session(self, session_id: str):
        """Archive a session."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.archived = True
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].archived = True
        except Exception as e:
            db.rollback()
            logger.error(f"Error archiving session: {e}")
            raise
        finally:
            db.close()

    def mark_important(self, session_id: str, important: bool = True):
        """Mark session as important."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.is_important = important
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()

                if session_id in self.sessions:
                    self.sessions[session_id].is_important = important
            else:
                raise KeyError(f"Session {session_id} not found")
        except Exception as e:
            db.rollback()
            logger.error(f"Error marking session important: {e}")
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_sessions_for_user(self, username: Optional[str] = None) -> Dict[str, Session]:
        """Return sessions for a specific user (or all if username is None)."""
        if username is None:
            return self.sessions
        return {
            sid: s for sid, s in self.sessions.items()
            if s.owner == username
        }

    def save_sessions(self):
        """No-op for DB compatibility."""

    def get_recent_context_messages(
        self,
        session_id: str,
        token_budget: int,
        reserve_tokens: int = 512,
        max_messages: Optional[int] = None,
    ) -> list:
        """Return recent LLM-context messages bounded by count and token budget.

        This is for hot prompt-building paths. It avoids hydrating an entire
        long session when a metadata-only cache entry can be served by querying
        the newest message rows directly.
        """
        try:
            budget = int(token_budget or 0) - int(reserve_tokens or 0)
        except (TypeError, ValueError):
            budget = 0
        budget = max(256, budget)
        limit = max_messages or self._context_message_limit()

        messages = self._recent_context_messages_from_cache(session_id, limit)
        if messages is None:
            messages = self._recent_context_messages_from_db(session_id, limit)

        selected = []
        selected_tokens = 0
        for msg in reversed(messages):
            msg_tokens = _estimate_message_tokens_dict(msg)
            if selected and selected_tokens + msg_tokens > budget:
                break
            selected.insert(0, msg)
            selected_tokens += msg_tokens
        return selected

    def _recent_context_messages_from_cache(self, session_id: str, limit: int) -> Optional[list]:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        self._mark_session_cache_used(session_id)
        if not session.history:
            return [] if getattr(session, "message_count", 0) == 0 else None
        messages = []
        for msg in session.history[-limit:]:
            if (msg.metadata or {}).get("source") == "slash":
                continue
            messages.append(msg.to_dict())
        return messages

    def _recent_context_messages_from_db(self, session_id: str, limit: int) -> list:
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                raise KeyError(f"Session {session_id} not found")
            if session_id not in self.sessions:
                self._cache_session(session_id, self._db_to_session_meta(db_session))

            rows = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session_id
            ).order_by(DbChatMessage.timestamp.desc()).limit(limit).all()
            messages = []
            for db_msg in reversed(rows):
                msg = self._db_message_to_context_dict(db_msg)
                if (msg.get("metadata") or {}).get("source") == "slash":
                    continue
                messages.append(msg)
            return messages
        finally:
            db.close()

    def ensure_task_session(self, session_id: str, name: str, endpoint_url: str, model: str, owner: str = None, task: object = None) -> Session:
        """Create a task session if it doesn't exist, or return the existing one.

        Unlike create_session, this checks the cache first and does NOT
        overwrite an existing in-memory session. The task scheduler must
        use this instead of direct dict assignment.
        """
        if session_id in self.sessions:
            return self.sessions[session_id]

        session = self.create_session(session_id, name, endpoint_url, model, owner=owner)
        if task is not None:
            task.session_id = session_id
        return session

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_empty_sessions(self, auto_archive_days: int = 30, min_age_hours: int = 1) -> dict:
        """Clean up empty and old sessions.

        Args:
            auto_archive_days: Age in days before non-important sessions are archived.
            min_age_hours: Minimum age in hours before an empty session can be deleted.
                          Prevents deleting sessions that were just created.
        """
        db = SessionLocal()
        stats = {'deleted_empty': 0, 'archived_old': 0, 'total_checked': 0}

        try:
            all_sessions = db.query(DbSession).all()
            cutoff_date = utcnow_naive() - timedelta(days=auto_archive_days)
            min_age = utcnow_naive() - timedelta(hours=min_age_hours)

            for db_session in all_sessions:
                stats['total_checked'] += 1

                # Delete empty sessions only if older than min_age_hours
                if db_session.message_count == 0:
                    if db_session.created_at is not None:
                        created = db_session.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        if created > min_age:
                            continue  # Too young to delete
                    if db_session.id in self.sessions:
                        del self.sessions[db_session.id]
                    db.delete(db_session)
                    stats['deleted_empty'] += 1

                # Archive old sessions
                elif (not db_session.archived and
                      db_session.last_accessed and
                      db_session.last_accessed < cutoff_date and
                      db_session.message_count > 0 and
                      not getattr(db_session, 'is_important', False)):
                    db_session.archived = True
                    stats['archived_old'] += 1

            db.commit()
            logger.info(f"Cleanup: {stats['deleted_empty']} deleted, {stats['archived_old']} archived")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            db.rollback()
            raise
        finally:
            db.close()

        return stats
