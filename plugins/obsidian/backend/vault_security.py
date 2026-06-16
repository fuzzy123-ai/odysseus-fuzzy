import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass


STATE_FILENAME = ".odysseus-vault.json"
EXPORT_MANIFEST = "odysseus-vault.json"
EXPORT_PAYLOAD = "vault.bin"
EXPORT_VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_IMPORT_FILES = 5000
MAX_IMPORT_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
PBKDF2_ITERATIONS = 390000
RESERVED_ARCHIVE_BASENAMES = {
    STATE_FILENAME,
    EXPORT_PAYLOAD,
}
RESERVED_OBSIDIAN_PATHS = {
    ".obsidian/history.json",
    ".obsidian/relationships.json",
    ".obsidian/project_planning_sessions.json",
}


class VaultSecurityError(ValueError):
    pass


@dataclass
class VaultArchive:
    data: bytes
    encrypted: bool
    file_count: int
    filename: str


def _state_path(vault_dir: str) -> str:
    return os.path.join(vault_dir, STATE_FILENAME)


def _load_state(vault_dir: str) -> dict:
    try:
        with open(_state_path(vault_dir), "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise VaultSecurityError("Vault protection metadata is unreadable") from exc


def _save_state(vault_dir: str, state: dict) -> None:
    os.makedirs(vault_dir, exist_ok=True)
    path = _state_path(vault_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    if not password:
        raise VaultSecurityError("Password is required")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def _password_hash(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return base64.b64encode(_derive_key(password, salt, iterations)).decode("ascii")


def _verify_password(state: dict, password: str) -> bool:
    if not state.get("protected"):
        return True
    try:
        salt = base64.b64decode(state["salt"])
        expected = state["password_hash"]
        iterations = int(state.get("iterations") or PBKDF2_ITERATIONS)
        actual = _password_hash(password, salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def protection_status(vault_dir: str) -> dict:
    state = _load_state(vault_dir)
    return {
        "protected": bool(state.get("protected")),
        "locked": bool(state.get("protected") and state.get("locked")),
        "version": state.get("version"),
    }


def require_unlocked(vault_dir: str) -> None:
    status = protection_status(vault_dir)
    if status["locked"]:
        raise VaultSecurityError("Vault is locked")


def set_password(vault_dir: str, password: str) -> dict:
    if not password or len(password) < 8:
        raise VaultSecurityError("Password must be at least 8 characters long")
    salt = os.urandom(16)
    state = {
        "version": 1,
        "protected": True,
        "locked": False,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": PBKDF2_ITERATIONS,
        "password_hash": _password_hash(password, salt),
        "updated_at": int(time.time()),
    }
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def lock_vault(vault_dir: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        raise VaultSecurityError("Vault has no password protection enabled")
    state["locked"] = True
    state["updated_at"] = int(time.time())
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def unlock_vault(vault_dir: str, password: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        return protection_status(vault_dir)
    if not _verify_password(state, password):
        raise VaultSecurityError("Invalid password")
    state["locked"] = False
    state["updated_at"] = int(time.time())
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def remove_password(vault_dir: str, password: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        return protection_status(vault_dir)
    if not _verify_password(state, password):
        raise VaultSecurityError("Invalid password")
    try:
        os.remove(_state_path(vault_dir))
    except FileNotFoundError:
        pass
    return protection_status(vault_dir)


def validate_archive_member(name: str) -> str:
    from .import_export import validate_archive_member as _validate_archive_member

    return _validate_archive_member(name)


def export_vault(vault_dir: str, password=None, root: str = "") -> VaultArchive:
    from .import_export import export_vault as _export_vault

    return _export_vault(vault_dir, password=password, root=root)


def import_vault(vault_dir: str, archive_data: bytes, password=None) -> dict:
    from .import_export import import_vault as _import_vault

    return _import_vault(vault_dir, archive_data, password=password)
