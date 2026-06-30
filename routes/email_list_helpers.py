"""Email list/search helper functions."""

from __future__ import annotations

import email as email_mod
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from routes.email_message_shapes import (
    fetch_flags_from_meta,
    fetch_size_from_meta,
    list_email_row_from_header,
)


DecodeHeader = Callable[[str], str]
OwnerClause = Callable[[str | None, str], tuple[str, list[str]]]
UidFromMeta = Callable[[bytes], str]


def normalize_email_tags(tags_raw: str | None) -> list[Any]:
    try:
        tags = json.loads(tags_raw or "[]")
    except Exception:
        tags = []
    if not isinstance(tags, list):
        return []
    return [
        "marketing" if str(tag).strip().lower().replace("_", "-") == "promo" else tag
        for tag in tags
    ]


def load_email_tags_by_uid(
    db_path: str | Path,
    *,
    folder: str,
    account_id: str | None,
    owner: str,
    uid_list: list[bytes],
    email_tag_owner_clause: OwnerClause,
    logger: Any = None,
) -> dict[str, dict[str, Any]]:
    tag_by_uid: dict[str, dict[str, Any]] = {}
    uid_strs = [uid.decode() for uid in uid_list]
    if not uid_strs:
        return tag_by_uid
    try:
        conn = sqlite3.connect(db_path)
        try:
            placeholders = ",".join("?" * len(uid_strs))
            owner_clause, owner_params = email_tag_owner_clause(account_id, owner)
            rows = conn.execute(
                f"SELECT uid, tags, spam_verdict FROM email_tags "
                f"WHERE folder=? AND {owner_clause} AND uid IN ({placeholders})",
                [folder, *owner_params, *uid_strs],
            ).fetchall()
            for uid, tags_raw, spam_raw in rows:
                tag_by_uid[uid] = {
                    "tags": normalize_email_tags(tags_raw),
                    "spam": bool(spam_raw),
                }
        finally:
            conn.close()
    except Exception as exc:
        if logger is not None:
            logger.warning(f"Tag preload failed: {exc}")
    return tag_by_uid


def message_ids_from_grouped_headers(grouped: list[tuple[bytes, bytes]]) -> list[str]:
    header_ids = []
    for _, raw_header in grouped:
        if not raw_header:
            continue
        mid = (email_mod.message_from_bytes(raw_header).get("Message-ID", "") or "").strip()
        if mid:
            header_ids.append(mid)
    return header_ids


def load_email_tags_by_message_id(
    db_path: str | Path,
    *,
    folder: str,
    account_id: str | None,
    owner: str,
    grouped: list[tuple[bytes, bytes]],
    email_tag_owner_clause: OwnerClause,
    logger: Any = None,
) -> dict[str, dict[str, Any]]:
    tag_by_message_id: dict[str, dict[str, Any]] = {}
    header_ids = message_ids_from_grouped_headers(grouped)
    if not header_ids:
        return tag_by_message_id
    try:
        conn = sqlite3.connect(db_path)
        try:
            owner_clause, owner_params = email_tag_owner_clause(account_id, owner)
            placeholders = ",".join("?" * len(header_ids))
            rows = conn.execute(
                f"SELECT message_id, tags, spam_verdict FROM email_tags "
                f"WHERE folder=? AND {owner_clause} "
                f"AND message_id IN ({placeholders})",
                [folder, *owner_params, *header_ids],
            ).fetchall()
            for mid, tags_raw, spam_raw in rows:
                tag_by_message_id[(mid or "").strip()] = {
                    "tags": normalize_email_tags(tags_raw),
                    "spam": bool(spam_raw),
                }
        finally:
            conn.close()
    except Exception as exc:
        if logger is not None:
            logger.warning(f"Message-ID tag preload failed: {exc}")
    return tag_by_message_id


def list_email_rows_from_grouped_headers(
    grouped: list[tuple[bytes, bytes]],
    *,
    tag_by_uid: dict[str, dict[str, Any]],
    tag_by_message_id: dict[str, dict[str, Any]],
    uid_from_fetch_meta: UidFromMeta,
    decode_header: DecodeHeader,
    logger: Any = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for meta_b, raw_header in grouped:
        try:
            uid_num = uid_from_fetch_meta(meta_b)
            if not uid_num or not raw_header:
                continue
            flags = fetch_flags_from_meta(meta_b)
            size = fetch_size_from_meta(meta_b)
            msg = email_mod.message_from_bytes(raw_header)
            message_id = (msg.get("Message-ID", "") or "").strip()
            tag_entry = tag_by_message_id.get(message_id) or tag_by_uid.get(uid_num, {})
            rows.append(
                list_email_row_from_header(
                    uid_num,
                    msg,
                    flags=flags,
                    size=size,
                    tag_entry=tag_entry,
                    decode_header=decode_header,
                )
            )
        except Exception as exc:
            if logger is not None:
                logger.warning(f"Error parsing batched email entry: {exc}")
    rows.sort(key=lambda row: row.get("date_epoch") or 0.0, reverse=True)
    return rows


def search_email_row_from_fetch_data(
    msg_data: list[Any],
    *,
    effective_folder: str,
    group_uid_fetch_records: Callable[[Any], list[tuple[bytes, bytes]]],
    uid_from_fetch_meta: UidFromMeta,
    decode_header: DecodeHeader,
) -> dict[str, Any] | None:
    raw_header = None
    flags = ""
    stable_uid = ""
    for meta_b, payload in group_uid_fetch_records(msg_data):
        if payload and b"RFC822.HEADER" in meta_b:
            raw_header = payload
        flags = fetch_flags_from_meta(meta_b) or flags
        stable_uid = uid_from_fetch_meta(meta_b) or stable_uid
    if not raw_header or not stable_uid:
        return None
    msg = email_mod.message_from_bytes(raw_header)
    return list_email_row_from_header(
        stable_uid,
        msg,
        flags=flags,
        folder=effective_folder,
        decode_header=decode_header,
    )
