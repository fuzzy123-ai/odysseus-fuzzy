import asyncio
from types import SimpleNamespace

import pytest

import routes.chat_helpers as chat_helpers


class _StrictPreprocessHandler:
    def __init__(self):
        self.calls = []

    async def preprocess_message(
        self,
        message,
        att_ids,
        sess,
        auto_opened_docs=None,
        allow_tool_preprocessing=True,
        *,
        document_authority=None,
    ):
        self.calls.append(
            (
                message,
                att_ids,
                sess,
                auto_opened_docs,
                allow_tool_preprocessing,
                document_authority,
            )
        )
        return "enhanced", ["user"], "text", ["youtube"], [{"id": "attachment"}]


@pytest.mark.parametrize("authority", [None, object()])
def test_preprocess_forwards_exact_authority_and_preserves_dto(authority):
    handler = _StrictPreprocessHandler()
    sess = SimpleNamespace()

    result = asyncio.run(
        chat_helpers.preprocess(
            handler,
            "hello",
            ["attachment"],
            sess,
            auto_opened_docs=[],
            allow_tool_preprocessing=False,
            document_authority=authority,
        )
    )

    assert result.enhanced_message == "enhanced"
    assert result.user_content == ["user"]
    assert result.text_for_context == "text"
    assert result.youtube_transcripts == ["youtube"]
    assert result.attachment_meta == [{"id": "attachment"}]
    assert handler.calls[0][-1] is authority
    assert handler.calls[0][4] is False
    if authority is None:
        asyncio.run(chat_helpers.preprocess(handler, "omitted", [], sess))
        assert handler.calls[-1][-1] is None


class _BuildSentinel(Exception):
    pass


@pytest.mark.parametrize("authority", [None, object()])
def test_build_chat_context_calls_strict_preprocess_before_other_dependencies(
    monkeypatch, authority
):
    observed = {}

    async def strict_preprocess(
        handler,
        message,
        att_ids,
        sess,
        auto_opened_docs=None,
        allow_tool_preprocessing=True,
        *,
        document_authority=None,
    ):
        observed["authority"] = document_authority
        observed["handler"] = handler
        raise _BuildSentinel

    monkeypatch.setattr(chat_helpers, "preprocess", strict_preprocess)
    handler = SimpleNamespace(
        validate_and_extract_preset=lambda _preset_id: (0.1, 10, "system", "character")
    )
    sess = SimpleNamespace(endpoint_url="http://model", model="model")

    with pytest.raises(_BuildSentinel):
        asyncio.run(
            chat_helpers.build_chat_context(
                sess=sess,
                request=SimpleNamespace(),
                chat_handler=handler,
                chat_processor=SimpleNamespace(),
                message="hello",
                session_id="session-1",
                document_authority=authority,
            )
        )

    assert observed["authority"] is authority
    assert observed["handler"] is handler
    if authority is None:
        observed.clear()
        with pytest.raises(_BuildSentinel):
            asyncio.run(
                chat_helpers.build_chat_context(
                    sess=sess,
                    request=SimpleNamespace(),
                    chat_handler=handler,
                    chat_processor=SimpleNamespace(),
                    message="omitted",
                    session_id="session-1",
                )
            )
        assert observed["authority"] is None
