#!/usr/bin/env python3
"""Harbour MCP CLI: Token bootstrapping, token listing, and MCP smoke testing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any
import uuid

import bcrypt

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auth import AuthManager
from core.database import ApiToken, SessionLocal, init_db

VALID_SCOPES = [
    "mcp:planning:read",
    "mcp:harbour:context:read",
    "mcp:harbour:gateway",
    "mcp:harbour:inbox",
]


def resolve_admin_identity(auth_mgr: AuthManager, owner: str | None = None) -> tuple[str, str]:
    """Resolve the active admin username and immutable subject_id."""
    if owner:
        identity = auth_mgr.active_admin_subject_identity(username=owner)
        if identity is None:
            raise ValueError(f"User '{owner}' is not an active admin or not found.")
        return identity

    users = getattr(auth_mgr, "users", {}) or {}
    for username, data in users.items():
        if isinstance(data, dict) and data.get("is_admin") is True:
            identity = auth_mgr.active_admin_subject_identity(username=username)
            if identity is not None:
                return identity

    raise ValueError("No active admin user found in auth_manager. Specify --owner or create an admin user.")


def ensure_admin_exists(auth_mgr: AuthManager) -> tuple[str, str]:
    """Ensure at least one admin exists, bootstrapping a test admin if auth store is empty."""
    try:
        return resolve_admin_identity(auth_mgr)
    except ValueError:
        if not auth_mgr.is_configured:
            auth_mgr.setup("admin", "test-admin-password-1234!")
            return resolve_admin_identity(auth_mgr, "admin")
        raise


def bootstrap_token(
    scope: str = "mcp:planning:read",
    name: str = "harbour-cli-token",
    owner: str | None = None,
    as_json: bool = False,
    quiet: bool = False,
    auth_mgr: AuthManager | None = None,
) -> dict[str, Any]:
    """Mint a new subject-bound API token and persist it in the database."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'. Must be one of {VALID_SCOPES}")

    if auth_mgr is None:
        auth_mgr = AuthManager()

    username, subject_id = ensure_admin_exists(auth_mgr) if owner is None else resolve_admin_identity(auth_mgr, owner)

    raw_token = "ody_" + secrets.token_urlsafe(32)
    token_prefix = raw_token[:8]
    token_hash = bcrypt.hashpw(raw_token.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    token_id = str(uuid.uuid4())[:8]

    init_db()
    db = SessionLocal()
    try:
        token_entry = ApiToken(
            id=token_id,
            name=name,
            owner=username,
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes=scope,
            owner_subject_id=subject_id,
            is_active=True,
        )
        db.add(token_entry)
        db.commit()
    finally:
        db.close()

    # Invalidate token cache in running app if loaded
    try:
        import app
        invalidator = getattr(app.app.state, "invalidate_token_cache", None)
        if invalidator:
            invalidator()
        app.app.state._token_cache_dirty = True
    except Exception:
        pass

    result = {
        "id": token_id,
        "name": name,
        "owner": username,
        "owner_subject_id": subject_id,
        "scopes": [scope],
        "token_prefix": token_prefix,
        "token": raw_token,
    }

    if not quiet:
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Token successfully created: {raw_token}")
            print(f"ID: {token_id} | Name: {name} | Owner: {username} | Scope: {scope} | Subject ID: {subject_id}")

    return result


def list_tokens(as_json: bool = False) -> list[dict[str, Any]]:
    """List all active API tokens safely without revealing token hashes or secrets."""
    init_db()
    db = SessionLocal()
    try:
        tokens = db.query(ApiToken).filter(ApiToken.is_active == True).all()
        result = []
        for t in tokens:
            scopes = [s.strip() for s in (getattr(t, "scopes", "") or "").split(",") if s.strip()]
            created_at_str = t.created_at.isoformat() if getattr(t, "created_at", None) else None
            result.append({
                "id": t.id,
                "name": t.name,
                "token_prefix": getattr(t, "token_prefix", "")[:8],
                "scopes": scopes,
                "owner": getattr(t, "owner", None),
                "owner_subject_id": getattr(t, "owner_subject_id", None),
                "created_at": created_at_str,
            })
    finally:
        db.close()

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        if not result:
            print("No active API tokens found.")
        else:
            print(f"Found {len(result)} active API token(s):")
            for item in result:
                print(
                    f"- ID: {item['id']} | Name: {item['name']} | Prefix: {item['token_prefix']} | "
                    f"Scopes: {item['scopes']} | Owner: {item['owner']} | "
                    f"Subject ID: {item['owner_subject_id']} | Created: {item['created_at']}"
                )

    return result


def smoke_test(
    url: str | None = None,
    token: str | None = None,
    as_json: bool = False,
) -> dict[str, Any]:
    """Execute canonical MCP sequence: initialize -> tools/list -> tools/call harbour_planning_current_state."""
    ephemeral = False
    ephemeral_token_id = None

    if token is None:
        bootstrapped = bootstrap_token(
            scope="mcp:planning:read",
            name="harbour-ephemeral-smoke",
            as_json=False,
            quiet=True,
        )
        token = bootstrapped["token"]
        ephemeral_token_id = bootstrapped["id"]
        ephemeral = True

    try:
        if url is None:
            # In-process execution via TestClient
            from app import app
            from fastapi.testclient import TestClient

            try:
                invalidator = getattr(app.state, "invalidate_token_cache", None)
                if invalidator:
                    invalidator()
                app.state._token_cache_dirty = True
            except Exception:
                pass

            client = TestClient(app)

            def post_mcp(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                resp = client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json=body)
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, {"raw": resp.text}

        else:
            # Remote execution via urllib
            import urllib.error
            import urllib.request

            target_url = url.rstrip("/") + "/mcp"

            def post_mcp(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    target_url,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        raw = r.read().decode("utf-8")
                        return r.status, json.loads(raw)
                except urllib.error.HTTPError as exc:
                    raw = exc.read().decode("utf-8")
                    try:
                        return exc.code, json.loads(raw)
                    except Exception:
                        return exc.code, {"raw": raw}

        # 1. initialize
        rpc_init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "harbour-cli", "version": "1.0"},
            },
        }
        status_init, res_init = post_mcp(rpc_init)
        if status_init != 200 or "error" in res_init or "result" not in res_init:
            raise RuntimeError(f"Step 1 (initialize) failed: HTTP {status_init}, response: {res_init}")

        # 2. tools/list
        rpc_list = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        status_list, res_list = post_mcp(rpc_list)
        if status_list != 200 or "error" in res_list or "result" not in res_list:
            raise RuntimeError(f"Step 2 (tools/list) failed: HTTP {status_list}, response: {res_list}")

        tools = res_list.get("result", {}).get("tools", [])

        # 3. tools/call harbour_planning_current_state
        rpc_call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "harbour_planning_current_state",
                "arguments": {},
            },
        }
        status_call, res_call = post_mcp(rpc_call)
        if status_call != 200 or "error" in res_call or "result" not in res_call:
            raise RuntimeError(f"Step 3 (tools/call) failed: HTTP {status_call}, response: {res_call}")

        call_result = res_call.get("result", {})
        if call_result.get("isError") is True:
            raise RuntimeError(f"Step 3 (tools/call) returned isError=True: {call_result}")

        content = call_result.get("content", [])
        if not content:
            raise RuntimeError(f"Step 3 (tools/call) returned empty content: {call_result}")

        report = {
            "status": "success",
            "target": url or "in-process (app.py:app)",
            "protocol_version": res_init.get("result", {}).get("protocolVersion"),
            "server_info": res_init.get("result", {}).get("serverInfo", {}),
            "tools_count": len(tools),
            "tools_call": {
                "tool": "harbour_planning_current_state",
                "status": "ok",
                "content_length": len(content[0].get("text", "")),
            },
        }

        if as_json:
            print(json.dumps(report, indent=2))
        else:
            print("=" * 60)
            print("HARBOUR MCP SMOKE TEST SUCCESSFUL")
            print("=" * 60)
            print(f"Target:           {report['target']}")
            print(f"Protocol Version: {report['protocol_version']}")
            print(f"Tools Available:  {report['tools_count']}")
            print(f"Tool Call:        {report['tools_call']['tool']} -> OK")
            print("=" * 60)

        return report

    finally:
        if ephemeral and ephemeral_token_id:
            db = SessionLocal()
            try:
                db.query(ApiToken).filter(ApiToken.id == ephemeral_token_id).delete()
                db.commit()
            finally:
                db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Harbour MCP CLI: Token management and smoke tests")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # bootstrap-token
    p_boot = subparsers.add_parser("bootstrap-token", help="Bootstrap a new API token for MCP")
    p_boot.add_argument(
        "--scope",
        choices=VALID_SCOPES,
        default="mcp:planning:read",
        help="MCP scope for the token (default: mcp:planning:read)",
    )
    p_boot.add_argument("--name", default="harbour-cli-token", help="Token name (default: harbour-cli-token)")
    p_boot.add_argument("--owner", default=None, help="Owner username (default: active admin)")
    p_boot.add_argument("--json", action="store_true", help="Output JSON")

    # list-tokens
    p_list = subparsers.add_parser("list-tokens", help="List active API tokens")
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    # smoke-test
    p_smoke = subparsers.add_parser("smoke-test", help="Run MCP E2E roundtrip smoke test")
    p_smoke.add_argument("--url", default=None, help="Target server URL (default: in-process app.py:app)")
    p_smoke.add_argument("--token", default=None, help="Existing API token (default: generate ephemeral token)")
    p_smoke.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if args.subcommand == "bootstrap-token":
        bootstrap_token(scope=args.scope, name=args.name, owner=args.owner, as_json=args.json)
    elif args.subcommand == "list-tokens":
        list_tokens(as_json=args.json)
    elif args.subcommand == "smoke-test":
        smoke_test(url=args.url, token=args.token, as_json=args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
