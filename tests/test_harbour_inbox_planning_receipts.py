from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_inbox_service import (
    _closed_planning_receipt,
    _planning_receipt_ref_schema,
    _unified_payload,
    _unified_receipts_payload,
    _validate_receipt_ref,
)
from src.mcp_authenticated_dispatch import (
    AppOwnedPlanningRuntime,
    MCP_HARBOUR_INBOX_SCOPE,
    McpAuthenticatedIdentity,
    McpDispatchFailure,
)
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import DurablePlanningRevisionStore
from src.planning_roadmap_migration import (
    build_h1r03_migration_manifest,
    canonical_json_bytes,
)
from tests.planning_durable_store_fixture import _append_clean_descendants
from tests.test_harbour_inbox_service import _clarification_capture, _knowledge_capture
from src.auth_helpers import SessionPrincipal


def _runtime_fixture(tmp_path: Path):
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth", key_id="planning-key-1",
        secret=b"native-durable-planning-source-secret-0001",
        clock=lambda: datetime.now(timezone.utc),
    )
    subject = "owner_" + "a" * 32
    principal = issuer.issue(
        subject_id=subject, owner_id="fuzzy", auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600, nonce="inbox-planning-receipt-fixture-0001",
    )
    store = DurablePlanningRevisionStore(tmp_path / "durable-planning", trusted_issuers=(issuer,))
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=manifest)
    store.accept_migration(principal, batch_id=manifest["batch_id"], current_manifest=lambda: deepcopy(manifest))
    _append_clean_descendants(store, principal)
    owner, _batch, _records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    selected = min(heads, key=lambda value: value["roadmap_id"])
    current = store.get_head(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=selected["roadmap_id"],
    )
    current_revision = selected["current_revision"]
    document = json.loads(store.read_payload(
        principal, owner_id=owner, revision_id=current.current_revision_id,
    ).decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = current_revision + 1
    roadmap["title"] = "Inbox receipt integration fixture"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {"revision": roadmap["revision"], "content_hash": roadmap["content_hash"]},
    }
    document["project"]["draft_refs"] = []
    store.append_revision(
        principal, owner_id=owner, project_id="harbour-one", roadmap_id=roadmap["roadmap_id"],
        payload_bytes=canonical_json_bytes(document), expected_head=current,
        idempotency_key="inbox-planning-receipt-append-0001",
    )
    identity = McpAuthenticatedIdentity(
        "inbox-planning-token", "fuzzy", subject, 1, 0, MCP_HARBOUR_INBOX_SCOPE,
    )
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(Path(__file__).resolve().parents[1]), legacy_store=None,
        durable_store=store, issuer=issuer,
    )
    return runtime, identity


def test_planning_capture_default_is_receipt_free_and_opt_in_reads_selected_head(tmp_path, monkeypatch):
    runtime, identity = _runtime_fixture(tmp_path)
    original = runtime.durable_store.read_subject_bound_current_receipt
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.durable_store, "read_subject_bound_current_receipt", record)
    default = runtime.capture_inbox_snapshot(identity=identity, limit=1)
    assert calls == []
    enriched = runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True)
    assert len(calls) == 1
    assert default.items[0].receipt is None
    assert enriched.items[0].receipt is not None
    assert enriched.items[0].receipt.operation == "append_revision"
    assert enriched.items[0].receipt.producer == "harbour_planning_store"
    assert enriched.items[0].receipt.verified is True
    assert enriched.items[0].receipt.authorizes_mutation is False


def test_typed_planning_capture_keeps_receipts_on_the_bounded_prefix(tmp_path):
    runtime, identity = _runtime_fixture(tmp_path)
    # The typed opt-in stays explicit and keeps the receipt reader scoped to
    # the normalized prefix; legacy callers retain their original default.
    typed = runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True, typed=True)
    assert len(typed.items) == 1
    assert typed.items[0].receipt is not None


def test_typed_clipped_planning_keeps_prefix_receipt_while_legacy_v2_rejects(tmp_path):
    runtime, identity = _runtime_fixture(tmp_path)
    context = "mcp-planning-read-v1\0" + identity.token_id + "\0" + identity.owner_subject_id
    principal = runtime.issuer.issue(
        subject_id=identity.owner_subject_id, owner_id=identity.owner_alias,
        auth_context_digest="sha256:" + __import__("hashlib").sha256(context.encode("utf-8")).hexdigest(), ttl_seconds=60,
    )
    owner, _batch, _records, heads = runtime.durable_store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    head = next(value for value in heads if value["current_revision"] == value["approved_revision"])
    current = runtime.durable_store.get_head(principal, owner_id=owner, project_id="harbour-one", roadmap_id=head["roadmap_id"])
    document = json.loads(runtime.durable_store.read_payload(principal, owner_id=owner, revision_id=current.current_revision_id).decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = int(head["current_revision"]) + 1
    roadmap["title"] = "Typed clipped planning prefix fixture"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {roadmap["roadmap_id"]: {"revision": roadmap["revision"], "content_hash": roadmap["content_hash"]}}
    document["project"]["draft_refs"] = []
    runtime.durable_store.append_revision(principal, owner_id=owner, project_id="harbour-one", roadmap_id=roadmap["roadmap_id"], payload_bytes=canonical_json_bytes(document), expected_head=current, idempotency_key="typed-clipped-prefix-002")
    pending = runtime.capture_inbox_snapshot(identity=identity, limit=100, include_receipts=True)
    assert len(pending.items) == 2
    typed = runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True, typed=True)
    assert typed.clipped is True and len(typed.items) == 1 and typed.items[0].receipt is not None
    with pytest.raises(McpDispatchFailure) as legacy:
        runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True)
    assert legacy.value.code == "planning_failed"


def test_planning_receipt_reference_and_closed_shape_bind_item_digest(tmp_path):
    runtime, identity = _runtime_fixture(tmp_path)
    capture = runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True)
    item = capture.items[0]
    closed = _closed_planning_receipt(item)
    assert set(closed) == {
        "schema", "source", "owner", "authority", "receipt_handle",
        "current_binding_digest", "producer", "operation", "occurred_at",
        "verified", "freshness", "authorizes_mutation", "redaction_flags",
    }
    ref = {
        "source": "planning",
        "item_handle": "hi1:planning:" + __import__("src.harbour_inbox_service", fromlist=["_unified_digest"])._unified_digest(
            "planning", item.record, item.record,
        ),
        "binding_digest": item.record,
        "receipt_handle": item.receipt.receipt_handle,
    }
    assert _validate_receipt_ref(ref) == ref
    assert _planning_receipt_ref_schema()["properties"]["source"] == {"const": "planning"}


def test_planning_unified_receipt_projection_populates_only_receipt_ref(tmp_path):
    runtime, identity = _runtime_fixture(tmp_path)
    planning = runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True)
    clarification = _clarification_capture(identity, count=0)
    knowledge = _knowledge_capture(identity, count=0)
    payload = _unified_payload(
        {"clarification": clarification, "planning": planning, "knowledge": knowledge}, set(), 1,
    )
    enriched = _unified_receipts_payload(
        payload, {"clarification": clarification, "planning": planning, "knowledge": knowledge},
        SessionPrincipal("fuzzy", identity.owner_subject_id, "mcp_subject"), None,
    )
    row = next(item for item in enriched["items"] if item["source"] == "planning")
    assert row["receipt_handle"] is None
    assert set(row["receipt_ref"]) == {"source", "item_handle", "binding_digest", "receipt_handle"}
    assert row["receipt_ref"]["binding_digest"] == row["binding_digest"]


def test_planning_receipt_reader_failure_is_not_unavailable(tmp_path, monkeypatch):
    runtime, identity = _runtime_fixture(tmp_path)
    original = runtime.durable_store.read_subject_bound_current_receipt

    def fail(*_args, **_kwargs):
        raise RuntimeError("reader failure")

    monkeypatch.setattr(runtime.durable_store, "read_subject_bound_current_receipt", fail)
    with pytest.raises(McpDispatchFailure) as error:
        runtime.capture_inbox_snapshot(identity=identity, limit=1, include_receipts=True)
    assert error.value.code == "planning_failed"
    assert callable(original)


def test_actual_default_app_planning_receipt_roundtrip_after_restart(tmp_path):
    from tests.test_app_mcp_authenticated_e2e import _UNIFIED_REAL_CHILD

    repo_root = Path(__file__).resolve().parents[1]
    profile_root, data_root = tmp_path / "profile", tmp_path / "data"
    for path in (profile_root, data_root, profile_root / "roaming", profile_root / "local",
                 tmp_path / "temp", tmp_path / "cache"):
        path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "real-planning-receipt-result.json"
    issuer_secret = base64.b64encode(bytes(range(32))).decode("ascii")
    assert base64.b64decode(issuer_secret, validate=True) == bytes(range(32))
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HOME": str(profile_root),
        "USERPROFILE": str(profile_root), "APPDATA": str(profile_root / "roaming"),
        "LOCALAPPDATA": str(profile_root / "local"), "TEMP": str(tmp_path / "temp"),
        "TMP": str(tmp_path / "temp"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONPATH": str(repo_root),
        "ODYSSEUS_STARTUP_OFFLINE": "true", "ODYSSEUS_DATA_DIR": str(data_root),
        "DATABASE_URL": f"sqlite:///{(data_root / 'app.db').as_posix()}",
        "ODYSSEUS_DISABLE_MCP": "1", "ODYSSEUS_DISABLE_USER_MCP": "1",
        "ODYSSEUS_INPROCESS_TASKS": "0", "ODYSSEUS_SECURITY_PUBLIC_EGRESS_DISCOVERY_DISABLED": "true",
        "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED": "false", "ODYSSEUS_TRANSCRIPTION_ENABLED": "false",
        "ODYSSEUS_LEGACY_STT_ENABLED": "false", "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false",
        "SECURE_COOKIES": "false", "ODYSSEUS_PLANNING_ISSUER_SECRET_B64": issuer_secret,
        "ODYSSEUS_PLANNING_ISSUER_KEY_ID": "real-planning-receipt-key",
        "ODYSSEUS_TEST_RESULT_FILE": str(result_file),
    })
    child = tmp_path / "real-planning-receipt-child.py"
    source = _UNIFIED_REAL_CHILD.replace(
        'value.get("schema") == "odysseus.harbour_inbox_unified_list.v1"',
        'value.get("schema") == "odysseus.harbour_inbox_unified_list.v2"',
    ).replace(
        '"current_revision", "approved_revision"}',
        '"current_revision", "approved_revision", "receipt_ref"}',
    ).replace(
        '"view": "unified", "limit": 10',
        '"view": "unified_receipts", "limit": 10',
    ).replace(
        'assert_real(payload(first))',
        'assert_real(payload(first))\n        planning_receipt = next(row["receipt_ref"] for row in payload(first)["items"] if row["source"] == "planning")\n        assert set(planning_receipt) == {"source", "item_handle", "binding_digest", "receipt_handle"}\n        exact = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "harbour_inbox_list", "arguments": {"view": "receipt", "receipt_ref": planning_receipt}}})\n        assert exact.status_code == 200\n        receipt = payload(exact)\n        assert set(receipt) == {"schema", "source", "owner", "authority", "receipt_handle", "current_binding_digest", "producer", "operation", "occurred_at", "verified", "freshness", "authorizes_mutation", "redaction_flags"}\n        assert receipt["source"] == "planning" and receipt["producer"] == "harbour_planning_store" and receipt["authorizes_mutation"] is False\n        tampered_refs = (\n            {**planning_receipt, "item_handle": "hi1:planning:" + "0" * 64},\n            {**planning_receipt, "binding_digest": "1" * 64},\n            {**planning_receipt, "receipt_handle": "sha256:" + "2" * 64},\n        )\n        for tampered_ref in tampered_refs:\n            denied = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "harbour_inbox_list", "arguments": {"view": "receipt", "receipt_ref": tampered_ref}}})\n            assert denied.status_code == 200 and denied.json().get("error", {}).get("code") == -32014\n            assert "content" not in denied.json().get("result", {})',
    )
    assert source != _UNIFIED_REAL_CHILD
    assert source.count('value.get("schema") == "odysseus.harbour_inbox_unified_list.v2"') == 1
    assert source.count('"view": "unified_receipts", "limit": 10') == 2
    assert source.count("planning_receipt = next") == 1
    assert source.count("tampered_refs = (") == 1
    source = source.replace(
        'assert_real(payload(restarted))',
        'restarted_payload = payload(restarted)\n        assert_real(restarted_payload)\n        restarted_ref = next(row["receipt_ref"] for row in restarted_payload["items"] if row["source"] == "planning")\n        assert restarted_ref == planning_receipt\n        restarted_exact = client.post("/api/plugins/mcp", headers={"Authorization": "Bearer " + token}, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "harbour_inbox_list", "arguments": {"view": "receipt", "receipt_ref": restarted_ref}}})\n        assert restarted_exact.status_code == 200\n        assert payload(restarted_exact) == receipt',
    )
    assert source.count("restarted_ref = next") == 1
    success_marker = 'result_file.write_text(json.dumps({"ok": True, "selection": bool(selection), "stage": "complete"}, sort_keys=True), encoding="utf-8")'
    failure_marker = '''except Exception:
    result_file.write_text(json.dumps({
        "ok": False, "selection": False,
        "stage": stage if stage in {"fixture", "app_import", "app_actions", "restart"} else "failed",
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)'''
    assert source.count(success_marker) == 1
    assert source.count(failure_marker) == 1
    source = source.replace(
        success_marker,
        'result_file.write_text(json.dumps({"ok": True, "selection": bool(selection), "stage": "complete", "error_code": None, "error_line": None}, sort_keys=True), encoding="utf-8")',
    ).replace(
        failure_marker,
        '''except Exception as exc:
    error_code = (
        "assertion" if isinstance(exc, AssertionError)
        else "fixture_value" if isinstance(exc, (KeyError, IndexError, TypeError, ValueError))
        else "io" if isinstance(exc, OSError)
        else "unexpected"
    )
    error_line = 0
    frame = exc.__traceback__
    while frame is not None:
        if frame.tb_frame.f_code.co_filename == __file__:
            error_line = frame.tb_lineno
        frame = frame.tb_next
    if type(error_line) is not int or not 1 <= error_line <= 1000000:
        error_line = 0
    result_file.write_text(json.dumps({
        "ok": False, "selection": False,
        "stage": stage if stage in {"fixture", "app_import", "app_actions", "restart"} else "failed",
        "error_code": error_code, "error_line": error_line,
    }, sort_keys=True), encoding="utf-8")
    raise SystemExit(1)''',
    )
    assert source.count('"error_code": error_code, "error_line": error_line') == 1
    assert source.count('"error_code": None, "error_line": None') == 1
    child.write_text(source, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [sys.executable, str(child)], cwd=repo_root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240, check=False,
    )
    projection = json.loads(result_file.read_text(encoding="utf-8"))
    assert type(projection) is dict and set(projection) == {
        "ok", "selection", "stage", "error_code", "error_line",
    }
    assert type(projection["ok"]) is bool and type(projection["selection"]) is bool
    assert type(projection["stage"]) is str and projection["stage"] in {
        "fixture", "app_import", "app_actions", "restart", "failed", "complete",
    }
    assert projection["error_code"] in {None, "assertion", "fixture_value", "io", "unexpected"}
    assert projection["error_line"] is None or (
        type(projection["error_line"]) is int and 1 <= projection["error_line"] <= 1000000
    )
    assert result.returncode == 0
    assert projection == {
        "ok": True, "selection": True, "stage": "complete",
        "error_code": None, "error_line": None,
    }
