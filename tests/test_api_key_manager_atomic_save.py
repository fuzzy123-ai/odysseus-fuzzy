import json

import pytest

from src.api_key_manager import APIKeyManager


def test_api_key_save_uses_atomic_json_and_preserves_encryption(tmp_path, monkeypatch):
    mgr = APIKeyManager(str(tmp_path))
    calls = []

    def fake_atomic(path, data, *, indent=None):
        calls.append((path, dict(data), indent))
        (tmp_path / "api_keys.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("src.api_key_manager.atomic_write_json", fake_atomic)

    mgr.save("openai", "sk-secret")

    assert calls
    path, data, indent = calls[0]
    assert path == str(tmp_path / "api_keys.json")
    assert indent is None
    assert data["openai"] != "sk-secret"
    assert mgr.load() == {"openai": "sk-secret"}


def test_failed_atomic_replacement_leaves_prior_credentials_file_intact(tmp_path, monkeypatch):
    mgr = APIKeyManager(str(tmp_path))
    mgr.save("openai", "sk-old")
    before = (tmp_path / "api_keys.json").read_text(encoding="utf-8")

    def fail_atomic(path, data, *, indent=None):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.api_key_manager.atomic_write_json", fail_atomic)

    with pytest.raises(OSError, match="simulated"):
        mgr.save("anthropic", "sk-new")

    assert (tmp_path / "api_keys.json").read_text(encoding="utf-8") == before
    assert mgr.load() == {"openai": "sk-old"}
