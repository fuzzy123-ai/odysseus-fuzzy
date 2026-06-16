"""Isolated client contract for the image tools worker."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
import json
import os
import socket
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request


_DEFAULT_TIMEOUT_SEC = 15.0
_DEFAULT_MAX_MB = 10.0
_MAX_MODE_LENGTH = 32
_MAX_ERROR_MESSAGE = 200
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ImageToolsWorkerMode(StrEnum):
    DISABLED = "disabled"
    LOCAL_VENV = "local-venv"
    DOCKER = "docker"
    REMOTE = "remote"


class ImageToolsWorkerErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    WORKER_UNREACHABLE = "worker_unreachable"
    TIMEOUT = "timeout"
    INVALID_IMAGE = "invalid_image"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    DEPENDENCY_MISSING = "dependency_missing"
    PERMISSION_DENIED = "permission_denied"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerSettings:
    mode: ImageToolsWorkerMode
    url: str
    timeout_sec: float
    max_mb: float
    legacy_fallback: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ImageToolsWorkerSettings":
        values = env or os.environ
        return cls(
            mode=_parse_mode(values.get("IMAGE_TOOLS_WORKER_MODE", ImageToolsWorkerMode.DISABLED.value)),
            url=str(values.get("IMAGE_TOOLS_WORKER_URL", "") or "").strip(),
            timeout_sec=_parse_positive_float(
                values.get("IMAGE_TOOLS_WORKER_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC),
                default=_DEFAULT_TIMEOUT_SEC,
            ),
            max_mb=_parse_positive_float(
                values.get("IMAGE_TOOLS_WORKER_MAX_MB", _DEFAULT_MAX_MB),
                default=_DEFAULT_MAX_MB,
            ),
            legacy_fallback=_parse_bool(values.get("IMAGE_TOOLS_WORKER_LEGACY_FALLBACK", "")),
        )

    @property
    def max_bytes(self) -> int:
        return int(self.max_mb * 1024 * 1024)

    @property
    def configured(self) -> bool:
        if self.mode is ImageToolsWorkerMode.DISABLED:
            return False
        return bool(self.url)


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerError:
    code: ImageToolsWorkerErrorCode
    message: str
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerResult:
    ok: bool
    image_bytes: bytes = b""
    error: ImageToolsWorkerError | None = None

    @property
    def error_code(self) -> str | None:
        return self.error.code.value if self.error else None


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerRequest:
    url: str
    method: str
    headers: Mapping[str, str]
    body: bytes
    timeout_sec: float


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


Transport = Callable[[ImageToolsWorkerRequest], ImageToolsWorkerResponse]


def _parse_mode(value: Any) -> ImageToolsWorkerMode:
    normalized = str(value or "").strip().lower()
    if len(normalized) > _MAX_MODE_LENGTH:
        normalized = normalized[:_MAX_MODE_LENGTH]
    try:
        return ImageToolsWorkerMode(normalized or ImageToolsWorkerMode.DISABLED.value)
    except ValueError:
        return ImageToolsWorkerMode.DISABLED


def _parse_positive_float(value: Any, *, default: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    if normalized <= 0:
        return default
    return normalized


def _parse_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _trim_message(value: Any, fallback: str) -> str:
    message = " ".join(str(value or "").split())
    if not message:
        return fallback
    if len(message) > _MAX_ERROR_MESSAGE:
        return message[: _MAX_ERROR_MESSAGE - 3] + "..."
    return message


def _json_loads(data: bytes) -> dict[str, Any]:
    loaded = json.loads(data.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _to_error(code: ImageToolsWorkerErrorCode, message: str, status_code: int | None = None) -> ImageToolsWorkerResult:
    return ImageToolsWorkerResult(
        ok=False,
        error=ImageToolsWorkerError(code=code, message=_trim_message(message, code.value), status_code=status_code),
    )


def _default_transport(request: ImageToolsWorkerRequest) -> ImageToolsWorkerResponse:
    raw_request = urllib.request.Request(
        request.url,
        data=request.body,
        headers=dict(request.headers),
        method=request.method,
    )
    with urllib.request.urlopen(raw_request, timeout=request.timeout_sec) as response:
        return ImageToolsWorkerResponse(
            status_code=int(getattr(response, "status", response.getcode())),
            body=response.read(),
            headers=dict(response.headers.items()),
        )


class ImageToolsWorkerClient:
    """Small client for the isolated image tools worker."""

    def __init__(
        self,
        settings: ImageToolsWorkerSettings,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or _default_transport

    def remove_background(
        self,
        image_bytes: bytes,
        hint_mask_bytes: bytes | None = None,
    ) -> ImageToolsWorkerResult:
        if not self.settings.configured:
            return _to_error(
                ImageToolsWorkerErrorCode.NOT_CONFIGURED,
                "Background removal worker is not configured.",
            )
        if not isinstance(image_bytes, (bytes, bytearray)) or not bytes(image_bytes):
            return _to_error(
                ImageToolsWorkerErrorCode.INVALID_IMAGE,
                "Image payload must contain bytes.",
            )

        payload_size = len(image_bytes) + len(hint_mask_bytes or b"")
        if payload_size > self.settings.max_bytes:
            return _to_error(
                ImageToolsWorkerErrorCode.PAYLOAD_TOO_LARGE,
                "Image payload exceeds the configured worker size limit.",
            )

        payload: dict[str, Any] = {
            "operation": "remove_background",
            "image_base64": base64.b64encode(bytes(image_bytes)).decode("ascii"),
            "response_format": "png",
            "legacy_fallback": self.settings.legacy_fallback,
        }
        if hint_mask_bytes is not None:
            payload["hint_mask_base64"] = base64.b64encode(bytes(hint_mask_bytes)).decode("ascii")

        request = ImageToolsWorkerRequest(
            url=self.settings.url,
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
            timeout_sec=self.settings.timeout_sec,
        )

        try:
            response = self._transport(request)
        except TimeoutError:
            return _to_error(ImageToolsWorkerErrorCode.TIMEOUT, "Image tools worker request timed out.")
        except urllib.error.HTTPError as exc:
            return self._handle_http_error(exc)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError | socket.timeout):
                return _to_error(ImageToolsWorkerErrorCode.TIMEOUT, "Image tools worker request timed out.")
            return _to_error(
                ImageToolsWorkerErrorCode.WORKER_UNREACHABLE,
                _trim_message(getattr(exc, "reason", exc), "Image tools worker is unreachable."),
            )
        except OSError as exc:
            if isinstance(exc, socket.timeout):
                return _to_error(ImageToolsWorkerErrorCode.TIMEOUT, "Image tools worker request timed out.")
            return _to_error(
                ImageToolsWorkerErrorCode.WORKER_UNREACHABLE,
                _trim_message(exc, "Image tools worker is unreachable."),
            )

        return self._parse_response(response)

    def _handle_http_error(self, exc: urllib.error.HTTPError) -> ImageToolsWorkerResult:
        status_code = int(getattr(exc, "code", 0) or 0)
        body = b""
        try:
            body = exc.read()
        except Exception:
            body = b""
        return self._parse_response(
            ImageToolsWorkerResponse(status_code=status_code, body=body, headers={})
        )

    def _parse_response(self, response: ImageToolsWorkerResponse) -> ImageToolsWorkerResult:
        try:
            payload = _json_loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _to_error(
                ImageToolsWorkerErrorCode.INVALID_RESPONSE,
                "Image tools worker returned an invalid response.",
                response.status_code,
            )

        if response.status_code >= 400:
            code = self._normalize_error_code(payload.get("error_code") or payload.get("code"))
            message = _trim_message(
                payload.get("message") or payload.get("detail"),
                "Image tools worker request failed.",
            )
            return _to_error(code, message, response.status_code)

        image_b64 = payload.get("image_base64")
        if not isinstance(image_b64, str) or not image_b64.strip():
            return _to_error(
                ImageToolsWorkerErrorCode.INVALID_RESPONSE,
                "Image tools worker did not return PNG bytes.",
                response.status_code,
            )
        try:
            image_bytes = base64.b64decode(image_b64, validate=True)
        except (ValueError, TypeError):
            return _to_error(
                ImageToolsWorkerErrorCode.INVALID_RESPONSE,
                "Image tools worker returned invalid base64 output.",
                response.status_code,
            )
        if not image_bytes.startswith(_PNG_SIGNATURE):
            return _to_error(
                ImageToolsWorkerErrorCode.INVALID_RESPONSE,
                "Image tools worker returned non-PNG output.",
                response.status_code,
            )
        return ImageToolsWorkerResult(ok=True, image_bytes=image_bytes)

    @staticmethod
    def _normalize_error_code(value: Any) -> ImageToolsWorkerErrorCode:
        normalized = str(value or "").strip().lower().replace("-", "_")
        for code in ImageToolsWorkerErrorCode:
            if code.value == normalized.replace("_", "-"):
                return code
        match normalized:
            case "not_configured":
                return ImageToolsWorkerErrorCode.NOT_CONFIGURED
            case "worker_unreachable":
                return ImageToolsWorkerErrorCode.WORKER_UNREACHABLE
            case "timeout":
                return ImageToolsWorkerErrorCode.TIMEOUT
            case "invalid_image":
                return ImageToolsWorkerErrorCode.INVALID_IMAGE
            case "payload_too_large":
                return ImageToolsWorkerErrorCode.PAYLOAD_TOO_LARGE
            case "dependency_missing":
                return ImageToolsWorkerErrorCode.DEPENDENCY_MISSING
            case "permission_denied":
                return ImageToolsWorkerErrorCode.PERMISSION_DENIED
            case _:
                return ImageToolsWorkerErrorCode.INVALID_RESPONSE
