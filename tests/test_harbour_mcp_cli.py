"""Tests for scripts/harbour_mcp_cli.py: Token bootstrap, listing, and MCP smoke test."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import pytest
import bcrypt

# Ensure src.database has real database symbols if conftest stubbed it during pytest
if "src.database" in sys.modules and not hasattr(sys.modules["src.database"], "Webhook"):
    import core.database
    for k, v in core.database.__dict__.items():
        if not k.startswith("__"):
            setattr(sys.modules["src.database"], k, v)

from core.database import ApiToken, SessionLocal
from scripts.harbour_mcp_cli import (
    VALID_SCOPES,
    bootstrap_token,
    list_tokens,
    smoke_test,
    main as cli_main,
)


def test_bootstrap_token_persists_and_verifies_hash():
    """bootstrap_token must persist a row with matching prefix, subject, and valid bcrypt hash."""
    created = bootstrap_token(
        scope="mcp:planning:read",
        name="test-boot-token",
        quiet=True,
    )
    token_id = created["id"]
    raw_token = created["token"]
    prefix = created["token_prefix"]

    assert raw_token.startswith("ody_")
    assert prefix == raw_token[:8]
    assert created["scopes"] == ["mcp:planning:read"]
    assert created["name"] == "test-boot-token"
    assert created["owner_subject_id"] is not None

    db = SessionLocal()
    try:
        row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
        assert row is not None
        assert row.is_active is True
        assert row.name == "test-boot-token"
        assert row.token_prefix == prefix
        assert row.scopes == "mcp:planning:read"
        assert row.owner_subject_id == created["owner_subject_id"]
        assert row.owner == created["owner"]

        # Verify bcrypt hash matches raw token
        assert bcrypt.checkpw(raw_token.encode("utf-8"), row.token_hash.encode("utf-8")) is True
    finally:
        db.query(ApiToken).filter(ApiToken.id == token_id).delete()
        db.commit()
        db.close()


def test_bootstrap_token_invalid_scope():
    """bootstrap_token must reject unauthorized or invalid scopes."""
    with pytest.raises(ValueError, match="Invalid scope"):
        bootstrap_token(scope="invalid:unauthorized:scope", quiet=True)


def test_list_tokens_no_secrets_leaked():
    """list_tokens must return token metadata without raw tokens or password/token hashes."""
    created = bootstrap_token(
        scope="mcp:harbour:gateway",
        name="test-list-token",
        quiet=True,
    )
    token_id = created["id"]
    prefix = created["token_prefix"]

    try:
        tokens = list_tokens(as_json=False)
        target = next((t for t in tokens if t["id"] == token_id), None)
        assert target is not None
        assert target["name"] == "test-list-token"
        assert target["token_prefix"] == prefix
        assert len(target["token_prefix"]) == 8
        assert target["scopes"] == ["mcp:harbour:gateway"]
        assert target["owner"] == created["owner"]
        assert target["owner_subject_id"] == created["owner_subject_id"]

        # Absolute secret safety guarantees
        assert "token" not in target
        assert "token_hash" not in target
        assert "hash" not in target
        assert created["token"] not in json.dumps(target)
    finally:
        db = SessionLocal()
        try:
            db.query(ApiToken).filter(ApiToken.id == token_id).delete()
            db.commit()
        finally:
            db.close()


def test_smoke_test_in_process():
    """smoke_test against in-process app.py:app must succeed with initialize, tools/list, tools/call."""
    report = smoke_test(url=None, as_json=False)

    assert report["status"] == "success"
    assert report["target"] == "in-process (app.py:app)"
    assert report["protocol_version"] in ("2025-06-18", "2024-11-05")
    assert report["tools_count"] > 0
    assert report["tools_call"]["tool"] == "harbour_planning_current_state"
    assert report["tools_call"]["status"] == "ok"
    assert report["tools_call"]["content_length"] > 0


def test_cli_main_subcommands(capsys, monkeypatch):
    """CLI subcommands bootstrap-token, list-tokens, and smoke-test work via argparse."""
    # 1. bootstrap-token --json
    monkeypatch.setattr("sys.argv", ["harbour_mcp_cli.py", "bootstrap-token", "--scope", "mcp:harbour:inbox", "--name", "cli-test-inbox", "--json"])
    assert cli_main() == 0
    out_boot, _ = capsys.readouterr()
    boot_data = json.loads(out_boot)
    assert boot_data["name"] == "cli-test-inbox"
    assert boot_data["scopes"] == ["mcp:harbour:inbox"]
    assert "token" in boot_data
    token_id = boot_data["id"]

    try:
        # 2. list-tokens --json
        monkeypatch.setattr("sys.argv", ["harbour_mcp_cli.py", "list-tokens", "--json"])
        assert cli_main() == 0
        out_list, _ = capsys.readouterr()
        list_data = json.loads(out_list)
        found = any(item["id"] == token_id for item in list_data)
        assert found is True
        # Ensure raw token was NOT leaked in list output
        assert boot_data["token"] not in out_list

    finally:
        db = SessionLocal()
        try:
            db.query(ApiToken).filter(ApiToken.id == token_id).delete()
            db.commit()
        finally:
            db.close()


def test_cli_sync_and_query_usi(tmp_path, capsys):
    """CLI subcommands sync-usi and query-usi execute properly."""
    doc_dir = tmp_path / "docs" / "plans"
    doc_dir.mkdir(parents=True)
    (doc_dir / "cli-plan.md").write_text("# CLI Planning\nTesting CLI sync and query capabilities.", encoding="utf-8")

    # 1. sync-usi --json
    code = cli_main(["sync-usi", "--repo-root", str(tmp_path), "--sources", "docs/plans/cli-plan.md", "--json"])
    assert code == 0
    out_sync, _ = capsys.readouterr()
    sync_report = json.loads(out_sync)
    assert sync_report["schema"] == "odysseus.harbour.memory_index_report.v1"
    assert sync_report["indexed_sources"] == 1
    assert sync_report["indexed_chunks"] >= 1

    # 2. query-usi --json
    code = cli_main(["query-usi", "capabilities", "--repo-root", str(tmp_path), "--json"])
    assert code == 0
    out_query, _ = capsys.readouterr()
    query_result = json.loads(out_query)
    assert query_result["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert query_result["total_matches"] >= 1
    assert "capabilities" in query_result["matches"][0]["snippet"]
