from __future__ import annotations

from plugins.obsidian.backend.context_provider import (
    _trim_text_to_token_budget,
    provider_spec,
)


MODEL = "odysseus-utf8-byte-v1"


def test_obsidian_provider_opts_into_model_hint_abi() -> None:
    spec = provider_spec()

    assert spec["accepts_model_hint"] is True
    assert callable(spec["retrieve"])


def test_obsidian_unicode_snippet_is_bounded_by_selected_model() -> None:
    text = "prefix " + ("界🙂" * 300)

    trimmed, tokens = _trim_text_to_token_budget(text, 96, model_hint=MODEL)

    assert trimmed
    assert len(trimmed) < len(text)
    assert tokens <= 96


def test_obsidian_no_hint_keeps_legacy_behavior() -> None:
    text = "x" * 1000

    assert _trim_text_to_token_budget(text, 80) == _trim_text_to_token_budget(
        text,
        80,
        model_hint=None,
    )
