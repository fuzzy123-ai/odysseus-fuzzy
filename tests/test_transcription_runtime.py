import asyncio
from types import SimpleNamespace

import pytest

from src.transcription_runtime import (
    TranscriptionRuntime,
    TranscriptionRuntimeConfig,
    TranscriptionRuntimeError,
)


class FakeStore:
    def __init__(self):
        self.recovered = 0
        self.chunks = []
        self.authorization = None
        self.retention = None

    def recover(self):
        self.recovered += 1
        return 0

    def register_authorization(self, value):
        self.authorization = value

    def register_retention_policy(self, value):
        self.retention = value

    def ingest(self, owner_id, key, chunks, media_type, authorization, retention, **expected):
        self.chunks = list(chunks)
        return SimpleNamespace(owner_id=owner_id, key=key, expected=expected)

    def read_record(self, owner_id, job_id):
        return owner_id, job_id


class FakePipeline:
    def __init__(self):
        self.runs = 0

    def run_once(self):
        self.runs += 1


def config(**changes):
    values = dict(
        enabled=True, recording_authorized=True,
        worker_poll_seconds=0.01, shutdown_timeout_seconds=1,
    )
    values.update(changes)
    return TranscriptionRuntimeConfig(**values)


def test_single_worker_recovers_once_and_shutdown_is_bounded():
    store = FakeStore()
    pipeline = FakePipeline()
    runtime = TranscriptionRuntime(config(), store=store, pipeline=pipeline)

    runtime.start()
    with pytest.raises(TranscriptionRuntimeError, match="already started"):
        runtime.start()
    runtime.stop()

    assert store.recovered == 1
    assert pipeline.runs >= 1


def test_config_requires_local_only_and_timeout_below_reservation_ttl():
    with pytest.raises(TranscriptionRuntimeError):
        TranscriptionRuntimeConfig(enabled=True, local_only=False)
    with pytest.raises(TranscriptionRuntimeError):
        TranscriptionRuntimeConfig(enabled=True, request_timeout_seconds=300, reservation_ttl_seconds=300)


@pytest.mark.asyncio
async def test_stream_bridge_is_bounded_and_passes_server_owned_refs():
    store = FakeStore()
    runtime = TranscriptionRuntime(config(max_chunk_bytes=3, bridge_depth=1), store=store, pipeline=FakePipeline())

    async def chunks():
        yield b"abc"
        await asyncio.sleep(0)
        yield b"de"

    result = await runtime.ingest_stream(
        "owner_0123456789abcdef0123456789abcdef",
        "retry_key",
        chunks(),
        "audio/wav",
        expected_sha256="0" * 64,
        expected_size=5,
    )

    assert result.key == "retry_key"
    assert store.chunks == [b"abc", b"de"]
    assert store.authorization.owner_id == result.owner_id
    assert store.retention.owner_id == result.owner_id


@pytest.mark.asyncio
async def test_stream_bridge_aborts_on_exact_length_mismatch():
    runtime = TranscriptionRuntime(config(), store=FakeStore(), pipeline=FakePipeline())

    async def chunks():
        yield b"short"

    with pytest.raises(TranscriptionRuntimeError, match="size mismatch"):
        await runtime.ingest_stream(
            "owner_0123456789abcdef0123456789abcdef", "retry_key", chunks(), "audio/wav",
            expected_sha256="0" * 64, expected_size=6,
        )
