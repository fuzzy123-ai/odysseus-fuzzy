import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GitHubIssueRecord
from src.agent_tools import TOOL_TAGS, ToolBlock
from src.github_issue_fields import default_issue_field_definitions
from src.github_issue_sync import GitHubIssueSyncItem, GitHubIssueSyncPage
from src.mcp_server_tool_policy import classify_mcp_tool
from src.tool_execution import execute_tool_block
from src.tool_implementations import do_manage_github_issues
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS, plan_mode_disabled_tools


def _session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add_all(
            [
                GitHubIssueRecord(
                    id="issue-1",
                    owner="alice",
                    provider="github",
                    repository="fuzzy123-ai/odysseus-fuzzy",
                    external_id="1",
                    title="Telegram inbox attachment fails",
                    body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
                    state="open",
                    labels_json=["area/inbox", "priority/high"],
                ),
                GitHubIssueRecord(
                    id="issue-2",
                    owner="alice",
                    provider="github",
                    repository="fuzzy123-ai/odysseus-fuzzy",
                    external_id="2",
                    title="Telegram inbox attachment fails",
                    body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
                    state="closed",
                    labels_json=["area/inbox"],
                ),
            ]
        )
        db.commit()
    return Session


def test_manage_github_issues_schema_index_security_and_mcp_policy():
    schema_by_name = {(schema.get("function") or {}).get("name"): schema for schema in FUNCTION_TOOL_SCHEMAS}

    assert "manage_github_issues" in schema_by_name
    actions = schema_by_name["manage_github_issues"]["function"]["parameters"]["properties"]["action"]["enum"]
    assert {"sync", "duplicate_search", "create_triaged", "set_fields"}.issubset(set(actions))
    assert set(default_issue_field_definitions()).issubset(
        schema_by_name["manage_github_issues"]["function"]["parameters"]["properties"]
    )

    assert "manage_github_issues" in TOOL_TAGS
    assert "manage_github_issues" in BUILTIN_TOOL_DESCRIPTIONS
    assert "manage_github_issues" in NON_ADMIN_BLOCKED_TOOLS
    assert "manage_github_issues" not in PLAN_MODE_READONLY_TOOLS
    assert "manage_github_issues" in plan_mode_disabled_tools()

    decision = classify_mcp_tool("manage_github_issues")
    assert decision.exposed is False
    assert decision.category == "high_risk"


def test_manage_github_issues_native_function_call_converts_to_tool_block():
    block = function_call_to_tool_block(
        "manage_github_issues",
        json.dumps({
            "action": "duplicate_search",
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "title": "Telegram inbox attachment fails",
        }),
    )

    assert block is not None
    assert block.tool_type == "manage_github_issues"
    assert json.loads(block.content)["action"] == "duplicate_search"


@pytest.mark.asyncio
async def test_duplicate_search_runs_locally_and_dispatches(monkeypatch):
    monkeypatch.setattr("src.tool_domains.github_issues.SessionLocal", _session_factory())
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)

    desc, result = await execute_tool_block(
        ToolBlock(
            "manage_github_issues",
            json.dumps(
                {
                    "action": "duplicate_search",
                    "repository": "fuzzy123-ai/odysseus-fuzzy",
                    "title": "Telegram inbox attachment fails",
                    "body": "Telegram file upload is not processed by universal inbox memory.",
                    "top_k": 2,
                }
            ),
        ),
        owner="alice",
    )

    assert desc == "manage_github_issues"
    assert result["exit_code"] == 0
    assert result["index"]["indexed"] == 2
    candidates = result["github_issue_duplicates"]["candidates"]
    assert [candidate["external_id"] for candidate in candidates] == ["1", "2"]
    assert candidates[0]["score"] >= candidates[1]["score"]


@pytest.mark.asyncio
async def test_sync_and_write_actions_are_gated_without_live_go(monkeypatch):
    monkeypatch.setattr("src.tool_domains.github_issues.SessionLocal", _session_factory())

    sync = await do_manage_github_issues(
        json.dumps({"action": "sync", "repository": "fuzzy123-ai/odysseus-fuzzy"}),
        owner="alice",
    )
    assert sync["status"] == "needs_live_go"
    assert sync["requires_live_go"] is True

    set_fields = await do_manage_github_issues(
        json.dumps(
            {
                "action": "set_fields",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
                "issue_ref": "#1",
                "fields": {"priority": "high", "area": "inbox"},
            }
        ),
        owner="alice",
    )
    assert set_fields["status"] == "confirmation_required"
    assert set_fields["requires_confirmation"] is True

    confirmed = await do_manage_github_issues(
        json.dumps(
            {
                "action": "set_fields",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
                "issue_ref": "#1",
                "fields": {"priority": "high", "area": "inbox"},
                "confirmed": True,
            }
        ),
        owner="alice",
    )
    assert confirmed["status"] == "needs_live_go"
    assert confirmed["requires_live_go"] is True


@pytest.mark.asyncio
async def test_sync_runs_when_server_live_gate_and_repo_allowlist_are_set(monkeypatch):
    monkeypatch.setattr("src.tool_domains.github_issues.SessionLocal", _session_factory())
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_LIVE_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES", "fuzzy123-ai/odysseus-fuzzy")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED", "true")

    class FakeLiveReadClient:
        def __init__(self, *, token, allow_public, max_items):
            self.calls = []

        def list_issues_page(self, *, repository, since, cursor):
            self.calls.append((repository, since, cursor))
            return GitHubIssueSyncPage(
                issues=(
                    GitHubIssueSyncItem(
                        external_id="9",
                        title="Live synced issue",
                        labels=("area/github",),
                    ),
                )
            )

    monkeypatch.setattr(
        "src.tool_domains.github_issues._make_live_read_client",
        lambda *, token, allow_public, max_items: FakeLiveReadClient(
            token=token,
            allow_public=allow_public,
            max_items=max_items,
        ),
    )

    result = await do_manage_github_issues(
        json.dumps(
            {
                "action": "sync",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
                "confirmed": True,
                "max_items": 5,
            }
        ),
        owner="alice",
    )

    assert result["status"] == "synced"
    assert result["provider_writes_performed"] == 0
    assert result["sync"]["fetched"] == 1
    assert result["sync"]["created"] == 1


@pytest.mark.asyncio
async def test_sync_blocks_when_repository_is_not_allowlisted(monkeypatch):
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_LIVE_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES", "other/repo")

    result = await do_manage_github_issues(
        json.dumps(
            {
                "action": "sync",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
                "confirmed": True,
            }
        ),
        owner="alice",
    )

    assert result["status"] == "needs_live_go"
    assert result["reason"] == "repository_not_allowlisted"


@pytest.mark.asyncio
async def test_sync_reports_readiness_when_confirmed_is_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_LIVE_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES", "fuzzy123-ai/odysseus-fuzzy")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED", "true")

    result = await do_manage_github_issues(
        json.dumps(
            {
                "action": "sync",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
            }
        ),
        owner="alice",
    )

    assert result["status"] == "needs_live_go"
    assert result["reason"] == "confirmation_required"


def test_sync_readiness_does_not_claim_server_token_is_validated(monkeypatch):
    from src.tool_domains.github_issues import build_github_issue_sync_readiness

    monkeypatch.setenv("GITHUB_ISSUE_SYNC_LIVE_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES", "fuzzy123-ai/odysseus-fuzzy")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "server-side-token")

    readiness = build_github_issue_sync_readiness("fuzzy123-ai/odysseus-fuzzy")

    assert readiness["status"] == "ready_for_confirmed_sync"
    assert readiness["auth_mode"] == "server_token_or_public_unauthenticated"
    assert readiness["server_token_present"] is True
    assert readiness["server_token_validated"] is False
    assert readiness["public_fallback_enabled"] is True


@pytest.mark.asyncio
async def test_create_triaged_blocks_on_high_confidence_duplicate(monkeypatch):
    monkeypatch.setattr("src.tool_domains.github_issues.SessionLocal", _session_factory())

    result = await do_manage_github_issues(
        json.dumps(
            {
                "action": "create_triaged",
                "repository": "fuzzy123-ai/odysseus-fuzzy",
                "title": "Telegram inbox attachment fails",
                "body": "File upload through Telegram is not processed by the universal inbox memory pipeline.",
                "fields": {"type": "bug", "priority": "high", "area": "inbox"},
                "confirmed": True,
            }
        ),
        owner="alice",
    )

    assert result["status"] == "blocked_by_duplicate_candidate"
    assert result["requires_confirmation"] is True
    assert result["github_issue_duplicates"]["blocks_auto_create"] is True
