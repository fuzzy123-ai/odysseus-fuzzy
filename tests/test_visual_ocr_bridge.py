import pytest

from src.visual_ocr_bridge import VisualOcrBridgeError, build_visual_ocr_request


def test_visual_ocr_request_prefers_local_engine():
    request = build_visual_ocr_request(
        {"artifact_ref": "reports/no_gpu/page.png", "image_hash": "sha256:" + "a" * 64},
        language_hint="deu+eng",
    )

    assert request["engine_preference"] == "local_ocr_first"
    assert request["language_hint"] == "deu+eng"
    assert request["raw_content_visible"] is False


def test_visual_ocr_request_rejects_absolute_artifact():
    with pytest.raises(VisualOcrBridgeError):
        build_visual_ocr_request({"artifact_ref": "/tmp/page.png"})
