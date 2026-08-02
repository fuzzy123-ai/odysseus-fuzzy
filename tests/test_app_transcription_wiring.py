from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_owns_exact_runtime_lifecycle_without_background_task():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(transcription_runtime.start)" in source
    assert "await asyncio.to_thread(transcription_runtime.stop)" in source
    assert "asyncio.create_task(transcription_runtime" not in source
    assert "app.state.transcription_runtime = transcription_runtime" in source


def test_privacy_pipeline_is_default_off_local_only_and_excludes_legacy_stt():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "ODYSSEUS_TRANSCRIPTION_ENABLED:-false" in compose
    assert "ODYSSEUS_TRANSCRIPTION_LOCAL_ONLY:-true" in compose
    assert "ODYSSEUS_TRANSCRIPTION_RECORDING_AUTHORIZED:-false" in compose
    assert "ODYSSEUS_LEGACY_STT_ENABLED:-true" in compose
    assert "privacy transcription requires legacy STT to be disabled" in app
    assert "for key in (\"TELEGRAM_STT_ENABLED\", \"TELEGRAM_VOICE_STT_ENABLED\")" in app
    assert "if legacy_stt_enabled:" in app
