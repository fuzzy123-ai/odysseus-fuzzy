"""Background-removal helpers for gallery image routes."""

from __future__ import annotations

import base64
import binascii

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.image_tools_worker import (
    ImageToolsWorkerErrorCode,
    ImageToolsWorkerResult,
    ImageToolsWorkerSettings,
)


def _decode_image_payload(value: str | None, *, field_name: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, f"No {field_name} provided")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, f"Invalid {field_name}")


def _remove_bg_error_status(code: str) -> int:
    mapping = {
        ImageToolsWorkerErrorCode.NOT_CONFIGURED.value: 503,
        ImageToolsWorkerErrorCode.DEPENDENCY_MISSING.value: 503,
        ImageToolsWorkerErrorCode.WORKER_UNREACHABLE.value: 502,
        ImageToolsWorkerErrorCode.TIMEOUT.value: 504,
        ImageToolsWorkerErrorCode.INVALID_IMAGE.value: 400,
        ImageToolsWorkerErrorCode.PAYLOAD_TOO_LARGE.value: 413,
        ImageToolsWorkerErrorCode.PERMISSION_DENIED.value: 403,
    }
    return mapping.get(code, 502)


def _remove_bg_error_response(result: ImageToolsWorkerResult) -> JSONResponse:
    error = result.error
    code = error.code.value if error else "worker_unreachable"
    message = error.message if error else "Background removal failed."
    return JSONResponse(
        status_code=_remove_bg_error_status(code),
        content={"error": message, "error_code": code},
    )


def _should_use_legacy_remove_bg_fallback(
    settings: ImageToolsWorkerSettings,
    result: ImageToolsWorkerResult | None = None,
) -> bool:
    if not settings.legacy_fallback:
        return False
    if result is None or result.error is None:
        return True
    return result.error.code in {
        ImageToolsWorkerErrorCode.NOT_CONFIGURED,
        ImageToolsWorkerErrorCode.WORKER_UNREACHABLE,
        ImageToolsWorkerErrorCode.TIMEOUT,
        ImageToolsWorkerErrorCode.DEPENDENCY_MISSING,
    }


def _legacy_remove_background_response(image_bytes: bytes, hint_bytes: bytes | None = None) -> dict[str, str]:
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    hint = None
    bbox = None
    if hint_bytes:
        try:
            hint = Image.open(io.BytesIO(hint_bytes)).convert("L")
            if hint.size != img.size:
                hint = hint.resize(img.size, Image.NEAREST)
            bbox = hint.getbbox()
            if bbox:
                pad = 8
                bbox = (
                    max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                    min(width, bbox[2] + pad), min(height, bbox[3] + pad),
                )
        except Exception:
            hint = None
            bbox = None

    crop = img.crop(bbox) if bbox else img
    try:
        from rembg import remove

        cut = remove(crop)
    except ImportError:
        try:
            from transformers import pipeline

            pipe = pipeline("image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True)
            mask_img = pipe(crop, return_mask=True).convert("L")
            tmp = crop.copy()
            tmp.putalpha(mask_img)
            cut = tmp
        except Exception:
            return {
                "error": "No background removal model available. Install rembg: pip install rembg",
                "error_code": ImageToolsWorkerErrorCode.DEPENDENCY_MISSING.value,
            }

    if bbox:
        result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        result.paste(cut, (bbox[0], bbox[1]), cut)
    else:
        result = cut.convert("RGBA")

    if hint is not None:
        r, g, b, a = result.split()
        from PIL import ImageChops

        a = ImageChops.multiply(a, hint)
        result = Image.merge("RGBA", (r, g, b, a))

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return {"image": base64.b64encode(buf.getvalue()).decode()}
