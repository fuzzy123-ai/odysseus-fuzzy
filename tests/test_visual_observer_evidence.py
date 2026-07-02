import pytest

from src.visual_observer_evidence import VisualObserverEvidenceError, build_screenshot_evidence


def test_screenshot_evidence_metadata_is_bounded_and_redacted():
    payload = build_screenshot_evidence(
        artifact_ref="reports/no_gpu/page.png",
        width=1280,
        height=720,
        viewport={"width": 1280, "height": 720},
        image_hash="a" * 64,
        selector_focus="main",
    )

    assert payload["image_hash"].startswith("sha256:")
    assert payload["raw_content_visible"] is False


def test_screenshot_evidence_rejects_host_paths():
    with pytest.raises(VisualObserverEvidenceError):
        build_screenshot_evidence(artifact_ref="C:/Users/private/page.png", width=1, height=1)
