import hashlib
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from .readiness import readiness_gate_from_signals


LEDGER_DB_PATH = ".obsidian/odysseus/memory/ledger.sqlite3"
SCAN_IGNORED_DIRS = {"__pycache__", ".trash", ".snapshots", ".git"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
DOCUMENT_EXTENSIONS = {
    ".txt",
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".csv",
    ".tsv",
    ".json",
    ".html",
    ".htm",
}
CAPTURE_PREFIXES = (
    "AI Memory/Inbox/",
    "AI Memory/Review Queue/",
    "AI Memory/Captures/",
    "AI Memory/Sessions/",
)


def ledger_db_abspath(vault_dir: str) -> str:
    return os.path.join(vault_dir, LEDGER_DB_PATH.replace("/", os.sep))


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mtime_iso(abs_path: str) -> str:
    stat = os.stat(abs_path)
    return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(abs_path: str) -> str:
    digest = hashlib.sha256()
    with open(abs_path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _source_type(path: str) -> str:
    normalized = _normalize_path(path)
    ext = os.path.splitext(normalized.lower())[1]
    if ext in MARKDOWN_EXTENSIONS:
        if normalized.startswith(CAPTURE_PREFIXES):
            return "chat_capture"
        return "markdown"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return "attachment"


def _scan_source_records(vault_dir: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in SCAN_IGNORED_DIRS and directory != ".obsidian"
        ]
        for filename in files:
            abs_path = os.path.join(root, filename)
            rel_path = _normalize_path(os.path.relpath(abs_path, vault_dir))
            records.append(
                {
                    "path": rel_path,
                    "source_type": _source_type(rel_path),
                    "source_hash": _sha256_file(abs_path),
                    "source_mtime": _mtime_iso(abs_path),
                    "size_bytes": int(os.path.getsize(abs_path)),
                }
            )
    records.sort(key=lambda item: item["path"].lower())
    return records


def _connect(vault_dir: str) -> sqlite3.Connection:
    db_path = ledger_db_abspath(vault_dir)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_memory_ledger(vault_dir: str) -> str:
    with closing(_connect(vault_dir)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_ledger (
                path TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                source_mtime TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                chunk_count INTEGER,
                indexed_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_ledger_status ON source_ledger(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_ledger_type ON source_ledger(source_type)"
        )
        conn.commit()
    return ledger_db_abspath(vault_dir)


def _rows_by_path(conn: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM source_ledger").fetchall()
    return {str(row["path"]): row for row in rows}


def sync_memory_ledger(vault_dir: str) -> Dict[str, Any]:
    ensure_memory_ledger(vault_dir)
    scanned = _scan_source_records(vault_dir)
    now = _utcnow()
    created: List[str] = []
    changed: List[str] = []
    unchanged: List[str] = []
    deleted: List[Dict[str, Any]] = []

    with closing(_connect(vault_dir)) as conn:
        existing = _rows_by_path(conn)
        scanned_by_path = {item["path"]: item for item in scanned}

        for path, record in scanned_by_path.items():
            current = existing.get(path)
            if current is None:
                conn.execute(
                    """
                    INSERT INTO source_ledger (
                        path, source_type, source_hash, source_mtime, size_bytes,
                        status, chunk_count, indexed_at, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        record["source_type"],
                        record["source_hash"],
                        record["source_mtime"],
                        record["size_bytes"],
                        "pending",
                        None,
                        None,
                        None,
                        now,
                        now,
                    ),
                )
                created.append(path)
                continue

            current_shape = (
                str(current["source_type"]),
                str(current["source_hash"]),
                str(current["source_mtime"]),
                int(current["size_bytes"]),
            )
            scanned_shape = (
                record["source_type"],
                record["source_hash"],
                record["source_mtime"],
                record["size_bytes"],
            )
            if current_shape == scanned_shape:
                unchanged.append(path)
                continue

            next_status = "stale" if str(current["status"]) == "indexed" else "pending"
            conn.execute(
                """
                UPDATE source_ledger
                SET source_type = ?, source_hash = ?, source_mtime = ?, size_bytes = ?,
                    status = ?, chunk_count = ?, last_error = ?, updated_at = ?
                WHERE path = ?
                """,
                (
                    record["source_type"],
                    record["source_hash"],
                    record["source_mtime"],
                    record["size_bytes"],
                    next_status,
                    None,
                    None,
                    now,
                    path,
                ),
            )
            changed.append(path)

        for path, row in existing.items():
            if path in scanned_by_path:
                continue
            deleted.append(
                {
                    "path": path,
                    "source_type": str(row["source_type"]),
                    "status": str(row["status"]),
                    "chunk_count": row["chunk_count"],
                }
            )
            conn.execute("DELETE FROM source_ledger WHERE path = ?", (path,))
        conn.commit()

    return {
        "success": True,
        "db_path": ledger_db_abspath(vault_dir),
        "created": created,
        "changed": changed,
        "unchanged": unchanged,
        "deleted": deleted,
        "summary": {
            "scanned_sources": len(scanned),
            "created": len(created),
            "changed": len(changed),
            "unchanged": len(unchanged),
            "deleted": len(deleted),
        },
    }


def _ensure_existing_row(conn: sqlite3.Connection, path: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM source_ledger WHERE path = ?", (_normalize_path(path),)).fetchone()
    if row is None:
        raise KeyError(f"Source not found in ledger: {path}")
    return row


def mark_source_indexed(vault_dir: str, path: str, *, chunk_count: int = 0) -> Dict[str, Any]:
    ensure_memory_ledger(vault_dir)
    normalized = _normalize_path(path)
    now = _utcnow()
    with closing(_connect(vault_dir)) as conn:
        _ensure_existing_row(conn, normalized)
        conn.execute(
            """
            UPDATE source_ledger
            SET status = ?, chunk_count = ?, indexed_at = ?, last_error = ?, updated_at = ?
            WHERE path = ?
            """,
            ("indexed", int(chunk_count), now, None, now, normalized),
        )
        conn.commit()
    return {"success": True, "path": normalized, "status": "indexed", "chunk_count": int(chunk_count)}


def mark_source_failed(vault_dir: str, path: str, error: str) -> Dict[str, Any]:
    ensure_memory_ledger(vault_dir)
    normalized = _normalize_path(path)
    now = _utcnow()
    message = str(error or "").strip() or "Indexing failed."
    with closing(_connect(vault_dir)) as conn:
        _ensure_existing_row(conn, normalized)
        conn.execute(
            """
            UPDATE source_ledger
            SET status = ?, chunk_count = ?, last_error = ?, updated_at = ?
            WHERE path = ?
            """,
            ("failed", None, message, now, normalized),
        )
        conn.commit()
    return {"success": True, "path": normalized, "status": "failed", "last_error": message}


def _read_rows(vault_dir: str) -> List[Dict[str, Any]]:
    ensure_memory_ledger(vault_dir)
    with closing(_connect(vault_dir)) as conn:
        rows = conn.execute(
            """
            SELECT path, source_type, source_hash, source_mtime, size_bytes,
                   status, chunk_count, indexed_at, last_error, created_at, updated_at
            FROM source_ledger
            ORDER BY lower(path)
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _readiness(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    counts = Counter(str(row.get("status") or "") for row in rows)
    gaps: List[str] = []
    if not rows:
        gaps.append("ledger_empty")
        state = "pending"
    elif counts.get("failed"):
        gaps.append("ledger_failed_sources")
        state = "failed"
    elif counts.get("stale"):
        gaps.append("ledger_stale_sources")
        state = "stale"
    elif counts.get("pending"):
        gaps.append("ledger_pending_sources")
        state = "pending"
    else:
        state = "ready"
    return {
        "ready": state == "ready",
        "state": state,
        "gaps": gaps,
        "writes_supported": True,
    }


def memory_ledger_status(vault_dir: str) -> Dict[str, Any]:
    rows = _read_rows(vault_dir)
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    type_counts = Counter(str(row.get("source_type") or "") for row in rows)
    readiness = _readiness(rows)
    last_indexed = sorted(
        [
            str(row.get("indexed_at"))
            for row in rows
            if str(row.get("indexed_at") or "").strip()
        ]
    )
    summary = {
        "total_sources": len(rows),
        "indexed_sources": status_counts.get("indexed", 0),
        "pending_sources": status_counts.get("pending", 0),
        "stale_sources": status_counts.get("stale", 0),
        "failed_sources": status_counts.get("failed", 0),
        "chunked_sources": sum(1 for row in rows if row.get("chunk_count") not in (None, 0)),
        "total_chunks": sum(int(row.get("chunk_count") or 0) for row in rows),
        "source_types": dict(sorted(type_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "last_indexed_at": last_indexed[-1] if last_indexed else "",
        "writes_supported": True,
        "readiness_state": readiness["state"],
        "readiness_gaps": len(readiness["gaps"]),
        "readiness_gap_names": list(readiness["gaps"]),
        "warnings": [],
    }
    readiness_signal = {
        "family": "ledger",
        "source": "readiness",
        "state": readiness["state"],
        "ready": readiness["ready"],
        "gaps": list(readiness["gaps"]),
        "gap_count": len(readiness["gaps"]),
    }
    readiness_gate = readiness_gate_from_signals([readiness_signal])
    summary["readiness_gate"] = readiness_gate
    return {
        "enabled": True,
        "storage": {
            "mode": "sqlite",
            "db_path": ledger_db_abspath(vault_dir),
        },
        "readiness": readiness,
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "summary": summary,
        "entries": rows,
        "warnings": [],
    }
