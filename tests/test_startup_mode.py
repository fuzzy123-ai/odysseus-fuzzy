from __future__ import annotations

import sys
import types

import pytest

from src.startup_mode import STARTUP_OFFLINE_ENV, startup_offline_enabled


def test_startup_offline_defaults_to_false(monkeypatch):
    monkeypatch.delenv(STARTUP_OFFLINE_ENV, raising=False)

    assert startup_offline_enabled() is False


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_startup_offline_accepts_only_literal_booleans(monkeypatch, value, expected):
    monkeypatch.setenv(STARTUP_OFFLINE_ENV, value)

    assert startup_offline_enabled() is expected


@pytest.mark.parametrize("value", ["TRUE", "False", " true", "false ", "1", "yes", ""])
def test_startup_offline_rejects_ambiguous_values(monkeypatch, value):
    monkeypatch.setenv(STARTUP_OFFLINE_ENV, value)

    with pytest.raises(RuntimeError, match="^invalid_startup_mode_configuration$"):
        startup_offline_enabled()


def test_offline_embedding_factory_returns_unavailable_before_provider_construction(monkeypatch):
    from src import embedding_lanes

    calls = []
    monkeypatch.setenv(STARTUP_OFFLINE_ENV, "true")
    monkeypatch.setattr(embedding_lanes, "_build_custom_client", lambda: calls.append("custom"))
    monkeypatch.setattr(embedding_lanes, "_build_fastembed_client", lambda: calls.append("fastembed"))
    monkeypatch.setitem(
        sys.modules,
        "src.chroma_client",
        types.SimpleNamespace(get_chroma_client=lambda: calls.append("chroma")),
    )

    assert embedding_lanes.build_embedding_lanes("offline-test") == []
    assert calls == []


def test_lazy_vector_initializers_stay_unavailable_in_offline_mode(monkeypatch):
    from src import rag_singleton, tool_index

    monkeypatch.setenv(STARTUP_OFFLINE_ENV, "true")
    monkeypatch.setattr(rag_singleton, "rag_instance", object())
    monkeypatch.setattr(tool_index, "_tool_index", object())

    assert rag_singleton.get_rag_manager() is None
    assert tool_index.get_tool_index() is None
