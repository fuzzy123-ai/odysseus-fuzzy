"""Minimal isolated image tools worker for background removal."""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8123
DEFAULT_MAX_MB = 10.0
MAX_MESSAGE = 200
_SESSION_LOCK = threading.Lock()
_REMBG_SESSION = None


def _env_text(name: str, default: str) -> str:
    value = str(os.environ.get(name, default) or "").strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def worker_host() -> str:
    return _env_text("IMAGE_TOOLS_WORKER_HOST", DEFAULT_HOST)


def worker_port() -> int:
    try:
        value = int(os.environ.get("IMAGE_TOOLS_WORKER_PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return value if value > 0 else DEFAULT_PORT


def max_payload_bytes() -> int:
    return int(_env_float("IMAGE_TOOLS_WORKER_MAX_MB", DEFAULT_MAX_MB) * 1024 * 1024)


def _trim_message(message: Any, fallback: str) -> str:
    text = " ".join(str(message or "").split())
    if not text:
        return fallback
    if len(text) > MAX_MESSAGE:
        return text[: MAX_MESSAGE - 3] + "..."
    return text


def _json_error(error_code: str, message: str, *, status: HTTPStatus) -> tuple[int, bytes]:
    payload = {
        "error_code": error_code,
        "message": _trim_message(message, error_code),
    }
    return status.value, json.dumps(payload).encode("utf-8")


def _decode_base64_image(field_name: str, value: Any) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{field_name} must be valid base64") from exc


def _load_rembg():
    try:
        from rembg import new_session, remove  # type: ignore
    except ImportError as exc:
        raise RuntimeError("rembg is not installed in the image tools worker environment.") from exc
    return new_session, remove


def _get_session():
    global _REMBG_SESSION
    if _REMBG_SESSION is not None:
        return _REMBG_SESSION
    with _SESSION_LOCK:
        if _REMBG_SESSION is None:
            new_session, _ = _load_rembg()
            _REMBG_SESSION = new_session()
    return _REMBG_SESSION


def remove_background_bytes(image_bytes: bytes) -> bytes:
    if not isinstance(image_bytes, (bytes, bytearray)) or not bytes(image_bytes):
        raise ValueError("image bytes are required")
    _, remove = _load_rembg()
    session = _get_session()
    try:
        output = remove(bytes(image_bytes), session=session)
    except Exception as exc:  # pragma: no cover - exact rembg failures are dependency-specific
        raise ValueError(f"remove_background failed: {exc}") from exc
    if isinstance(output, memoryview):
        output = output.tobytes()
    if not isinstance(output, (bytes, bytearray)):
        raise ValueError("worker returned a non-bytes image")
    output_bytes = bytes(output)
    if not output_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("worker returned non-PNG output")
    return output_bytes


def build_remove_background_response(payload: dict[str, Any]) -> tuple[int, bytes]:
    try:
        image_bytes = _decode_base64_image("image_base64", payload.get("image_base64"))
        hint_mask_bytes = None
        if payload.get("hint_mask_base64") is not None:
            hint_mask_bytes = _decode_base64_image("hint_mask_base64", payload.get("hint_mask_base64"))
    except ValueError as exc:
        return _json_error("invalid_image", str(exc), status=HTTPStatus.BAD_REQUEST)

    if len(image_bytes) + len(hint_mask_bytes or b"") > max_payload_bytes():
        return _json_error(
            "payload_too_large",
            "Image payload exceeds the configured worker size limit.",
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    try:
        png_bytes = remove_background_bytes(image_bytes)
    except RuntimeError as exc:
        return _json_error("dependency_missing", str(exc), status=HTTPStatus.SERVICE_UNAVAILABLE)
    except ValueError as exc:
        return _json_error("invalid_image", str(exc), status=HTTPStatus.BAD_REQUEST)

    response = {
        "image_base64": base64.b64encode(png_bytes).decode("ascii"),
        "mime_type": "image/png",
        "hint_mask_accepted": hint_mask_bytes is not None,
        "hint_mask_applied": False,
    }
    return HTTPStatus.OK.value, json.dumps(response).encode("utf-8")


class ImageToolsWorkerHandler(BaseHTTPRequestHandler):
    server_version = "OdysseusImageToolsWorker/0.1"

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/remove-background":
            self._write_json(*_json_error("invalid_route", "Route not found.", status=HTTPStatus.NOT_FOUND))
            return

        content_length = self.headers.get("Content-Length", "")
        try:
            length = int(content_length)
        except (TypeError, ValueError):
            self._write_json(*_json_error("invalid_image", "Content-Length is required.", status=HTTPStatus.BAD_REQUEST))
            return
        if length <= 0:
            self._write_json(*_json_error("invalid_image", "Request body is required.", status=HTTPStatus.BAD_REQUEST))
            return
        if length > max_payload_bytes() * 2:
            self._write_json(
                *_json_error(
                    "payload_too_large",
                    "Request body exceeds the configured worker size limit.",
                    status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
            )
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(*_json_error("invalid_image", "Request body must be valid JSON.", status=HTTPStatus.BAD_REQUEST))
            return
        if not isinstance(payload, dict):
            self._write_json(*_json_error("invalid_image", "JSON body must be an object.", status=HTTPStatus.BAD_REQUEST))
            return
        self._write_json(*build_remove_background_response(payload))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = json.dumps(
                {
                    "ok": True,
                    "service": "image-tools-worker",
                    "capabilities": ["remove_background"],
                }
            ).encode("utf-8")
            self._write_json(HTTPStatus.OK.value, body)
            return
        self._write_json(*_json_error("invalid_route", "Route not found.", status=HTTPStatus.NOT_FOUND))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, status_code: int, body: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run() -> None:
    server = ThreadingHTTPServer((worker_host(), worker_port()), ImageToolsWorkerHandler)
    print(f"[image-tools-worker] listening on http://{worker_host()}:{worker_port()}")
    server.serve_forever()


if __name__ == "__main__":
    run()
