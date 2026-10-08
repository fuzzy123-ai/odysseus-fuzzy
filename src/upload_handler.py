# src/upload_handler.py
import os
import re
import json
import uuid
import time
import hashlib
import mimetypes
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import HTTPException, UploadFile

from core.auth import SUBJECT_ID_PATTERN
from src.upload_limits import format_byte_limit, get_chat_upload_max_bytes


def secure_filename(filename: str) -> str:
    """Sanitize a filename (replaces werkzeug.utils.secure_filename)."""
    import unicodedata
    filename = unicodedata.normalize("NFKD", filename)
    filename = filename.encode("ascii", "ignore").decode("ascii")
    # Replace path separators with underscores
    for sep in (os.sep, os.altsep or "", "/", "\\"):
        if sep:
            filename = filename.replace(sep, "_")
    # Keep only safe characters
    filename = re.sub(r"[^\w\s\-.]", "", filename).strip()
    filename = re.sub(r"[\s]+", "_", filename)
    # Don't allow dotfiles
    filename = filename.lstrip(".")
    return filename or "unnamed"
import logging

logger = logging.getLogger(__name__)

# The extension is optional: save_upload builds the id as `{uuid.hex}{ext}`,
# and a file with no extension (Dockerfile, README, ...) yields a bare 32-hex
# id. Requiring `.ext` made those ids fail validation, so the stored file
# could never be resolved or downloaded again.
UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$")
_UPLOAD_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_INBOX_INDEX_MAX_BYTES = 4 * 1024 * 1024
_INBOX_INDEX_MAX_ROWS = 10_000
_INBOX_ROW_MAX_BYTES = 64 * 1024
_INBOX_JSON_MAX_DEPTH = 16
_INBOX_JSON_MAX_NODES = 1_024


class UploadInboxSnapshotError(ValueError):
    """Fixed failure raised when the private upload index is not trustworthy."""


@dataclass(frozen=True, slots=True, repr=False)
class UploadInboxItem:
    """Detached private descriptor used to build one opaque Inbox row."""

    item_handle: str
    binding_digest: str
    index_key: str
    upload_id: str
    canonical_row: bytes

    def __repr__(self) -> str:
        return "UploadInboxItem(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class UploadInboxSnapshot:
    """Detached SID-and-alias-bound snapshot of every selected upload row."""

    selected_count: int
    binding_digest: str
    items: tuple[UploadInboxItem, ...]
    clipped: bool

    def __repr__(self) -> str:
        return "UploadInboxSnapshot(<redacted>)"


def _inbox_frame(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _bounded_inbox_utf8(value: Any, maximum: int) -> bytes:
    if type(value) is not str:
        raise UploadInboxSnapshotError("inbox_failed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise UploadInboxSnapshotError("inbox_failed") from None
    if len(encoded) > maximum:
        raise UploadInboxSnapshotError("inbox_failed")
    return encoded


def _reject_json_constant(_value: str) -> None:
    raise UploadInboxSnapshotError("inbox_failed")


def _strict_json_float(value: str) -> float:
    try:
        parsed = float(value)
    except (ValueError, OverflowError):
        raise UploadInboxSnapshotError("inbox_failed") from None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise UploadInboxSnapshotError("inbox_failed")
    return parsed


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise UploadInboxSnapshotError("inbox_failed")
        result[key] = value
    return result


def _validate_inbox_json(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > _INBOX_JSON_MAX_NODES or depth > _INBOX_JSON_MAX_DEPTH:
        raise UploadInboxSnapshotError("inbox_failed")
    if value is None or type(value) in {bool, int, float, str}:
        if type(value) is str:
            _bounded_inbox_utf8(value, _INBOX_ROW_MAX_BYTES)
        if type(value) is float and (value != value or value in {float("inf"), float("-inf")}):
            raise UploadInboxSnapshotError("inbox_failed")
        return
    if type(value) is list:
        for item in value:
            _validate_inbox_json(item, depth=depth + 1, nodes=nodes)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise UploadInboxSnapshotError("inbox_failed")
            _validate_inbox_json(key, depth=depth + 1, nodes=nodes)
            _validate_inbox_json(item, depth=depth + 1, nodes=nodes)
        return
    raise UploadInboxSnapshotError("inbox_failed")


def _canonical_inbox_json(value: Any) -> bytes:
    _validate_inbox_json(value)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise UploadInboxSnapshotError("inbox_failed") from None
    if len(encoded) > _INBOX_ROW_MAX_BYTES:
        raise UploadInboxSnapshotError("inbox_failed")
    return encoded


def _validated_expected_inbox_snapshot(
    value: UploadInboxSnapshot,
    *,
    owner_alias: str,
    creator_subject_id: str,
    limit: int,
    owner_bytes: bytes,
) -> tuple[int, str, tuple[tuple[str, str, str, str, bytes], ...], bool]:
    """Detach and validate an expected snapshot before any private index read."""
    if type(value) is not UploadInboxSnapshot:
        raise UploadInboxSnapshotError("inbox_failed")
    selected_count = value.selected_count
    binding_digest = value.binding_digest
    source_items = value.items
    clipped = value.clipped
    if (
        type(selected_count) is not int
        or isinstance(selected_count, bool)
        or not 0 <= selected_count <= _INBOX_INDEX_MAX_ROWS
        or type(binding_digest) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", binding_digest) is None
        or type(source_items) is not tuple
        or len(source_items) != min(selected_count, limit)
        or type(clipped) is not bool
        or clipped is not (selected_count > limit)
    ):
        raise UploadInboxSnapshotError("inbox_failed")
    _bounded_inbox_utf8(binding_digest, 71)

    detached: list[tuple[str, str, str, str, bytes]] = []
    previous_handle: str | None = None
    subject_bytes = creator_subject_id.encode("ascii")
    for source_item in source_items:
        if type(source_item) is not UploadInboxItem:
            raise UploadInboxSnapshotError("inbox_failed")
        item_handle = source_item.item_handle
        item_binding = source_item.binding_digest
        index_key = source_item.index_key
        upload_id = source_item.upload_id
        canonical_row = source_item.canonical_row
        if (
            type(item_handle) is not str
            or re.fullmatch(r"hi1:upload:[0-9a-f]{64}", item_handle) is None
            or type(item_binding) is not str
            or item_binding != "sha256:" + item_handle.removeprefix("hi1:upload:")
            or type(index_key) is not str
            or type(upload_id) is not str
            or not is_valid_upload_id(upload_id)
            or type(canonical_row) is not bytes
            or not canonical_row
            or len(canonical_row) > _INBOX_ROW_MAX_BYTES
            or previous_handle is not None
            and item_handle <= previous_handle
        ):
            raise UploadInboxSnapshotError("inbox_failed")
        key_bytes = _bounded_inbox_utf8(index_key, _INBOX_ROW_MAX_BYTES)
        try:
            row = json.loads(
                canonical_row.decode("ascii"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
                parse_float=_strict_json_float,
            )
        except UploadInboxSnapshotError:
            raise
        except (UnicodeError, ValueError, RecursionError, MemoryError):
            raise UploadInboxSnapshotError("inbox_failed") from None
        if type(row) is not dict or _canonical_inbox_json(row) != canonical_row:
            raise UploadInboxSnapshotError("inbox_failed")
        row_owner = row.get("owner")
        row_subject = row.get("creator_subject_id")
        row_upload_id = row.get("id")
        row_hash = row.get("hash")
        if (
            type(row_owner) is not str
            or row_owner != owner_alias
            or type(row_subject) is not str
            or row_subject != creator_subject_id
            or type(row_upload_id) is not str
            or row_upload_id != upload_id
            or type(row_hash) is not str
            or _UPLOAD_HASH_RE.fullmatch(row_hash) is None
            or index_key != f"{owner_alias}:{creator_subject_id}:{row_hash}"
        ):
            raise UploadInboxSnapshotError("inbox_failed")
        item_digest = hashlib.sha256(
            b"odysseus.harbour_inbox.upload_item.v3\0"
            + _inbox_frame(owner_bytes)
            + _inbox_frame(subject_bytes)
            + _inbox_frame(key_bytes)
            + _inbox_frame(canonical_row)
        ).hexdigest()
        if item_handle != "hi1:upload:" + item_digest or item_binding != "sha256:" + item_digest:
            raise UploadInboxSnapshotError("inbox_failed")
        detached.append((item_handle, item_binding, index_key, upload_id, canonical_row))
        previous_handle = item_handle
    if not clipped:
        source = hashlib.sha256(
            b"odysseus.harbour_inbox.upload_source.v3\0"
            + _inbox_frame(owner_bytes)
            + _inbox_frame(subject_bytes)
        )
        for _, _, index_key, _, canonical_row in sorted(detached, key=lambda item: item[2]):
            source.update(_inbox_frame(_bounded_inbox_utf8(index_key, _INBOX_ROW_MAX_BYTES)))
            source.update(_inbox_frame(canonical_row))
        source.update(selected_count.to_bytes(8, "big"))
        if binding_digest != "sha256:" + source.hexdigest():
            raise UploadInboxSnapshotError("inbox_failed")
    return selected_count, binding_digest, tuple(detached), clipped


def is_valid_upload_id(upload_id: str) -> bool:
    """Return True when *upload_id* matches the canonical uploads.json id format."""
    return UPLOAD_ID_RE.fullmatch(upload_id or "") is not None


def _build_upload_id(safe_filename: str) -> str:
    """Build a unique upload id whose extension matches UPLOAD_ID_RE.

    secure_filename keeps '_' and '-', so an extension like '.jpg-1' (the
    suffix browsers append to duplicate downloads) or '.v1_final' produced an
    id that failed is_valid_upload_id, making the saved file permanently
    unreadable (every read path gates on validate_upload_id). Sanitize the
    extension to the single-alnum shape the id contract requires.
    """
    _, ext = os.path.splitext(safe_filename or "")
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)
    return uuid.uuid4().hex + (("." + ext) if ext else "")


def count_recent_uploads(timestamps, now: float, window: float = 10.0) -> int:
    """Number of upload events in *timestamps* within the last *window* seconds.

    Used by the per-IP concurrency guard. The count is of genuine prior upload
    events — it must NOT scale with how many files are in the *current* request,
    or a single multi-file batch would reject itself (issue #1346)."""
    if not timestamps:
        return 0
    cutoff = now - window
    return sum(1 for t in timestamps if t > cutoff)


class UploadHandler:
    def __init__(self, base_dir: str, upload_dir: str):
        self.base_dir = base_dir
        self.upload_dir = upload_dir
        self.max_upload_size = get_chat_upload_max_bytes()
        self.max_concurrent_uploads = 3
        self.cleanup_days = 30
        # Per-IP per-minute cap. save_upload() counts EACH file, and the chat
        # composer lets a user attach up to MAX_FILES (10, static/js/fileHandler.js)
        # in one batch — so this must comfortably exceed 10, or a single 6+ file
        # attach is rejected mid-batch (issue #1346: "5 work, 6 fail"). Burst abuse
        # is separately bounded by max_concurrent_uploads. Headroom for a few full
        # batches per minute.
        self.upload_rate_limit = 60  # max 60 file-uploads per minute per IP
        self.upload_rate_window = 60  # 60 seconds
        
        # Track upload rates
        self.upload_rate_log: Dict[str, list] = {}
        self._upload_rate_lock = threading.Lock()
        self._upload_rate_counter = 0
        self._upload_rate_max_entries = 1000
        # Serialise the read-modify-write of uploads.json within one
        # Python process. Scope: single FastAPI worker (the default
        # uvicorn deployment). Cross-process / multi-worker deployments
        # need an additional file-level lock (flock) or a database;
        # the atomic-rename write below keeps on-disk state consistent
        # on its own but does not serialise writers across processes.
        self._index_lock = threading.Lock()
        
        # Create upload directory
        os.makedirs(self.upload_dir, exist_ok=True)
        
        # Initialize file detector
        try:
            import magic
            self.file_detector = magic.Magic(mime=True)
        except Exception:
            self.file_detector = None
            logger.warning("python-magic not available, falling back to basic detection")
    
    def inside_base_dir(self, path: str) -> bool:
        """Check if path is inside base directory"""
        base = os.path.realpath(self.base_dir)
        p = os.path.realpath(path)
        try:
            return os.path.commonpath([base, p]) == base
        except Exception:
            return False
    
    def get_upload_dir(self):
        """Get date-based upload directory"""
        now = datetime.now()
        upload_dir = os.path.join(self.upload_dir, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir
    
    def calculate_file_hash(self, file_obj) -> str:
        """Calculate SHA-256 hash of file content."""
        file_obj.seek(0)
        hash_sha256 = hashlib.sha256()
        for chunk in iter(lambda: file_obj.read(4096), b""):
            hash_sha256.update(chunk)
        file_obj.seek(0)
        return hash_sha256.hexdigest()
    
    def detect_content_type(self, file_obj, original_filename: str) -> str:
        """Detect MIME type based on file content, with extension fallback."""
        content_type = "application/octet-stream"
        if self.file_detector:
            try:
                file_obj.seek(0)
                content_type = self.file_detector.from_buffer(file_obj.read(1024))
                file_obj.seek(0)
            except Exception as e:
                logger.warning(f"Failed to detect content type: {e}")
        
        if not content_type or content_type == "application/octet-stream":
            _, ext = os.path.splitext(original_filename.lower())
            if ext:
                content_type = mimetypes.guess_type(original_filename)[0] or content_type
        
        return content_type
        
    def is_image_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is an image based on extension or content type."""
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
        image_mime_types = {
            'image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in image_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in image_mime_types:
            return True
            
        return False
        
    def is_document_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is a document based on extension or content type."""
        document_extensions = {
            '.pdf', '.docx', '.xlsx', '.pptx', '.xls', '.epub',
            '.txt', '.py', '.js', '.html', '.htm',
            '.css', '.json', '.md', '.csv', '.log', '.xml', '.yml',
            '.yaml', '.nix', '.sql', '.sh', '.bash', '.c', '.cpp', '.h',
            '.java', '.go', '.rs', '.php', '.rb', '.ts', '.jsx', '.tsx'
        }
        document_mime_types = {
            'application/pdf', 
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'application/vnd.ms-excel',
            'application/epub+zip',
            'text/plain'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in document_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in document_mime_types:
            return True
            
        return False
            
    def is_audio_file(self, filename: str, content_type: str = None) -> bool:
        """Check if a file is an audio file based on extension or content type."""
        audio_extensions = {'.webm', '.wav', '.mp3', '.m4a', '.ogg'}
        audio_mime_types = {
            'audio/webm', 'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg'
        }
        
        # Check by extension
        _, ext = os.path.splitext(filename.lower())
        if ext in audio_extensions:
            return True
            
        # Check by content type if provided
        if content_type and content_type in audio_mime_types:
            return True
            
        return False
    
    def is_safe_file_type(self, content_type: str, filename: str) -> bool:
        """Check if file type is safe to store and serve."""
        dangerous_types = {
            'application/x-executable', 'application/x-sharedlib',
            'application/x-dll', 'application/x-msdownload',
            'application/x-sh', 'application/x-bat', 'application/x-vbs',
            'application/javascript', 'application/x-javascript'
        }
        
        dangerous_extensions = {
            '.exe', '.dll', '.bat', '.cmd', '.vbs', 
            '.ps1', '.jsp', '.asp', '.aspx'
        }
        
        if content_type in dangerous_types:
            return False
        
        _, ext = os.path.splitext(filename.lower())
        if ext in dangerous_extensions:
            return False
        
        return True
    
    def cleanup_old_uploads(self):
        """Remove uploaded files older than CLEANUP_DAYS days."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.cleanup_days)
            cleaned_count = 0
            
            for root, dirs, files in os.walk(self.upload_dir):
                if root == self.upload_dir:
                    continue
                    
                path_parts = root.split(os.sep)
                if len(path_parts) >= 4:
                    try:
                        dir_date = datetime(int(path_parts[-3]), int(path_parts[-2]), int(path_parts[-1]))
                        if dir_date < cutoff_date:
                            for file in files:
                                file_path = os.path.join(root, file)
                                try:
                                    os.remove(file_path)
                                    cleaned_count += 1
                                    logger.info(f"Cleaned up old upload: {file_path}")
                                except Exception as e:
                                    logger.warning(f"Failed to remove {file_path}: {e}")
                            
                            try:
                                os.rmdir(root)
                                logger.info(f"Removed empty upload directory: {root}")
                            except Exception as e:
                                logger.warning(f"Failed to remove directory {root}: {e}")
                    except (ValueError, IndexError):
                        continue
            
            logger.info(f"Upload cleanup completed: {cleaned_count} files removed")
            return cleaned_count
        except Exception as e:
            logger.error(f"Upload cleanup failed: {e}")
            return 0
    
    def validate_upload_id(self, upload_id: str) -> bool:
        """Validate that the upload ID matches the expected pattern."""
        return is_valid_upload_id(upload_id)

    def _inside_upload_dir(self, path: str) -> bool:
        """Check if path is inside the upload directory."""
        base = os.path.realpath(self.upload_dir)
        p = os.path.realpath(path)
        try:
            return os.path.commonpath([base, p]) == base
        except Exception:
            return False

    @staticmethod
    def _remove_new_upload_file(path: str) -> None:
        """Remove only a file created by the current upload call."""
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        except OSError:
            raise HTTPException(status_code=500, detail="Upload cleanup failed") from None

    def _atomic_write_json(self, path: str, data: dict) -> None:
        """Write `data` to `path` atomically: write to a temp file in the
        same directory, then `os.replace` onto the target. The kernel
        guarantees `os.replace` is atomic on POSIX, so a reader either
        sees the old contents or the new contents, never a half-written
        file. Also keeps a `.bak` sibling of the previous good state.
        """
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".uploads-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(path):
                bak = path + ".bak"
                try:
                    shutil.copy2(path, bak)
                except OSError:
                    pass
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load_upload_index(self) -> Dict[str, Any]:
        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        if not os.path.exists(uploads_db_path):
            return {}
        # Try the live file first, fall back to the .bak sibling if the
        # live file is truncated/corrupted (e.g. a previous writer was
        # SIGKILL'd mid-rename before the new code path was deployed).
        for candidate in (uploads_db_path, uploads_db_path + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning(f"Failed to read uploads database ({candidate}): {e}")
                continue
        return {}

    @staticmethod
    def _read_strict_upload_index_candidate(path: str) -> Optional[dict[str, Any]]:
        try:
            with open(path, "rb") as handle:
                raw = handle.read(_INBOX_INDEX_MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError:
            raise UploadInboxSnapshotError("inbox_failed") from None
        if len(raw) > _INBOX_INDEX_MAX_BYTES:
            raise UploadInboxSnapshotError("inbox_failed")
        try:
            text = raw.decode("utf-8")
            data = json.loads(
                text,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
                parse_float=_strict_json_float,
            )
        except UploadInboxSnapshotError:
            raise
        except (UnicodeError, ValueError, RecursionError, MemoryError):
            raise UploadInboxSnapshotError("inbox_failed") from None
        if type(data) is not dict or len(data) > _INBOX_INDEX_MAX_ROWS:
            raise UploadInboxSnapshotError("inbox_failed")
        for key in data:
            _bounded_inbox_utf8(key, _INBOX_ROW_MAX_BYTES)
        return data

    def _load_strict_upload_index(self) -> dict[str, Any]:
        """Read only the live index or a strictly validated backup."""
        live = os.path.join(self.upload_dir, "uploads.json")
        backup = live + ".bak"
        invalid = False
        for candidate in (live, backup):
            try:
                data = self._read_strict_upload_index_candidate(candidate)
            except UploadInboxSnapshotError:
                invalid = True
                continue
            if data is not None:
                return data
        if invalid:
            raise UploadInboxSnapshotError("inbox_failed")
        return {}

    def capture_inbox_snapshot(
        self,
        *,
        owner_alias: str,
        creator_subject_id: str,
        limit: int,
        expected_capture: Optional[UploadInboxSnapshot] = None,
    ) -> UploadInboxSnapshot:
        """Capture current SID-bound upload metadata without reading upload content."""
        if (
            type(owner_alias) is not str
            or not owner_alias
            or type(creator_subject_id) is not str
            or SUBJECT_ID_PATTERN.fullmatch(creator_subject_id) is None
            or type(limit) is not int
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise UploadInboxSnapshotError("inbox_failed")
        owner_bytes = _bounded_inbox_utf8(owner_alias, 320)
        expected_values = None
        if expected_capture is not None:
            expected_values = _validated_expected_inbox_snapshot(
                expected_capture,
                owner_alias=owner_alias,
                creator_subject_id=creator_subject_id,
                limit=limit,
                owner_bytes=owner_bytes,
            )

        current_prefix = f"{owner_alias}:{creator_subject_id}:"
        selected: list[tuple[str, str, bytes]] = []
        with self._index_lock:
            index = self._load_strict_upload_index()
            for index_key, row in index.items():
                if type(row) is not dict:
                    if index_key.startswith(current_prefix):
                        raise UploadInboxSnapshotError("inbox_failed")
                    continue
                row_subject = row.get("creator_subject_id")
                if row_subject != creator_subject_id:
                    if index_key.startswith(current_prefix):
                        raise UploadInboxSnapshotError("inbox_failed")
                    continue
                row_owner = row.get("owner")
                upload_id = row.get("id")
                file_hash = row.get("hash")
                if (
                    type(row_subject) is not str
                    or type(row_owner) is not str
                    or row_owner != owner_alias
                    or type(upload_id) is not str
                    or not is_valid_upload_id(upload_id)
                    or type(file_hash) is not str
                    or _UPLOAD_HASH_RE.fullmatch(file_hash) is None
                    or index_key != f"{owner_alias}:{creator_subject_id}:{file_hash}"
                ):
                    raise UploadInboxSnapshotError("inbox_failed")
                selected.append((index_key, upload_id, _canonical_inbox_json(row)))

        selected.sort(key=lambda value: value[0])
        source = hashlib.sha256(
            b"odysseus.harbour_inbox.upload_source.v3\0"
            + _inbox_frame(owner_bytes)
            + _inbox_frame(creator_subject_id.encode("ascii"))
        )
        items: list[UploadInboxItem] = []
        for index_key, upload_id, canonical_row in selected:
            key_bytes = _bounded_inbox_utf8(index_key, _INBOX_ROW_MAX_BYTES)
            source.update(_inbox_frame(key_bytes))
            source.update(_inbox_frame(canonical_row))
            item = hashlib.sha256(
                b"odysseus.harbour_inbox.upload_item.v3\0"
                + _inbox_frame(owner_bytes)
                + _inbox_frame(creator_subject_id.encode("ascii"))
                + _inbox_frame(key_bytes)
                + _inbox_frame(canonical_row)
            ).hexdigest()
            items.append(UploadInboxItem(
                item_handle="hi1:upload:" + item,
                binding_digest="sha256:" + item,
                index_key=index_key,
                upload_id=upload_id,
                canonical_row=canonical_row,
            ))
        source.update(len(selected).to_bytes(8, "big"))
        items.sort(key=lambda value: value.item_handle)
        snapshot = UploadInboxSnapshot(
            selected_count=len(selected),
            binding_digest="sha256:" + source.hexdigest(),
            items=tuple(items[:limit]),
            clipped=len(selected) > limit,
        )
        snapshot_values = (
            snapshot.selected_count,
            snapshot.binding_digest,
            tuple(
                (item.item_handle, item.binding_digest, item.index_key, item.upload_id, item.canonical_row)
                for item in snapshot.items
            ),
            snapshot.clipped,
        )
        if expected_values is not None and snapshot_values != expected_values:
            raise UploadInboxSnapshotError("inbox_failed")
        return snapshot

    def get_upload_info(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Return the uploads.json metadata row for an upload ID, if present."""
        if not self.validate_upload_id(upload_id):
            return None
        for info in self._load_upload_index().values():
            if isinstance(info, dict) and info.get("id") == upload_id:
                return dict(info)
        return None

    def _renamed_upload_index_key(self, key: str, info: Dict[str, Any], old_owner: str, new_owner: str) -> str:
        """Return the storage key to use after renaming an owned upload row."""
        if isinstance(key, str) and ":" in key:
            owner_part, rest = key.split(":", 1)
            if owner_part.strip().lower() == old_owner:
                return f"{new_owner}:{rest}"
        file_hash = info.get("hash")
        if file_hash:
            creator_subject_id = info.get("creator_subject_id")
            if isinstance(creator_subject_id, str) and SUBJECT_ID_PATTERN.fullmatch(creator_subject_id):
                return f"{new_owner}:{creator_subject_id}:{file_hash}"
            return f"{new_owner}:{file_hash}"
        return key

    def _unique_upload_index_key(self, base_key: str, used_keys: set, reserved_keys: set, info: Dict[str, Any]) -> str:
        """Choose a deterministic collision key without overwriting an existing row."""
        if base_key not in used_keys and base_key not in reserved_keys:
            return base_key

        upload_id = str(info.get("id") or "renamed").strip() or "renamed"
        candidate = f"{base_key}:{upload_id}"
        if candidate not in used_keys and candidate not in reserved_keys:
            return candidate

        index = 2
        while True:
            candidate = f"{base_key}:{upload_id}:{index}"
            if candidate not in used_keys and candidate not in reserved_keys:
                return candidate
            index += 1

    def rename_owner(self, old_owner: str, new_owner: str) -> int:
        """Rename upload metadata ownership from old_owner to new_owner.

        Upload rows are keyed by owner-qualified hashes for dedupe and also
        carry an `owner` field for access checks. Both must move together when
        usernames change.
        """
        old_owner_normalized = str(old_owner or "").strip().lower()
        new_owner = str(new_owner or "").strip()
        if not old_owner_normalized or not new_owner:
            return 0
        if old_owner_normalized == new_owner.lower():
            return 0

        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        with self._index_lock:
            current = self._load_upload_index()
            if not current:
                return 0

            updated = {}
            renamed = 0
            original_keys = set(current.keys())

            for key, info in current.items():
                new_key = key
                new_info = info
                if isinstance(info, dict) and str(info.get("owner", "")).strip().lower() == old_owner_normalized:
                    new_info = dict(info)
                    new_info["owner"] = new_owner
                    base_key = self._renamed_upload_index_key(key, new_info, old_owner_normalized, new_owner)
                    new_key = self._unique_upload_index_key(
                        base_key,
                        set(updated.keys()),
                        original_keys - {key},
                        new_info,
                    )
                    if new_key != base_key:
                        logger.warning(
                            "Upload owner rename key collision for %s -> %s at %s; preserving row as %s",
                            old_owner_normalized,
                            new_owner,
                            base_key,
                            new_key,
                        )
                    renamed += 1
                updated[new_key] = new_info

            if renamed:
                self._atomic_write_json(uploads_db_path, updated)
            return renamed

    def _find_upload_path(self, upload_id: str) -> Optional[str]:
        """Find an upload file by ID while staying inside upload_dir."""
        if not self.validate_upload_id(upload_id):
            return None

        direct = os.path.join(self.upload_dir, upload_id)
        if os.path.exists(direct) and self._inside_upload_dir(direct):
            return direct

        for root, _dirs, files in os.walk(self.upload_dir, followlinks=False):
            if upload_id in files:
                path = os.path.join(root, upload_id)
                if self._inside_upload_dir(path):
                    return path
        return None

    def resolve_upload(
        self,
        upload_id: str,
        owner: Optional[str] = None,
        auth_manager: Any = None,
        allow_admin: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an upload ID to metadata only if the caller may read it.

        This is the owner-aware lookup used by internal processors. Public
        download routes already perform owner checks; chat/document paths must
        do the same before reading file bytes server-side.
        """
        if not self.validate_upload_id(upload_id):
            logger.warning(f"Invalid upload ID format: {upload_id}")
            return None

        auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
        if auth_configured and not owner:
            return None

        info = self.get_upload_info(upload_id) or {}
        is_admin = False
        if allow_admin and owner and auth_manager and hasattr(auth_manager, "is_admin"):
            try:
                is_admin = bool(auth_manager.is_admin(owner))
            except Exception:
                is_admin = False

        if owner and not is_admin:
            if info.get("owner") != owner:
                logger.warning("Upload %s denied for owner %s", upload_id, owner)
                return None
        if not owner and info.get("owner") is not None:
            logger.warning("Upload %s denied without an authenticated owner", upload_id)
            return None

        path = info.get("path")
        if not path or not os.path.exists(path) or not self._inside_upload_dir(path):
            path = self._find_upload_path(upload_id)
        if not path:
            return None
        if not self._inside_upload_dir(path):
            logger.warning(f"Upload path outside upload directory: {path}")
            return None

        resolved = dict(info)
        resolved.setdefault("id", upload_id)
        resolved["path"] = path
        resolved.setdefault("name", os.path.basename(path))
        resolved.setdefault("original_name", resolved["name"])
        resolved.setdefault("mime", mimetypes.guess_type(path)[0] or "application/octet-stream")
        return resolved
    
    def cleanup_rate_limits(self):
        """Remove stale entries from upload_rate_log."""
        now = time.time()
        removed_ips = 0
        removed_timestamps = 0
        
        with self._upload_rate_lock:
            ips_to_delete = []
            for ip, timestamps in list(self.upload_rate_log.items()):
                new_ts = [t for t in timestamps if now - t < self.upload_rate_window]
                removed = len(timestamps) - len(new_ts)
                removed_timestamps += removed
                if new_ts:
                    self.upload_rate_log[ip] = new_ts
                else:
                    ips_to_delete.append(ip)
            
            for ip in ips_to_delete:
                del self.upload_rate_log[ip]
                removed_ips += 1
            
            if len(self.upload_rate_log) > self._upload_rate_max_entries:
                sorted_ips = sorted(
                    self.upload_rate_log.items(),
                    key=lambda item: max(item[1]) if item[1] else 0,
                    reverse=True
                )
                keep = dict(sorted_ips[:self._upload_rate_max_entries])
                dropped = len(self.upload_rate_log) - len(keep)
                self.upload_rate_log = keep
                logger.info(f"Rate-limit dict size exceeded. Dropped {dropped} oldest IP entries.")
        
        logger.info(f"Rate-limit cleanup: removed {removed_ips} IPs, {removed_timestamps} timestamps.")
    
    def get_upload_stats(self) -> Dict[str, Any]:
        """Get statistics about uploaded files."""
        try:
            total_files = 0
            total_size = 0
            file_types = {}
            
            uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
            if os.path.exists(uploads_db_path):
                with open(uploads_db_path, "r", encoding="utf-8") as f:
                    files = json.load(f)
                
                total_files = len(files)
                for file_info in files.values():
                    total_size += file_info.get("size", 0)
                    mime = file_info.get("mime", "unknown")
                    file_types[mime] = file_types.get(mime, 0) + 1
            
            return {
                "total_files": total_files,
                "total_size": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "file_types": file_types,
                "cleanup_days": self.cleanup_days
            }
        except Exception as e:
            logger.error(f"Failed to get upload stats: {e}")
            return {"error": str(e)}

    def register_generated_artifact(
        self,
        source_path: str,
        *,
        owner: str,
        allowed_root: str,
        display_name: str = None,
    ) -> Dict[str, Any]:
        """Register a trusted agent output in the existing owner upload store.

        This path is deliberately separate from HTTP uploads: it does not use
        client-IP rate limits, and an index-write failure is fatal.  Only the
        narrow interactive-deliverable types used by the agent are accepted.
        """

        normalized_owner = str(owner or "").strip()
        if not normalized_owner:
            raise ValueError("an authenticated owner is required")

        root = os.path.realpath(os.path.abspath(str(allowed_root or "")))
        source_lexical = os.path.abspath(str(source_path or ""))
        source = os.path.realpath(source_lexical)
        if not os.path.isdir(root):
            raise ValueError("allowed_root must be a directory")
        try:
            if os.path.commonpath([root, source]) != root:
                raise ValueError("generated artifact is outside the active workspace")
            if os.path.commonpath([os.path.abspath(str(allowed_root)), source_lexical]) != os.path.abspath(str(allowed_root)):
                raise ValueError("generated artifact path escapes the active workspace")
        except ValueError as exc:
            raise ValueError("generated artifact is outside the active workspace") from exc

        relative = os.path.relpath(source_lexical, os.path.abspath(str(allowed_root)))
        cursor = os.path.abspath(str(allowed_root))
        for part in relative.split(os.sep):
            cursor = os.path.join(cursor, part)
            if os.path.islink(cursor):
                raise ValueError("generated artifact paths must not contain symlinks")

        safe_name = secure_filename(display_name or os.path.basename(source))
        source_ext = os.path.splitext(source)[1].lower()
        name_ext = os.path.splitext(safe_name)[1].lower()
        allowed_extensions = {".py", ".html", ".png"}
        if source_ext not in allowed_extensions:
            raise ValueError("generated artifact type is not publishable")
        if name_ext != source_ext:
            raise ValueError("display name must keep the generated file extension")

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(source, flags)
        except OSError as exc:
            raise ValueError("generated artifact cannot be opened safely") from exc

        try:
            with os.fdopen(fd, "rb") as file_obj:
                file_stat = os.fstat(file_obj.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ValueError("generated artifact must be a regular file")
                if file_stat.st_size <= 0:
                    raise ValueError("generated artifact is empty")
                if file_stat.st_size > self.max_upload_size:
                    raise ValueError("generated artifact exceeds the upload size limit")

                content_type = self.detect_content_type(file_obj, safe_name)
                if not self.is_safe_file_type(content_type, safe_name):
                    raise ValueError("generated artifact type is not safe to serve")
                file_hash = self.calculate_file_hash(file_obj)

                width = None
                height = None
                if source_ext == ".png":
                    try:
                        from PIL import Image

                        file_obj.seek(0)
                        with Image.open(file_obj) as image:
                            image.verify()
                            if image.format != "PNG":
                                raise ValueError("generated PNG has invalid content")
                        file_obj.seek(0)
                        with Image.open(file_obj) as image:
                            width, height = image.size
                        file_obj.seek(0)
                        content_type = "image/png"
                    except (OSError, ValueError) as exc:
                        raise ValueError("generated PNG has invalid content") from exc

                uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
                with self._index_lock:
                    current = self._load_upload_index()
                    stale_keys = []
                    for key, info in current.items():
                        if (
                            info.get("hash") != file_hash
                            or info.get("owner") != normalized_owner
                            or os.path.splitext(str(info.get("name") or ""))[1].lower() != source_ext
                        ):
                            continue
                        stored_path = info.get("path")
                        if stored_path and os.path.isfile(stored_path) and self._inside_upload_dir(stored_path):
                            existing = dict(info)
                            existing["is_duplicate"] = True
                            return existing
                        stale_keys.append(key)
                    for key in stale_keys:
                        current.pop(key, None)

                    file_id = _build_upload_id(safe_name)
                    target_dir = self.get_upload_dir()
                    target_path = os.path.join(target_dir, file_id)
                    now = datetime.now().isoformat()
                    metadata = {
                        "id": file_id,
                        "path": target_path,
                        "mime": content_type,
                        "size": file_stat.st_size,
                        "name": safe_name,
                        "hash": file_hash,
                        "original_name": safe_name,
                        "uploaded_at": now,
                        "last_accessed": now,
                        "owner": normalized_owner,
                        "origin": "agent_generated",
                    }
                    if width and height:
                        metadata["width"] = width
                        metadata["height"] = height

                    try:
                        file_obj.seek(0)
                        with open(target_path, "xb") as target:
                            shutil.copyfileobj(file_obj, target, length=8192)
                            target.flush()
                            os.fsync(target.fileno())
                        current[f"{normalized_owner}:{file_hash}:{source_ext.lstrip('.')}"] = metadata
                        self._atomic_write_json(uploads_db_path, current)
                    except Exception:
                        try:
                            os.remove(target_path)
                        except OSError:
                            pass
                        raise
                    return metadata
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("generated artifact registration failed") from exc
    
    def save_upload(
        self,
        u: UploadFile,
        client_ip: str,
        owner: str = None,
        *,
        creator_subject_id: Optional[str] = None,
    ) -> dict:
        """Save uploaded file with enhanced security and organization."""
        if creator_subject_id is not None and (
            type(creator_subject_id) is not str
            or SUBJECT_ID_PATTERN.fullmatch(creator_subject_id) is None
        ):
            raise HTTPException(status_code=400, detail="Invalid upload owner identity")

        # Rate limiting
        now = time.time()
        with self._upload_rate_lock:
            if client_ip not in self.upload_rate_log:
                self.upload_rate_log[client_ip] = []
            
            self.upload_rate_log[client_ip] = [
                timestamp for timestamp in self.upload_rate_log[client_ip]
                if now - timestamp < self.upload_rate_window
            ]
            
            if len(self.upload_rate_log[client_ip]) >= self.upload_rate_limit:
                raise HTTPException(
                    status_code=429,
                    detail="Upload rate limit exceeded. Please try again later."
                )
            
            self.upload_rate_log[client_ip].append(now)
            self._upload_rate_counter += 1
        
        if self._upload_rate_counter % 100 == 0:
            self.cleanup_rate_limits()
        
        # Validate file size
        file_obj = u.file
        file_obj.seek(0, 2)
        file_size = file_obj.tell()
        file_obj.seek(0)
        
        if file_size == 0:
            raise HTTPException(400, "File is empty")
            
        if file_size > self.max_upload_size:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds {format_byte_limit(self.max_upload_size)} limit"
            )
        
        # Get original filename and sanitize it
        original_filename = u.filename or f"upload_{int(time.time())}"
        safe_filename = secure_filename(original_filename)
        
        # Detect content type
        content_type = self.detect_content_type(file_obj, safe_filename)
        
        # Check if file type is safe
        if not self.is_safe_file_type(content_type, safe_filename):
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed: {content_type}"
            )
        
        # Calculate file hash for deduplication
        file_hash = self.calculate_file_hash(file_obj)
        
        # Check for duplicate files.
        # The duplicate-detection lookup AND the write must both happen
        # under _index_lock: a duplicate upload racing with a new-entry
        # insert must not overwrite a newer snapshot of the index with
        # the stale one read before the insert.
        uploads_db_path = os.path.join(self.upload_dir, "uploads.json")
        existing_file = None
        with self._index_lock:
            existing_files = self._load_upload_index()
            stale_keys = []
            for key, info in existing_files.items():
                if (
                    info.get("hash") == file_hash
                    and info.get("owner") == owner
                    and info.get("creator_subject_id") == creator_subject_id
                ):
                    stored_path = info.get("path")
                    if stored_path and os.path.exists(stored_path) and self._inside_upload_dir(stored_path):
                        existing_file = info
                        break
                    stale_keys.append(key)
            if stale_keys:
                for key in stale_keys:
                    existing_files.pop(key, None)
                try:
                    self._atomic_write_json(uploads_db_path, existing_files)
                    logger.info("Removed %d stale upload index entries for missing duplicates", len(stale_keys))
                except Exception:
                    if creator_subject_id is not None:
                        raise HTTPException(status_code=500, detail="Failed to save upload")
                    logger.warning("Failed to remove stale upload index entries")
        if existing_file:
            logger.info(f"Duplicate file upload detected: {original_filename} -> {existing_file['id']}")

            with self._index_lock:
                try:
                    current = self._load_upload_index()
                    # Always re-resolve from the locked live snapshot.  The
                    # row at the previously observed key may have changed.
                    live_key = None
                    for k, v in current.items():
                        if (
                            v.get("hash") == file_hash
                            and v.get("owner") == owner
                            and v.get("creator_subject_id") == creator_subject_id
                        ):
                            live_key = k
                            existing_file = dict(v)
                            break
                    if live_key is None:
                        # No matching entry anymore (e.g. cleaned up between
                        # the outer read and the write). Fall through to the
                        # fresh-insert path below; release the lock first.
                        raise LookupError("upload entry vanished mid-dedupe")
                    existing_file["last_accessed"] = datetime.now().isoformat()
                    current[live_key] = existing_file
                    self._atomic_write_json(uploads_db_path, current)
                except LookupError:
                    existing_file = None
                except Exception:
                    if creator_subject_id is not None:
                        raise HTTPException(status_code=500, detail="Failed to save upload")
                    logger.warning("Failed to update uploads database")

            if existing_file:
                return {
                    "id": existing_file["id"],
                    "path": existing_file["path"],
                    "mime": existing_file["mime"],
                    "size": existing_file["size"],
                    "name": existing_file["original_name"],
                    "hash": file_hash,
                    "uploaded_at": existing_file["uploaded_at"],
                    "owner": existing_file.get("owner"),
                    "width": existing_file.get("width"),
                    "height": existing_file.get("height"),
                    "is_duplicate": True
                }
        
        # Generate unique ID and determine save location
        file_id = _build_upload_id(safe_filename)
        
        # Create date-based directory structure
        upload_dir = self.get_upload_dir()
        file_path = os.path.join(upload_dir, file_id)
        
        # Save the file
        file_created = False
        try:
            with open(file_path, "xb") as f:
                file_created = True
                while chunk := file_obj.read(8192):
                    f.write(chunk)
        except HTTPException:
            raise
        except Exception:
            if file_created and creator_subject_id is not None:
                self._remove_new_upload_file(file_path)
            raise HTTPException(status_code=500, detail="Failed to save file")
        
        # Create file metadata
        file_metadata = {
            "id": file_id,
            "path": file_path,
            "mime": content_type,
            "size": file_size,
            "name": safe_filename,
            "hash": file_hash,
            "original_name": original_filename,
            "uploaded_at": datetime.now().isoformat(),
            "last_accessed": datetime.now().isoformat(),
            "client_ip": client_ip,
            "owner": owner,
            "creator_subject_id": creator_subject_id,
        }
        # Capture image dimensions (EXIF-rotated) so the chat thumbnail skeleton
        # can size itself to the right aspect ratio before the bytes arrive.
        if content_type.startswith("image/"):
            try:
                from PIL import Image, ImageOps
                with Image.open(file_path) as _im:
                    _im = ImageOps.exif_transpose(_im)
                    file_metadata["width"] = _im.width
                    file_metadata["height"] = _im.height
            except Exception as e:
                logger.warning(f"Failed to read image dimensions for {file_id}: {e}")
        
        # Update uploads database
        with self._index_lock:
            try:
                current = self._load_upload_index() if os.path.exists(uploads_db_path) else {}
                if creator_subject_id is not None:
                    storage_key = f"{owner}:{creator_subject_id}:{file_hash}"
                    live_duplicate = None
                    stale_keys = []
                    for key, info in current.items():
                        if (
                            info.get("hash") != file_hash
                            or info.get("owner") != owner
                            or info.get("creator_subject_id") != creator_subject_id
                        ):
                            continue
                        stored_path = info.get("path")
                        if stored_path and os.path.exists(stored_path) and self._inside_upload_dir(stored_path):
                            live_duplicate = (key, dict(info))
                            break
                        stale_keys.append(key)

                    if live_duplicate is not None:
                        self._remove_new_upload_file(file_path)
                        file_created = False
                        live_key, existing = live_duplicate
                        existing["last_accessed"] = datetime.now().isoformat()
                        current[live_key] = existing
                        self._atomic_write_json(uploads_db_path, current)
                        return {
                            "id": existing["id"],
                            "path": existing["path"],
                            "mime": existing["mime"],
                            "size": existing["size"],
                            "name": existing["original_name"],
                            "hash": file_hash,
                            "uploaded_at": existing["uploaded_at"],
                            "owner": existing.get("owner"),
                            "width": existing.get("width"),
                            "height": existing.get("height"),
                            "is_duplicate": True,
                        }
                    for key in stale_keys:
                        current.pop(key, None)
                    if storage_key in current:
                        raise RuntimeError("upload index key conflict")
                else:
                    storage_key = f"{owner}:{file_hash}" if owner else file_hash
                current[storage_key] = file_metadata
                self._atomic_write_json(uploads_db_path, current)
            except HTTPException:
                raise
            except Exception:
                if creator_subject_id is not None:
                    if file_created:
                        self._remove_new_upload_file(file_path)
                    raise HTTPException(status_code=500, detail="Failed to save upload")
                logger.warning("Failed to update uploads database")
        
        logger.info(f"File uploaded successfully: {original_filename} ({file_size} bytes)")
        public_metadata = dict(file_metadata)
        public_metadata.pop("creator_subject_id", None)
        return public_metadata
