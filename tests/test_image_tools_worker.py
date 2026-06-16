import base64
import json
import urllib.error

from src.image_tools_worker import (
    ImageToolsWorkerClient,
    ImageToolsWorkerErrorCode,
    ImageToolsWorkerResponse,
    ImageToolsWorkerSettings,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\nmock-png"


def _settings(**overrides) -> ImageToolsWorkerSettings:
    payload = {
        "mode": "docker",
        "url": "http://127.0.0.1:8123/remove-background",
        "timeout_sec": 8.0,
        "max_mb": 1.0,
        "legacy_fallback": False,
    }
    payload.update(overrides)
    return ImageToolsWorkerSettings.from_env(
        {
            "IMAGE_TOOLS_WORKER_MODE": payload["mode"],
            "IMAGE_TOOLS_WORKER_URL": payload["url"],
            "IMAGE_TOOLS_WORKER_TIMEOUT_SEC": str(payload["timeout_sec"]),
            "IMAGE_TOOLS_WORKER_MAX_MB": str(payload["max_mb"]),
            "IMAGE_TOOLS_WORKER_LEGACY_FALLBACK": "true" if payload["legacy_fallback"] else "false",
        }
    )


def _response(payload: dict[str, object], *, status_code: int = 200) -> ImageToolsWorkerResponse:
    return ImageToolsWorkerResponse(
        status_code=status_code,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def test_disabled_or_not_configured_returns_structured_error() -> None:
    result = ImageToolsWorkerClient(_settings(mode="disabled", url="")).remove_background(b"img")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.NOT_CONFIGURED

    result = ImageToolsWorkerClient(_settings(url="")).remove_background(b"img")
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.NOT_CONFIGURED


def test_payload_too_large_is_rejected_before_transport() -> None:
    calls: list[object] = []

    def transport(_request):
        calls.append(object())
        return _response({"image_base64": base64.b64encode(PNG_BYTES).decode("ascii")})

    client = ImageToolsWorkerClient(_settings(max_mb=0.000001), transport=transport)
    result = client.remove_background(b"x" * 32)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.PAYLOAD_TOO_LARGE
    assert calls == []


def test_unreachable_transport_error_maps_cleanly() -> None:
    def transport(_request):
        raise urllib.error.URLError("connection refused")

    result = ImageToolsWorkerClient(_settings(), transport=transport).remove_background(b"img")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.WORKER_UNREACHABLE


def test_timeout_maps_cleanly() -> None:
    def transport(_request):
        raise TimeoutError("timed out")

    result = ImageToolsWorkerClient(_settings(), transport=transport).remove_background(b"img")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.TIMEOUT


def test_invalid_response_is_reported() -> None:
    def transport(_request):
        return ImageToolsWorkerResponse(status_code=200, body=b"not-json", headers={})

    result = ImageToolsWorkerClient(_settings(), transport=transport).remove_background(b"img")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.INVALID_RESPONSE


def test_success_returns_png_bytes_from_mocked_base64() -> None:
    def transport(_request):
        return _response({"image_base64": base64.b64encode(PNG_BYTES).decode("ascii")})

    result = ImageToolsWorkerClient(_settings(), transport=transport).remove_background(b"img")

    assert result.ok is True
    assert result.error is None
    assert result.image_bytes == PNG_BYTES


def test_hint_mask_payload_is_forwarded() -> None:
    captured = {}

    def transport(request):
        captured["payload"] = json.loads(request.body.decode("utf-8"))
        return _response({"image_base64": base64.b64encode(PNG_BYTES).decode("ascii")})

    result = ImageToolsWorkerClient(_settings(legacy_fallback=True), transport=transport).remove_background(
        b"image-bytes",
        hint_mask_bytes=b"mask-bytes",
    )

    assert result.ok is True
    payload = captured["payload"]
    assert payload["hint_mask_base64"] == base64.b64encode(b"mask-bytes").decode("ascii")
    assert payload["legacy_fallback"] is True


def test_invalid_image_error_from_worker_is_preserved() -> None:
    def transport(_request):
        return _response(
            {"error_code": "invalid_image", "message": "worker rejected image"},
            status_code=422,
        )

    result = ImageToolsWorkerClient(_settings(), transport=transport).remove_background(b"img")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ImageToolsWorkerErrorCode.INVALID_IMAGE
    assert result.error.message == "worker rejected image"
