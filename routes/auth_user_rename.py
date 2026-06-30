"""Owner-reference migration helpers for auth user renames."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import HTTPException, Request

from core.atomic_io import atomic_write_json, atomic_write_text
from core.auth import AuthManager
from src.constants import DEEP_RESEARCH_DIR, MEMORY_FILE, SKILLS_DIR


def _rollback_auth_rename(
    auth_manager: AuthManager,
    *,
    old_username: str,
    new_username: str,
    acting_user: str,
    logger,
) -> bool:
    # On self-rename the admin session has already moved to the new username,
    # so the rollback must authenticate as the new user.
    rollback_user = new_username if acting_user == old_username else acting_user
    try:
        return bool(auth_manager.rename_user(new_username, old_username, rollback_user))
    except Exception as rollback_err:
        logger.error(
            "Failed to roll back auth rename %s -> %s after owner migration failure: %s",
            new_username,
            old_username,
            rollback_err,
        )
        return False


def _rename_sql_owner_references(old_username: str, new_username: str) -> None:
    from sqlalchemy import func
    from core.database import Base, SessionLocal

    db = SessionLocal()
    try:
        for mapper in Base.registry.mappers:
            model = mapper.class_
            if not hasattr(model, "owner"):
                continue
            (
                db.query(model)
                .filter(func.lower(model.owner) == old_username)
                .update({"owner": new_username}, synchronize_session=False)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _rename_user_preferences(old_username: str, new_username: str, logger) -> None:
    try:
        from routes.prefs_routes import _load as _load_prefs, _save as _save_prefs

        prefs = _load_prefs()
        users = prefs.get("_users") if isinstance(prefs, dict) else None
        if isinstance(users, dict):
            prefs_key = next(
                (k for k in users if str(k).strip().lower() == old_username),
                None,
            )
            new_taken = any(str(k).strip().lower() == new_username for k in users)
            if prefs_key is not None and not new_taken:
                users[new_username] = users.pop(prefs_key)
                _save_prefs(prefs)
    except Exception as exc:
        logger.warning("Failed to rename user prefs %s -> %s: %s", old_username, new_username, exc)


def _rename_active_research_tasks(request: Request, old_username: str, new_username: str, logger) -> None:
    try:
        handler = getattr(request.app.state, "research_handler", None)
        rename_owner = getattr(handler, "rename_owner", None)
        if callable(rename_owner):
            rename_owner(old_username, new_username)
    except Exception as exc:
        logger.warning("Failed to rename active research tasks %s -> %s: %s", old_username, new_username, exc)


def _rename_completed_research_reports(
    old_username: str,
    new_username: str,
    logger,
    *,
    deep_research_dir: str = DEEP_RESEARCH_DIR,
) -> None:
    try:
        research_dir = Path(deep_research_dir)
        if research_dir.is_dir():
            for path in research_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if str(payload.get("owner", "")).strip().lower() == old_username:
                        payload["owner"] = new_username
                        atomic_write_json(str(path), payload)
                except Exception as err:
                    logger.warning("Failed to update research owner in %s: %s", path.name, err)
    except Exception as exc:
        logger.warning("Failed to rename research owner references %s -> %s: %s", old_username, new_username, exc)


def _rename_memory_entries(
    old_username: str,
    new_username: str,
    logger,
    *,
    memory_file: str = MEMORY_FILE,
) -> None:
    try:
        if os.path.isfile(memory_file):
            with open(memory_file, encoding="utf-8") as handle:
                entries = json.loads(handle.read())
            if isinstance(entries, list):
                changed = False
                for entry in entries:
                    if isinstance(entry, dict) and str(entry.get("owner", "")).strip().lower() == old_username:
                        entry["owner"] = new_username
                        changed = True
                if changed:
                    atomic_write_json(memory_file, entries)
    except Exception as exc:
        logger.warning("Failed to rename memory.json owner references %s -> %s: %s", old_username, new_username, exc)


def _rename_upload_owner(request: Request, old_username: str, new_username: str, logger) -> None:
    try:
        upload_handler = getattr(request.app.state, "upload_handler", None)
        rename_owner = getattr(upload_handler, "rename_owner", None)
        if callable(rename_owner):
            rename_owner(old_username, new_username)
    except Exception as exc:
        logger.warning("Failed to rename upload owner references %s -> %s: %s", old_username, new_username, exc)


def _rename_personal_rag_owner(request: Request, old_username: str, new_username: str, logger) -> None:
    try:
        from routes.personal_routes import rename_personal_upload_owner

        personal_docs_manager = getattr(request.app.state, "personal_docs_manager", None)
        if personal_docs_manager is not None:
            rag_manager = getattr(personal_docs_manager, "rag_manager", None)
            rename_personal_upload_owner(
                old_username,
                new_username,
                personal_docs_manager=personal_docs_manager,
                rag_manager=rag_manager,
            )
    except Exception as exc:
        logger.warning("Failed to rename personal RAG upload owner references %s -> %s: %s", old_username, new_username, exc)


def _rename_skill_owner_references(
    old_username: str,
    new_username: str,
    logger,
    *,
    skills_dir: str = SKILLS_DIR,
) -> None:
    try:
        skills_root = Path(skills_dir)
        if not skills_root.is_dir():
            return

        owner_re = re.compile(
            r"(?m)^(owner:\s*)" + re.escape(old_username) + r"\s*$",
            re.IGNORECASE,
        )
        for path in skills_root.rglob("SKILL.md"):
            try:
                text = path.read_text(encoding="utf-8")
                new_text = owner_re.sub(r"\g<1>" + new_username, text)
                if new_text != text:
                    atomic_write_text(str(path), new_text)
            except Exception as err:
                logger.warning("Failed to update skill owner in %s: %s", path, err)

        usage_path = skills_root / "_usage.json"
        if usage_path.is_file():
            try:
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
                if isinstance(usage, dict):
                    new_usage = {}
                    changed = False
                    for key, value in usage.items():
                        owner_part, sep, skill_part = key.partition("::")
                        if sep and owner_part.lower() == old_username:
                            new_usage[new_username + "::" + skill_part] = value
                            changed = True
                        else:
                            new_usage[key] = value
                    if changed:
                        atomic_write_json(str(usage_path), new_usage)
            except Exception as err:
                logger.warning("Failed to update skills usage keys %s -> %s: %s", old_username, new_username, err)
    except Exception as exc:
        logger.warning("Failed to rename skills owner references %s -> %s: %s", old_username, new_username, exc)


def _rename_cached_session_owners(request: Request, old_username: str, new_username: str) -> None:
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        return
    for session in list(getattr(manager, "sessions", {}).values()):
        if str(getattr(session, "owner", None) or "").strip().lower() == old_username:
            session.owner = new_username


def _invalidate_api_token_cache(request: Request) -> None:
    invalidator = getattr(request.app.state, "invalidate_token_cache", None)
    if callable(invalidator):
        invalidator()


def migrate_renamed_user_references(
    *,
    request: Request,
    auth_manager: AuthManager,
    old_username: str,
    new_username: str,
    acting_user: str,
    logger,
    deep_research_dir: str = DEEP_RESEARCH_DIR,
    memory_file: str = MEMORY_FILE,
    skills_dir: str = SKILLS_DIR,
) -> None:
    """Migrate owner-scoped references after AuthManager accepted a rename."""
    try:
        _rename_sql_owner_references(old_username, new_username)
    except Exception as exc:
        logger.error("Failed to rename owner references %s -> %s: %s", old_username, new_username, exc)
        if not _rollback_auth_rename(
            auth_manager,
            old_username=old_username,
            new_username=new_username,
            acting_user=acting_user,
            logger=logger,
        ):
            logger.error(
                "Auth rename %s -> %s could not be rolled back after owner migration failure",
                old_username,
                new_username,
            )
        raise HTTPException(500, "Failed to rename user data") from exc

    _rename_user_preferences(old_username, new_username, logger)
    _rename_active_research_tasks(request, old_username, new_username, logger)
    _rename_completed_research_reports(
        old_username,
        new_username,
        logger,
        deep_research_dir=deep_research_dir,
    )
    _rename_memory_entries(old_username, new_username, logger, memory_file=memory_file)
    _rename_upload_owner(request, old_username, new_username, logger)
    _rename_personal_rag_owner(request, old_username, new_username, logger)
    _rename_skill_owner_references(old_username, new_username, logger, skills_dir=skills_dir)
    _rename_cached_session_owners(request, old_username, new_username)
    _invalidate_api_token_cache(request)
