"""Small WebDAV client for review-gated Nextcloud file writes."""

from __future__ import annotations

from pathlib import Path
import posixpath
import re
from typing import Any, Mapping
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx


_UNSAFE_PATH_CHARS = set('<>:"|?*')
_DAV = "{DAV:}"


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

    def _url(self, relative_path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in relative_path.split("/") if part)
        return f"{self.base_url}/{encoded}"


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
