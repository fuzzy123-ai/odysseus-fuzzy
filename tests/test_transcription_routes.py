import hashlib
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from routes.transcription_routes import setup_transcription_routes
from src.transcription_runtime import TranscriptionRuntime, TranscriptionRuntimeConfig
from src.transcription_store import TranscriptionNotFoundError


OWNER = "owner_0123456789abcdef0123456789abcdef"


class Auth:
    def subject_id_for_username(self, username):
        return {"alice": OWNER, "bob": "owner_fedcba9876543210fedcba9876543210"}.get(username)


class Store:
    def recover(self): return 0
    def register_authorization(self, value): pass
    def register_retention_policy(self, value): pass

    def ingest(self, owner_id, key, chunks, media_type, authorization, retention, **expected):
        body = b"".join(chunks)
        digest = hashlib.sha256(body).hexdigest()
        if digest != expected["expected_sha256"]:
            raise RuntimeError("digest")
        artifact = SimpleNamespace(
            artifact_id="artifact_123", source_sha256=digest,
            byte_count=len(body), media_type=media_type,
        )
        job = SimpleNamespace(job_id="job_123", state="stored")
        return SimpleNamespace(artifact=artifact, job=job, idempotent_replay=key == "retry_key")

    def read_record(self, owner_id, job_id):
        if owner_id != OWNER or job_id != "job_123":
            raise TranscriptionNotFoundError("not found")
        return Record(owner_id)


class Record:
    def __init__(self, owner_id):
        self.artifact = SimpleNamespace(artifact_id="artifact_123", byte_count=3, media_type="audio/wav")
        self.job = SimpleNamespace(job_id="job_123", state="transcribed")
        self.owner_id = owner_id

    def to_dict(self):
        return {
            "artifact": {"artifact_id": "artifact_123", "owner_id": self.owner_id, "storage_locator": "secret/path"},
            "job": {"job_id": "job_123", "owner_id": self.owner_id, "authorization_id": "auth_trp_v1"},
            "segments": [{"text": "synthetic transcript", "owner_id": self.owner_id}],
        }


class Pipeline:
    def run_once(self): return None


def client():
    app = FastAPI()
    app.state.auth_manager = Auth()
    runtime = TranscriptionRuntime(
        TranscriptionRuntimeConfig(enabled=True, recording_authorized=True),
        store=Store(), pipeline=Pipeline(),
    )
    app.include_router(setup_transcription_routes(runtime))

    @app.middleware("http")
    async def session(request: Request, call_next):
        request.state.current_user = request.headers.get("x-test-user", "alice")
        request.state.api_token = request.headers.get("x-test-bearer") == "true"
        return await call_next(request)

    return TestClient(app)


def upload_headers(body, **extra):
    headers = {
        "Origin": "http://testserver",
        "Content-Type": "application/octet-stream",
        "X-Audio-Media-Type": "audio/wav",
        "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
        "Idempotency-Key": "upload_key",
    }
    headers.update(extra)
    return headers


def test_cookie_owner_upload_success_and_retry():
    with client() as api:
        response = api.post("/api/transcriptions", content=b"abc", headers=upload_headers(b"abc"))
        assert response.status_code == 200
        assert response.json()["job_id"] == "job_123"
        retry = api.post(
            "/api/transcriptions", content=b"abc",
            headers=upload_headers(b"abc", **{"Idempotency-Key": "retry_key"}),
        )
        assert retry.status_code == 200
        assert retry.json()["idempotent_replay"] is True


def test_upload_rejects_bearer_cross_origin_and_oversize():
    with client() as api:
        assert api.post(
            "/api/transcriptions", content=b"abc",
            headers=upload_headers(b"abc", **{"X-Test-Bearer": "true"}),
        ).status_code == 403
        assert api.post(
            "/api/transcriptions", content=b"abc",
            headers=upload_headers(b"abc", Origin="https://evil.example"),
        ).status_code == 403
        assert api.post(
            "/api/transcriptions", content=b"abc",
            headers=upload_headers(b"abc", **{"Content-Length": str(26 * 1024 * 1024)}),
        ).status_code == 413


def test_status_and_result_are_owner_scoped_and_hide_subject_and_server_refs():
    with client() as api:
        assert api.get("/api/transcriptions/job_123").status_code == 200
        result = api.get("/api/transcriptions/job_123/result")
        assert result.status_code == 200
        encoded = result.text
        assert "synthetic transcript" in encoded
        assert OWNER not in encoded
        assert "secret/path" not in encoded
        assert "auth_trp_v1" not in encoded
        assert api.get("/api/transcriptions/job_123", headers={"X-Test-User": "bob"}).status_code == 404


def test_deletion_requires_separate_exact_confirmation():
    with client() as api:
        assert api.delete(
            "/api/transcriptions/job_123", headers={"Origin": "http://testserver"}
        ).status_code == 409
