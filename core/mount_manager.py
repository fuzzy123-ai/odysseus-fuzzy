"""Persistent mount definitions for virtual Odysseus file-tool paths."""

from __future__ import annotations

import json
import os
import re
import fnmatch
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from src.constants import BASE_DIR, DATA_DIR


MOUNTS_FILE = os.path.join(DATA_DIR, "mounts.json")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SENSITIVE_BASENAMES = {
    ".ssh",
    ".gnupg",
    ".gitconfig",
    ".bashrc",
    ".bash_profile",
    ".bash_logout",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".profile",
    ".tcshrc",
    ".cshrc",
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".docker",
    ".kube",
}
_SENSITIVE_FILE_PATTERNS = (
    "authorized_keys",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "known_hosts",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*token*",
    "*secret*",
)

_WRITE_TOOLS = {"write_file", "edit_file"}
_DANGEROUS_WRITE_COMPONENTS = {
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "env",
    ".tox",
    "node_modules",
    "__pycache__",
    "site-packages",
    "dist",
    "build",
    "Program Files",
    "Program Files (x86)",
    "Windows",
    "System32",
    "Startup",
    "Autostart",
    "OneDrive",
    "Dropbox",
    "Google Drive",
    "iCloudDrive",
}

_ALLOWED_WRITE_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".html",
    ".xml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".ps1",
    ".bat",
    ".sql",
}

_MAX_WRITE_BYTES = 1_000_000


@dataclass(frozen=True)
class WritePolicy:
    enabled: bool = False
    create_only: bool = False
    backup: bool = False
    allowed_extensions: list[str] = field(default_factory=lambda: sorted(_ALLOWED_WRITE_EXTENSIONS))
    max_bytes: int = _MAX_WRITE_BYTES


@dataclass(frozen=True)
class MountDefinition:
    name: str
    host_path: str
    virtual_path: str
    owner: str = "default"
    read_only: bool = True
    enabled: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    write_policy: WritePolicy = field(default_factory=WritePolicy)
    description: str = ""

    def public_dict(self, *, include_host_path: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_host_path:
            data.pop("host_path", None)
        return data


def _norm_owner(owner: str | None) -> str:
    return str(owner or "default").strip() or "default"


def _norm_virtual_path(raw: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.endswith("/") and value != "/":
        value = value.rstrip("/")
    return value


def is_sensitive_path(resolved: str) -> bool:
    parts = os.path.normpath(resolved).split(os.sep)
    if any(part in _SENSITIVE_BASENAMES for part in parts):
        return True
    filename = (parts[-1] if parts else "").lower()
    return any(fnmatch.fnmatch(filename, pattern.lower()) for pattern in _SENSITIVE_FILE_PATTERNS)


_is_sensitive_path = is_sensitive_path


def _is_filesystem_root(path: str) -> bool:
    parent = os.path.dirname(path)
    return bool(path) and parent == path


def _contains(root: str, candidate: str) -> bool:
    nroot = os.path.normcase(os.path.realpath(root))
    ncandidate = os.path.normcase(os.path.realpath(candidate))
    if ncandidate == nroot:
        return True
    try:
        return os.path.commonpath([ncandidate, nroot]) == nroot
    except ValueError:
        return False


def _is_windows_device_path(path: str) -> bool:
    value = str(path or "")
    return value.startswith("\\\\?\\") or value.startswith("\\\\.\\")


def _is_unc_path(path: str) -> bool:
    value = str(path or "")
    return value.startswith("\\\\") and not _is_windows_device_path(value)


def _parse_write_policy(raw: dict[str, Any]) -> WritePolicy:
    raw_policy = raw.get("write_policy") or {}
    if not isinstance(raw_policy, dict):
        raw_policy = {}
    extensions = raw_policy.get("allowed_extensions")
    if not isinstance(extensions, list) or not extensions:
        extensions = sorted(_ALLOWED_WRITE_EXTENSIONS)
    normalized_exts = []
    for ext in extensions:
        value = str(ext or "").strip().lower()
        if not value:
            continue
        normalized_exts.append(value if value.startswith(".") else f".{value}")
    try:
        max_bytes = int(raw_policy.get("max_bytes") or _MAX_WRITE_BYTES)
    except (TypeError, ValueError):
        max_bytes = _MAX_WRITE_BYTES
    return WritePolicy(
        enabled=bool(raw_policy.get("enabled", False)),
        create_only=bool(raw_policy.get("create_only", False)),
        backup=bool(raw_policy.get("backup", False)),
        allowed_extensions=sorted(set(normalized_exts or _ALLOWED_WRITE_EXTENSIONS)),
        max_bytes=max(1, min(max_bytes, _MAX_WRITE_BYTES)),
    )


def _mount_from_raw(raw: dict[str, Any]) -> MountDefinition:
    allowed_tools = raw.get("allowed_tools") or []
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    return MountDefinition(
        name=str(raw.get("name") or "").strip(),
        host_path=str(raw.get("host_path") or "").strip(),
        virtual_path=_norm_virtual_path(raw.get("virtual_path") or ""),
        owner=_norm_owner(raw.get("owner")),
        read_only=bool(raw.get("read_only", True)),
        enabled=bool(raw.get("enabled", True)),
        allowed_tools=[str(item).strip() for item in allowed_tools if str(item).strip()],
        write_policy=_parse_write_policy(raw),
        description=str(raw.get("description") or "").strip(),
    )


def load_mounts() -> list[MountDefinition]:
    if not os.path.exists(MOUNTS_FILE):
        return []
    with open(MOUNTS_FILE, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_mounts = payload.get("mounts") if isinstance(payload, dict) else payload
    if not isinstance(raw_mounts, list):
        return []
    mounts: list[MountDefinition] = []
    for raw in raw_mounts:
        if isinstance(raw, dict):
            mounts.append(_mount_from_raw(raw))
    return mounts


def save_mounts(mounts: Iterable[MountDefinition]) -> None:
    os.makedirs(os.path.dirname(MOUNTS_FILE), exist_ok=True)
    payload = {"mounts": [mount.public_dict(include_host_path=True) for mount in mounts]}
    tmp = f"{MOUNTS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, MOUNTS_FILE)


def validate_mount_definition(raw: dict[str, Any]) -> MountDefinition:
    mount = _mount_from_raw(raw)
    if not _NAME_RE.fullmatch(mount.name):
        raise ValueError("mount name must use 1-64 letters, digits, _ or -")
    if not mount.virtual_path.startswith("/mnt/"):
        raise ValueError("virtual_path must start with /mnt/")
    virtual_parts = [part for part in mount.virtual_path.split("/") if part]
    if any(part in (".", "..") for part in virtual_parts):
        raise ValueError("virtual_path must not contain . or .. segments")
    if mount.virtual_path == "/mnt":
        raise ValueError("virtual_path must include a mount name below /mnt")
    host = os.path.realpath(os.path.expanduser(mount.host_path))
    if not os.path.isdir(host):
        raise ValueError("host_path must be an existing directory")
    if _is_filesystem_root(host):
        raise ValueError("host_path must not be a filesystem root")
    if _is_sensitive_path(host):
        raise ValueError("host_path is sensitive and cannot be mounted")
    write_enabled = bool(mount.write_policy.enabled)
    if write_enabled:
        if mount.read_only:
            raise ValueError("write_policy.enabled requires read_only=false")
        if mount.owner == "*":
            raise ValueError("global mounts (owner='*') must be read-only")
        if not (_WRITE_TOOLS & set(mount.allowed_tools)):
            raise ValueError("writable mounts must explicitly allow write_file and/or edit_file")
        if _contains(DATA_DIR, host) or _contains(host, DATA_DIR):
            raise ValueError("writable mounts cannot target or contain Odysseus data")
        if _contains(BASE_DIR, host):
            raise ValueError("writable mounts cannot target the Odysseus application directory")
        if _is_unc_path(mount.host_path) or _is_windows_device_path(mount.host_path):
            raise ValueError("UNC and device paths are read-only unless explicitly supported later")
        home = os.path.realpath(os.path.expanduser("~"))
        if host == home:
            raise ValueError("writable mounts cannot target the home directory root")
        parts = {part.lower() for part in os.path.normpath(host).split(os.sep)}
        dangerous_parts = {part.lower() for part in _DANGEROUS_WRITE_COMPONENTS}
        if parts & dangerous_parts:
            raise ValueError("host_path contains a directory that is unsafe for writable mounts")
    return MountDefinition(
        name=mount.name,
        host_path=host,
        virtual_path=mount.virtual_path,
        owner=mount.owner,
        read_only=mount.read_only,
        enabled=mount.enabled,
        allowed_tools=mount.allowed_tools,
        write_policy=mount.write_policy,
        description=mount.description,
    )


def list_mounts_for_owner(owner: str | None, *, include_disabled: bool = False) -> list[MountDefinition]:
    effective_owner = _norm_owner(owner)
    mounts = []
    for mount in load_mounts():
        if mount.owner not in (effective_owner, "*"):
            continue
        if not include_disabled and not mount.enabled:
            continue
        mounts.append(mount)
    return mounts


def list_all_mounts() -> list[MountDefinition]:
    return load_mounts()


def upsert_mount(raw: dict[str, Any]) -> MountDefinition:
    mount = validate_mount_definition(raw)
    mounts = load_mounts()
    kept = [item for item in mounts if not (item.owner == mount.owner and item.name == mount.name)]
    kept.append(mount)
    kept.sort(key=lambda item: (item.owner, item.name))
    save_mounts(kept)
    return mount


def delete_mount(owner: str | None, name: str) -> bool:
    effective_owner = _norm_owner(owner)
    mounts = load_mounts()
    kept = [item for item in mounts if not (item.owner == effective_owner and item.name == name)]
    if len(kept) == len(mounts):
        return False
    save_mounts(kept)
    return True
