import base64
import json
import os
import posixpath
import time
import zipfile
from io import BytesIO
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .vault_security import (
    EXPORT_MANIFEST,
    EXPORT_PAYLOAD,
    EXPORT_VERSION,
    MAX_ARCHIVE_BYTES,
    MAX_IMPORT_FILES,
    MAX_IMPORT_UNCOMPRESSED_BYTES,
    PBKDF2_ITERATIONS,
    RESERVED_ARCHIVE_BASENAMES,
    RESERVED_OBSIDIAN_PATHS,
    STATE_FILENAME,
    VaultArchive,
    VaultSecurityError,
    _derive_key,
    require_unlocked,
)


def validate_archive_member(name: str) -> str:
    cleaned = (name or "").replace("\\", "/").strip()
    if not cleaned or cleaned.endswith("/"):
        raise VaultSecurityError("Archive entry is not a file")
    drive_like = len(cleaned) >= 2 and cleaned[1] == ":"
    if cleaned.startswith("/") or cleaned.startswith("//") or drive_like:
        raise VaultSecurityError("Archive contains an absolute path")
    normalized = posixpath.normpath(cleaned)
    if normalized == "." or normalized.startswith("../") or normalized == "..":
        raise VaultSecurityError("Archive contains a path traversal entry")
    parts = normalized.split("/")
    if parts[0] == ".obsidian" or normalized in RESERVED_OBSIDIAN_PATHS:
        raise VaultSecurityError("Archive may not contain reserved internal files")
    if any(part in RESERVED_ARCHIVE_BASENAMES or part == EXPORT_MANIFEST for part in parts):
        raise VaultSecurityError("Archive may not contain reserved import/export metadata")
    return normalized


def _iter_vault_files(vault_dir: str, root: str = ""):
    base = os.path.abspath(vault_dir)
    start = os.path.abspath(os.path.join(base, root.strip("/\\")))
    if os.path.commonpath([base, start]) != base:
        raise VaultSecurityError("Export path is outside the vault")
    if not os.path.exists(start):
        raise VaultSecurityError("Export path does not exist")
    if os.path.isfile(start):
        rel = os.path.relpath(start, base).replace("\\", "/")
        if os.path.basename(rel) != STATE_FILENAME:
            yield rel, start
        return
    for dirpath, dirs, files in os.walk(start):
        dirs[:] = [d for d in dirs if d not in {".obsidian", "__pycache__"}]
        for filename in files:
            if filename == STATE_FILENAME:
                continue
            abs_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(abs_path, base).replace("\\", "/")
            yield rel_path, abs_path


def _build_plain_zip(vault_dir: str, root: str = "") -> tuple[bytes, int]:
    buffer = BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format": "odysseus-obsidian-vault",
            "version": EXPORT_VERSION,
            "encrypted": False,
            "created_at": int(time.time()),
        }
        zf.writestr(EXPORT_MANIFEST, json.dumps(manifest, sort_keys=True))
        for rel_path, abs_path in _iter_vault_files(vault_dir, root):
            zf.write(abs_path, rel_path)
            count += 1
    return buffer.getvalue(), count


def export_vault(vault_dir: str, password: Optional[str] = None, root: str = "") -> VaultArchive:
    require_unlocked(vault_dir)
    plain_zip, count = _build_plain_zip(vault_dir, root)
    if not password:
        return VaultArchive(
            data=plain_zip,
            encrypted=False,
            file_count=count,
            filename="obsidian-vault.zip",
        )

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    encrypted = AESGCM(key).encrypt(nonce, plain_zip, None)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format": "odysseus-obsidian-vault",
            "version": EXPORT_VERSION,
            "encrypted": True,
            "kdf": "pbkdf2-sha256",
            "iterations": PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "created_at": int(time.time()),
        }
        zf.writestr(EXPORT_MANIFEST, json.dumps(manifest, sort_keys=True))
        zf.writestr(EXPORT_PAYLOAD, encrypted)
    return VaultArchive(
        data=buffer.getvalue(),
        encrypted=True,
        file_count=count,
        filename="obsidian-vault.encrypted.zip",
    )


def _read_zip_bytes(archive_data: bytes, password: Optional[str]) -> bytes:
    if len(archive_data) > MAX_ARCHIVE_BYTES:
        raise VaultSecurityError("Archive is too large")
    try:
        with zipfile.ZipFile(BytesIO(archive_data), "r") as zf:
            names = set(zf.namelist())
            if EXPORT_MANIFEST in names and EXPORT_PAYLOAD in names:
                manifest = json.loads(zf.read(EXPORT_MANIFEST).decode("utf-8"))
                if manifest.get("encrypted"):
                    if not password:
                        raise VaultSecurityError("Password is required")
                    try:
                        salt = base64.b64decode(manifest["salt"])
                        nonce = base64.b64decode(manifest["nonce"])
                        iterations = int(manifest.get("iterations") or PBKDF2_ITERATIONS)
                        key = _derive_key(password, salt, iterations)
                        return AESGCM(key).decrypt(nonce, zf.read(EXPORT_PAYLOAD), None)
                    except (InvalidTag, KeyError, ValueError) as exc:
                        raise VaultSecurityError("Invalid password or corrupted archive") from exc
            return archive_data
    except zipfile.BadZipFile as exc:
        raise VaultSecurityError("Archive is not a valid ZIP file") from exc


def import_vault(vault_dir: str, archive_data: bytes, password: Optional[str] = None) -> dict:
    require_unlocked(vault_dir)
    plain_archive = _read_zip_bytes(archive_data, password)
    imported = 0
    total_size = 0
    planned: list[tuple[str, zipfile.ZipInfo]] = []
    seen_paths: set[str] = set()
    try:
        with zipfile.ZipFile(BytesIO(plain_archive), "r") as zf:
            names = set(zf.namelist())
            if EXPORT_PAYLOAD in names:
                raise VaultSecurityError("Archive contains unexpected encrypted payload metadata")
            for info in zf.infolist():
                if info.is_dir() or info.filename == EXPORT_MANIFEST:
                    continue
                rel_path = validate_archive_member(info.filename)
                if info.file_size < 0:
                    raise VaultSecurityError("Archive contains an invalid file")
                total_size += info.file_size
                if len(planned) >= MAX_IMPORT_FILES:
                    raise VaultSecurityError("Archive contains too many files")
                if total_size > MAX_IMPORT_UNCOMPRESSED_BYTES:
                    raise VaultSecurityError("Archive expands beyond the size limit")
                rel_key = rel_path.lower()
                if rel_key in seen_paths:
                    raise VaultSecurityError(f"Archive contains duplicate paths: {rel_path}")
                seen_paths.add(rel_key)
                target = os.path.abspath(os.path.join(vault_dir, rel_path))
                if os.path.commonpath([os.path.abspath(vault_dir), target]) != os.path.abspath(vault_dir):
                    raise VaultSecurityError("Archive entry escapes the vault")
                if os.path.exists(target):
                    raise VaultSecurityError(f"Import conflict: {rel_path}")
                planned.append((rel_path, info))

            extracted: list[tuple[str, bytes]] = []
            pwd = password.encode("utf-8") if password else None
            for rel_path, info in planned:
                try:
                    with zf.open(info, "r", pwd=pwd) as src:
                        extracted.append((rel_path, src.read()))
                except RuntimeError as exc:
                    raise VaultSecurityError("Invalid password or encrypted archive unsupported") from exc

            os.makedirs(vault_dir, exist_ok=True)
            for rel_path, data in extracted:
                target = os.path.abspath(os.path.join(vault_dir, rel_path))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as dst:
                    dst.write(data)
                imported += 1
    except zipfile.BadZipFile as exc:
        raise VaultSecurityError("Archive is not a valid ZIP file") from exc
    return {"imported_files": imported, "bytes": total_size}
