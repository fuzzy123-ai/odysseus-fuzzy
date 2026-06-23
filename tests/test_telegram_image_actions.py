from src.image_tools_worker import ImageToolsWorkerResult
from src.telegram_image_actions import (
    plan_telegram_image_action,
    run_telegram_image_action,
    select_telegram_photo_variant,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\ntelegram-image"


class FakeWorkerClient:
    def __init__(self):
        self.calls = []

    def remove_background(self, image_bytes, hint_mask_bytes=None):
        self.calls.append((image_bytes, hint_mask_bytes))
        return ImageToolsWorkerResult(ok=True, image_bytes=PNG_BYTES)


def _message(**overrides):
    payload = {
        "kind": "image",
        "chat_allowed": True,
        "media": {
            "type": "image",
            "file_handle": "image_file_abc",
            "file_unique_handle": "image_unique_def",
            "file_size": 128,
        },
    }
    payload.update(overrides)
    return payload


def test_select_telegram_photo_variant_picks_largest_file():
    selected = select_telegram_photo_variant(
        [
            {"file_id": "small", "file_size": 10, "width": 10, "height": 10},
            {"file_id": "large", "file_size": 20, "width": 10, "height": 10},
        ]
    )

    assert selected["file_id"] == "large"


def test_image_action_plan_is_disabled_by_default():
    plan = plan_telegram_image_action(_message(), enabled=False)

    assert plan.allowed is False
    assert plan.status == "disabled"
    assert plan.raw_identifiers_visible is False


def test_image_action_runs_with_injected_bytes_and_worker_client():
    worker = FakeWorkerClient()
    result = run_telegram_image_action(
        _message(),
        enabled=True,
        image_bytes_provider=lambda file_handle: b"source:" + file_handle.encode("ascii"),
        worker_client=worker,
    )

    assert result is not None
    assert result["plan"]["allowed"] is True
    assert result["worker"]["called"] is True
    assert result["worker"]["ok"] is True
    assert result["worker"]["output_image_present"] is True
    assert result["worker"]["raw_image_visible"] is False
    assert worker.calls[0][0] == b"source:image_file_abc"


def test_image_action_waits_for_injected_bytes_provider():
    result = run_telegram_image_action(_message(), enabled=True)

    assert result is not None
    assert result["worker"]["called"] is False
    assert result["worker"]["status"] == "waiting_image_bytes_provider"
