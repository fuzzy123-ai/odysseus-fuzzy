"""Small WebDAV client for review-gated Nextcloud file writes."""

from __future__ import annotations

from pathlib import Path
import os
import posixpath
import re
from typing import Any, Mapping
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx


_UNSAFE_PATH_CHARS = set('<>:"|?*')
_DAV = "{DAV:}"
_DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class NextcloudWebDAVClientError(RuntimeError):
    """Raised when a Nextcloud WebDAV operation fails."""


class NextcloudWebDAVClient:
    """Minimal WebDAV adapter matching the Universal Inbox transfer client."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        app_password: str,
        root: str = "",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.username = str(username or "").strip()
        self.root = _normalize_relative_path(root, allow_empty=True)
        if not self.base_url:
            raise ValueError("base_url must not be empty")
        if not self.username:
            raise ValueError("username must not be empty")
        if not str(app_password or ""):
            raise ValueError("app_password must not be empty")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            auth=httpx.BasicAuth(self.username, str(app_password)),
            timeout=timeout,
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "NextcloudWebDAVClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def stat(self, relative_path: str) -> Mapping[str, Any] | None:
        path = self._path(relative_path)
        response = self._client.request(
            "PROPFIND",
            self._url(path),
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            content=(
                '<?xml version="1.0" encoding="utf-8"?>'
                "<d:propfind xmlns:d=\"DAV:\"><d:prop>"
                "<d:getcontentlength/><d:getetag/><d:resourcetype/>"
                "</d:prop></d:propfind>"
            ),
        )
        if response.status_code == 404:
            return None
        _raise_for_status(response, expected={200, 207})
        return _parse_propfind(response.text, path)

    def get_file_bytes(
        self,
        relative_path: str,
        *,
        max_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> bytes:
        path = self._path(relative_path)
        metadata = self.stat(relative_path)
        if metadata is None:
            raise NextcloudWebDAVClientError("WebDAV file not found")
        if bool(metadata.get("is_collection")):
            raise NextcloudWebDAVClientError("WebDAV path is a folder")
        declared = int(metadata.get("size_bytes") or 0)
        if max_bytes > 0 and declared > max_bytes:
            raise NextcloudWebDAVClientError("WebDAV file exceeds download limit")
        response = self._client.get(self._url(path))
        if response.status_code == 404:
            raise NextcloudWebDAVClientError("WebDAV file not found")
        _raise_for_status(response, expected={200})
        payload = response.content
        if max_bytes > 0 and len(payload) > max_bytes:
            raise NextcloudWebDAVClientError("WebDAV file exceeds download limit")
        return payload

    def put_file(self, source_path: Path, relative_path: str) -> Mapping[str, Any]:
        source = Path(source_path)
        if not source.is_file():
            raise NextcloudWebDAVClientError("source file does not exist")
        path = self._path(relative_path)
        self._ensure_parent_dirs(path)
        with source.open("rb") as handle:
            response = self._client.put(self._url(path), content=handle)
        _raise_for_status(response, expected={200, 201, 204})
        return {
            "size_bytes": source.stat().st_size,
            "etag": _clean_etag(response.headers.get("ETag", "")),
        }

    def put_text(self, relative_path: str, text: str) -> Mapping[str, Any]:
        payload = str(text or "").encode("utf-8")
        path = self._path(relative_path)
        self._ensure_parent_dirs(path)
        response = self._client.put(
            self._url(path),
            content=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        _raise_for_status(response, expected={200, 201, 204})
        return {
            "size_bytes": len(payload),
            "etag": _clean_etag(response.headers.get("ETag", "")),
        }

    def put_bytes_create_only(
        self,
        relative_path: str,
        content: bytes,
        *,
        max_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES,
    ) -> Mapping[str, Any]:
        """Create an in-memory payload without exposing it in errors.

        This Forge-only primitive can never replace remote evidence.  The
        older ``put_file``/``put_text`` methods retain their existing path and
        overwrite semantics for their review-gated callers.
        """

        if not isinstance(content, bytes):
            raise ValueError("content must be bytes")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if len(content) > max_bytes:
            raise NextcloudWebDAVClientError("WebDAV payload exceeds upload limit")
        path = self._strict_path(relative_path)
        self._ensure_parent_dirs(path)
        headers = {
            "Content-Type": "application/octet-stream",
            "If-None-Match": "*",
        }
        response = self._client.put(self._url(path), content=content, headers=headers)
        _raise_for_status(response, expected={201})
        return {
            "size_bytes": len(content),
            "etag": _clean_etag(response.headers.get("ETag", "")),
        }

    def move_create_only(
        self,
        source_relative: str,
        destination_relative: str,
    ) -> Mapping[str, Any]:
        """Promote a path with WebDAV MOVE and fail closed on overwrite.

        Automatic overwrite is intentionally unsupported.  A future live
        policy gate may define stable-pointer replacement separately; this
        primitive only supports immutable create-only promotion.
        """

        source = self._strict_path(source_relative)
        destination = self._strict_path(destination_relative)
        if source == destination:
            raise ValueError("source and destination must differ")
        self._ensure_parent_dirs(destination)
        response = self._client.request(
            "MOVE",
            self._url(source),
            headers={
                "Destination": self._url(destination),
                "Overwrite": "F",
            },
        )
        _raise_for_status(response, expected={201})
        return {
            "created": True,
            "etag": _clean_etag(response.headers.get("ETag", "")),
        }

    def _ensure_parent_dirs(self, relative_path: str) -> None:
        parent = posixpath.dirname(relative_path)
        if not parent:
            return
        current = ""
        for segment in parent.split("/"):
            current = segment if not current else f"{current}/{segment}"
            response = self._client.request("MKCOL", self._url(current))
            if response.status_code in {201, 405}:
                continue
            if response.status_code == 409:
                raise NextcloudWebDAVClientError("parent folder is missing or blocked")
            _raise_for_status(response, expected={200, 201, 204, 405})

    def _path(self, relative_path: str) -> str:
        child = _normalize_relative_path(relative_path)
        return f"{self.root}/{child}" if self.root else child

    def _strict_path(self, relative_path: str) -> str:
        child = _normalize_strict_relative_path(relative_path)
        return f"{self.root}/{child}" if self.root else child

    def _url(self, relative_path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in relative_path.split("/") if part)
        return f"{self.base_url}/{encoded}"


def build_nextcloud_webdav_client_from_env() -> NextcloudWebDAVClient:
    """Create a WebDAV client from runtime env without exposing secret values.

    This helper is intentionally narrow and performs no network IO on its own.
    The caller still needs an explicit review/operator gate before any write.
    """

    base_url = _first_env("NEXTCLOUD_WEBDAV_BASE_URL", "NEXTCLOUD_WEBDAV_URL")
    username = _first_env("NEXTCLOUD_WEBDAV_USERNAME", "NEXTCLOUD_USERNAME")
    app_password = _first_env(
        "NEXTCLOUD_WEBDAV_APP_PASSWORD",
        "NEXTCLOUD_WEBDAV_PASSWORD",
        "NEXTCLOUD_WEBDAV_PASSWORT",
        "NEXTCLOUD_PASSWORD",
    )
    missing = []
    if not base_url:
        missing.append("NEXTCLOUD_WEBDAV_BASE_URL")
    if not username:
        missing.append("NEXTCLOUD_WEBDAV_USERNAME")
    if not app_password:
        missing.append("NEXTCLOUD_WEBDAV_APP_PASSWORD")
    if missing:
        raise NextcloudWebDAVClientError(
            "Nextcloud WebDAV runtime config missing: " + ",".join(missing)
        )
    return NextcloudWebDAVClient(
        base_url=base_url,
        username=username,
        app_password=app_password,
        root=os.getenv("NEXTCLOUD_WEBDAV_ROOT", ""),
    )


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _parse_propfind(text: str, relative_path: str) -> Mapping[str, Any]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise NextcloudWebDAVClientError("invalid WebDAV PROPFIND XML") from exc
    prop = root.find(f".//{_DAV}prop")
    if prop is None:
        return {"relative_path": relative_path, "size_bytes": 0, "etag": "", "is_collection": False}
    length = prop.findtext(f"{_DAV}getcontentlength") or "0"
    etag = prop.findtext(f"{_DAV}getetag") or ""
    resource_type = prop.find(f"{_DAV}resourcetype")
    is_collection = resource_type is not None and resource_type.find(f"{_DAV}collection") is not None
    try:
        size = int(length)
    except ValueError:
        size = 0
    return {
        "relative_path": relative_path,
        "size_bytes": size,
        "etag": _clean_etag(etag),
        "is_collection": is_collection,
    }


def _raise_for_status(response: httpx.Response, *, expected: set[int]) -> None:
    if response.status_code in expected:
        return
    raise NextcloudWebDAVClientError(f"WebDAV request failed: HTTP {response.status_code}")


def _clean_etag(value: Any) -> str:
    return str(value or "").strip().strip('"')[:160]


def _normalize_relative_path(value: Any, *, allow_empty: bool = False) -> str:
    raw_input = str(value or "").strip().replace("\\", "/")
    if raw_input.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw_input):
        raise ValueError("relative path must not be absolute")
    raw = raw_input.strip("/")
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("relative path must not be empty")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("relative path must not contain traversal")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise ValueError("relative path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise ValueError("relative path contains unsafe characters")
    return "/".join(parts)


def _normalize_strict_relative_path(value: Any) -> str:
    raw_input = str(value or "").strip()
    if "\\" in raw_input or "\x00" in raw_input:
        raise ValueError("relative path must use forward slashes")
    return _normalize_relative_path(raw_input)
