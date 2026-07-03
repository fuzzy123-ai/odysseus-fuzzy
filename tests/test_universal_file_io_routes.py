import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.universal_file_io_routes import setup_universal_file_io_routes


class _AuthManager:
    is_configured = True


def _app(*, user="alice", auth_configured=True) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager()
    app.state.auth_manager.is_configured = auth_configured

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_universal_file_io_routes())
    return app


def test_export_plan_is_redacted_and_does_not_execute_converter():
    body = {
        "request_text": "Mach aus meiner privaten Rechnung bitte ein PDF",
        "recent_source_ref": "C:/Users/name/private/invoice.docx",
        "source_name_or_extension": "C:/Users/name/private/invoice.docx",
    }

    response = TestClient(_app()).post("/api/universal-file-io/export-plan", json=body)
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.universal_file_io.export_plan_response.v1"
    assert payload["execution_performed"] is False
    assert payload["converter_execution_allowed"] is False
    assert payload["delivery_allowed"] is False
    assert payload["live_write_allowed"] is False
    assert payload["intent"]["source_ref"].startswith("sha256:")
    assert payload["intent"]["target_format"] == "pdf"
    assert payload["plan"]["status"] == "needs_review"
    assert payload["plan"]["source_extension"] == ".docx"
    assert payload["plan"]["live_execution_allowed"] is False
    assert "private Rechnung" not in encoded
    assert "C:/Users" not in encoded
    assert "invoice.docx" not in encoded


def test_export_plan_dsgvo_forces_local_only_and_warning():
    response = TestClient(_app()).post(
        "/api/universal-file-io/export-plan",
        json={
            "request_text": "mach daraus png",
            "recent_source_ref": "inbox:scan1",
            "source_name_or_extension": ".pdf",
            "dsgvo_mode": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["intent"]["local_only"] is True
    assert payload["plan"]["local_only"] is True
    assert "DSGVO mode forces local-only converter selection" in payload["plan"]["warnings"]
    assert payload["plan"]["required_tool"] == "pdf_page_renderer"


def test_export_plan_reports_unsupported_target_without_execution():
    response = TestClient(_app()).post(
        "/api/universal-file-io/export-plan",
        json={
            "request_text": "mach daraus exe",
            "recent_source_ref": "inbox:file1",
            "source_name_or_extension": ".txt",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["plan"]["status"] == "unsupported"
    assert payload["plan"]["target_format"] == "unknown"
    assert "export target could not be detected from the follow-up request" in payload["plan"]["blockers"]
    assert payload["execution_performed"] is False


def test_capabilities_route_returns_redacted_summary():
    response = TestClient(_app()).get("/api/universal-file-io/capabilities?extensions=.pdf,.png,.glb")
    payload = response.json()

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.universal_file_io.capabilities_response.v1"
    assert payload["capabilities"]["count"] == 3
    assert payload["capabilities"]["families"] == {"asset_3d": 1, "image": 1, "pdf": 1}
    assert payload["raw_content_visible"] is False
    assert payload["path_values_visible"] is False


def test_telegram_delivery_plan_blocks_without_live_gates_and_redacts():
    response = TestClient(_app()).post(
        "/api/universal-file-io/telegram-delivery-plan",
        json={
            "status": "exported",
            "target_format": "pdf",
            "mime_type": "application/pdf",
            "delivery_ready": True,
            "reply_gate_enabled": False,
            "operator_live_go": False,
        },
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.universal_file_io.telegram_delivery_plan_response.v1"
    assert payload["execution_performed"] is False
    assert payload["telegram_send_performed"] is False
    assert payload["delivery_allowed"] is False
    assert payload["plan"]["status"] == "blocked"
    assert payload["plan"]["method"] == "sendDocument"
    assert "telegram_reply_gate_disabled" in payload["plan"]["blockers"]
    assert "telegram_delivery_live_go_missing" in payload["plan"]["blockers"]
    assert payload["plan"]["host_paths_visible"] is False
    assert payload["plan"]["filename_visible"] is False
    assert "C:/Users" not in encoded
    assert "secret" not in encoded


def test_telegram_delivery_plan_selects_media_methods_when_gated():
    client = TestClient(_app())
    photo = client.post(
        "/api/universal-file-io/telegram-delivery-plan",
        json={
            "status": "exported",
            "target_format": "png",
            "delivery_ready": True,
            "reply_gate_enabled": True,
            "operator_live_go": True,
        },
    ).json()
    audio = client.post(
        "/api/universal-file-io/telegram-delivery-plan",
        json={
            "status": "exported",
            "target_format": "mp3",
            "delivery_ready": True,
            "reply_gate_enabled": True,
            "operator_live_go": True,
        },
    ).json()

    assert photo["delivery_allowed"] is True
    assert photo["plan"]["method"] == "sendPhoto"
    assert "image_delivery_should_use_reviewed_preview_policy" in photo["plan"]["warnings"]
    assert audio["delivery_allowed"] is True
    assert audio["plan"]["method"] == "sendAudio"
    assert "audio_delivery_should_use_reviewed_media_policy" in audio["plan"]["warnings"]


def test_export_plan_requires_auth_when_auth_is_configured():
    response = TestClient(_app(user=None, auth_configured=True)).post(
        "/api/universal-file-io/export-plan",
        json={
            "request_text": "mach daraus pdf",
            "recent_source_ref": "inbox:file1",
            "source_name_or_extension": ".docx",
        },
    )

    assert response.status_code == 401
