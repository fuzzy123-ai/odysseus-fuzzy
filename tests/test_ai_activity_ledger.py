import json

import httpx
import pytest

from src import ai_activity_ledger
from src import llm_core


class _Response:
    is_success = True
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "hello back"}}]}


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ai_activity_record_redacts_content_and_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))

    record = ai_activity_ledger.record_ai_activity(
        owner="alice",
        surface="chat",
        session_id="session-1",
        prompt_type="chat",
        provider="openai",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="model-a",
        messages=[{"role": "user", "content": "PRIVATE RAW TEXT should stay out"}],
        output_chars=7,
        duration_ms=12,
        status="success",
    )

    encoded = json.dumps(record, sort_keys=True)
    assert record["base_host"] == "api.example.test"
    assert record["endpoint_hash"].startswith("sha256:")
    assert record["prompt_hash"].startswith("sha256:")
    assert record["input_chars"] == len("PRIVATE RAW TEXT should stay out")
    assert "PRIVATE RAW TEXT" not in encoded
    assert "/v1/chat/completions" not in encoded


def test_ai_activity_rejects_secret_markers():
    with pytest.raises(ai_activity_ledger.AIActivityLedgerError):
        ai_activity_ledger.build_ai_activity_record(
            owner="alice",
            surface="chat",
            provider="openai",
            endpoint_url="https://api.example.test/v1",
            model="Bearer secret",
            messages=[],
        )


@pytest.mark.asyncio
async def test_llm_call_async_writes_success_and_cache_hit_records(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))
    llm_core._response_cache.clear()
    calls = {"count": 0}

    async def fake_post(client, url, headers=None, **kwargs):
        calls["count"] += 1
        return _Response()

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: object())
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)

    messages = [{"role": "user", "content": "secret invoice content"}]
    first = await llm_core.llm_call_async(
        "https://api.example.test/v1/chat/completions",
        "model-a",
        messages,
        prompt_type="unit_test",
        session_id="s1",
        owner="alice",
        surface="test",
    )
    second = await llm_core.llm_call_async(
        "https://api.example.test/v1/chat/completions",
        "model-a",
        messages,
        prompt_type="unit_test",
        session_id="s1",
        owner="alice",
        surface="test",
    )

    records = _read_records(ai_activity_ledger.ledger_path())
    assert first == "hello back"
    assert second == "hello back"
    assert calls["count"] == 1
    assert [record["status"] for record in records] == ["success", "success"]
    assert records[0]["cache_hit"] is False
    assert records[1]["cache_hit"] is True
    assert records[0]["output_chars"] == len("hello back")
    assert "secret invoice content" not in json.dumps(records, sort_keys=True)


@pytest.mark.asyncio
async def test_llm_call_async_writes_error_record(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))
    llm_core._response_cache.clear()

    async def fake_post(client, url, headers=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: object())
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)

    with pytest.raises(Exception):
        await llm_core.llm_call_async(
            "https://api.example.test/v1/chat/completions",
            "model-a",
            [{"role": "user", "content": "private body"}],
            max_retries=1,
            prompt_type="unit_test",
        )

    records = _read_records(ai_activity_ledger.ledger_path())
    assert records[-1]["status"] == "error"
    assert records[-1]["error_class"] == "HTTPException"
    assert "private body" not in json.dumps(records[-1], sort_keys=True)


@pytest.mark.asyncio
async def test_stream_llm_writes_redacted_usage_record(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))

    async def fake_stream(*args, **kwargs):
        yield 'data: {"delta": "hi"}\n\n'
        yield 'data: {"type": "usage", "data": {"input_tokens": 3, "output_tokens": 2}}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "_stream_llm_impl", fake_stream)

    chunks = [
        chunk
        async for chunk in llm_core.stream_llm(
            "https://api.example.test/v1/chat/completions",
            "model-a",
            [{"role": "user", "content": "private stream prompt"}],
            prompt_type="chat",
            session_id="s1",
        )
    ]

    records = _read_records(ai_activity_ledger.ledger_path())
    assert chunks[-1] == "data: [DONE]\n\n"
    assert records[-1]["status"] == "success"
    assert records[-1]["output_chars"] == 2
    assert records[-1]["input_tokens"] == 3
    assert records[-1]["output_tokens"] == 2
    assert records[-1]["side_effects"] == ["stream"]
    assert "private stream prompt" not in json.dumps(records[-1], sort_keys=True)
