"""Native consent boundaries staged for later additive test integration.

The local fixture wrapper changes only setup order: it seeds the genuine durable
Planning store before the existing consent fixture constructs its application
service.  The browser challenge, acknowledgement, permit, page execution, and
cleanup paths remain the existing native implementation.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from routes.auth_routes import SESSION_COOKIE
from src.planning_definition_contract import compute_roadmap_content_hash
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
)
import tests.test_app_mcp_private_context_confirmation as consent_tests


_CANONICAL_PAYLOAD_LIMIT = 4 * 1024 * 1024
_PAGE_LIMIT = 16_384
_BASE_NATIVE_MCP_FIXTURE = consent_tests.native_mcp_fixture
_BASE_CONSENT_APP_FIXTURE = consent_tests.consent_app


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _set_approved_revision(document: dict[str, object], revision: int, title: str) -> None:
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = revision
    roadmap["title"] = title
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {
            "revision": revision,
            "content_hash": roadmap["content_hash"],
        },
    }
    document["project"]["draft_refs"] = []


def _exact_size_constraints(document: dict[str, object], size: int) -> bytes:
    """Fill a roadmap with unique, non-sensitive text to an exact byte size."""

    roadmap = document["roadmaps"][0]
    roadmap["constraints"] = []
    _set_approved_revision(
        document,
        roadmap["revision"],
        "Native canonical payload boundary",
    )
    baseline = _canonical(document)
    deficit = size - len(baseline)
    assert deficit > 0

    # Replacing [] with N JSON strings adds sum(lengths) + 3*N - 1 bytes.
    item_count = (deficit + 4003) // 4003
    total_text_bytes = deficit - 3 * item_count + 1
    assert item_count <= total_text_bytes <= 4000 * item_count
    width, remainder = divmod(total_text_bytes, item_count)
    constraints = []
    for index in range(item_count):
        item_length = width + (1 if index < remainder else 0)
        prefix = f"native-boundary-padding-{index:04d}-"
        assert len(prefix) <= item_length <= 4000
        constraints.append(prefix + "x" * (item_length - len(prefix)))
    assert len(constraints) == len(set(constraints))
    roadmap["constraints"] = constraints
    _set_approved_revision(
        document,
        roadmap["revision"],
        "Native canonical payload boundary",
    )
    payload = _canonical(document)
    assert len(payload) == size
    return payload


def _refresh_selection_and_oracle(fixture: dict[str, object]):
    """Resolve the accepted descriptor, then read its opaque id from the store."""

    with CookieConfirmationAuthority(fixture["auth"]).hold(
        fixture["cookie"], fixture["chat_id"],
    ) as binding:
        planning_items = fixture["durable_source"].discover(binding)
        selection = ContextSelection(tuple(sorted(
            (fixture["forge_selection"], *planning_items, fixture["knowledge_target"]),
            key=lambda item: (item.source, item.selection_ref, item.revision_ref),
        )))
        fixture["selection"] = selection
        target = next(item for item in selection.items if item.source == "planning")
        descriptor = fixture["durable_source"].resolve(
            binding, ContextSelection((target,)),
        )[0]
        store = fixture["planning_producer"]._runtime.durable_store
        payload = store.read_payload(
            fixture["principal"],
            owner_id="fuzzy",
            revision_id=descriptor.durable_revision_id,
        )
    assert payload == _canonical(json.loads(payload.decode("utf-8")))
    assert descriptor.durable_payload_sha256 == "sha256:" + hashlib.sha256(payload).hexdigest()
    head = store.get_head(
        fixture["principal"],
        owner_id="fuzzy",
        project_id=descriptor.project_id,
        roadmap_id=descriptor.roadmap_id,
    )
    assert descriptor.durable_revision_id == head.current_revision_id
    assert descriptor.durable_revision_id == head.approved_revision_id
    return descriptor, payload


def _append_and_approve(
    fixture: dict[str, object], document: dict[str, object], *, key: str,
) -> str:
    store = fixture["planning_producer"]._runtime.durable_store
    principal = fixture["principal"]
    roadmap = document["roadmaps"][0]
    head = store.get_head(
        principal,
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap["roadmap_id"],
    )
    appended = store.append_revision(
        principal,
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap["roadmap_id"],
        payload_bytes=_canonical(document),
        expected_head=head,
        idempotency_key=key + "-append",
    )
    assert appended.new_revision_id is not None
    approved = store.approve_revision(
        principal,
        owner_id="fuzzy",
        project_id="harbour-one",
        roadmap_id=roadmap["roadmap_id"],
        revision_id=appended.new_revision_id,
        expected_head=appended.after_head,
        idempotency_key=key + "-approve",
    )
    assert approved.after_head.current_revision_id == appended.new_revision_id
    assert approved.after_head.approved_revision_id == appended.new_revision_id
    return appended.new_revision_id


def _seed_exact_limit(fixture: dict[str, object]) -> None:
    descriptor, parent_payload = _refresh_selection_and_oracle(fixture)
    document = json.loads(parent_payload.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    assert roadmap["revision_state"] == "approved"
    _set_approved_revision(
        document,
        roadmap["revision"] + 1,
        "Native canonical payload boundary",
    )
    payload = _exact_size_constraints(document, _CANONICAL_PAYLOAD_LIMIT)
    assert payload == _canonical(document)
    revision_id = _append_and_approve(
        fixture, document, key="native-browser-exact-4mib",
    )
    final_descriptor, oracle = _refresh_selection_and_oracle(fixture)
    assert final_descriptor.roadmap_id == descriptor.roadmap_id
    assert final_descriptor.durable_revision_id == revision_id
    assert oracle == payload
    fixture["boundary_oracle"] = oracle


def _seed_deep_accepted_chain(fixture: dict[str, object]) -> None:
    descriptor, parent_payload = _refresh_selection_and_oracle(fixture)
    document = json.loads(parent_payload.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    for index in range(33):
        _set_approved_revision(
            document,
            roadmap["revision"] + 1,
            f"Native accepted ancestry boundary {index + 1:02d}",
        )
        _append_and_approve(
            fixture,
            document,
            key=f"native-browser-deep-ancestry-{index + 1:02d}",
        )

    final_descriptor, oracle = _refresh_selection_and_oracle(fixture)
    store = fixture["planning_producer"]._runtime.durable_store
    owner, _batch, records, heads = store.read_subject_bound_accepted_migration_records(
        fixture["principal"], project_id="harbour-one",
    )
    assert owner == "fuzzy"
    matching_documents = [
        item
        for record_owner, item, _opaque_revision_ref in records
        if record_owner == owner
        and item["roadmaps"][0]["roadmap_id"] == final_descriptor.roadmap_id
    ]
    matching_heads = [
        head for head in heads if head["roadmap_id"] == final_descriptor.roadmap_id
    ]
    assert len(matching_heads) == 1
    ancestor_count = len(matching_documents) - 1
    assert ancestor_count > 32
    assert sorted(item["roadmaps"][0]["revision"] for item in matching_documents) == list(
        range(1, len(matching_documents) + 1)
    )
    assert matching_heads[0]["current_revision_id"] == final_descriptor.durable_revision_id
    assert matching_heads[0]["approved_revision_id"] == final_descriptor.durable_revision_id
    assert final_descriptor.roadmap_id == descriptor.roadmap_id
    fixture["boundary_oracle"] = oracle
    fixture["boundary_ancestor_count"] = ancestor_count


@pytest.fixture
def boundary_consent_app(tmp_path, monkeypatch, request):
    mode = request.param
    assert mode in {"exact-limit", "deep-ancestry"}

    def seeded_native_mcp_body(inner_tmp_path, inner_monkeypatch):
        base_body = _BASE_NATIVE_MCP_FIXTURE.__wrapped__(
            inner_tmp_path, inner_monkeypatch,
        )
        fixture = next(base_body)
        try:
            if mode == "exact-limit":
                _seed_exact_limit(fixture)
            else:
                _seed_deep_accepted_chain(fixture)
            yield fixture
        finally:
            try:
                next(base_body)
            except StopIteration:
                pass

    monkeypatch.setattr(
        consent_tests,
        "native_mcp_fixture",
        SimpleNamespace(__wrapped__=seeded_native_mcp_body),
    )
    return _BASE_CONSENT_APP_FIXTURE.__wrapped__(tmp_path, monkeypatch, request)


def _browser_ack(client: TestClient) -> dict[str, object]:
    fixture = client.app.state.fixture
    prepared = consent_tests._assert_success(client.post(
        "/test/prepare",
        headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
    ))
    request_id = prepared.json()["request_id"]
    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    challenge = consent_tests._assert_success(client.post(
        "/api/context/mcp/challenge",
        headers=consent_tests._headers(),
        json={"request_id": request_id},
    )).json()
    response = consent_tests._assert_success(client.post(
        "/api/chat",
        headers=consent_tests._headers(),
        json={
            "message": "/mcp-context-disclose " + challenge["token"],
            "session": "chat-owned",
        },
    ))
    receipt = response.json()
    assert receipt["request_id"] == request_id
    assert receipt["user_message_id"] != receipt["acknowledgement_message_id"]
    assert challenge["token"] not in response.text
    assert consent_tests._assert_success(client.post("/test/readback")).json() == {
        "pair_verified": True,
        "owned_row_count": 2,
    }
    return fixture


def _assert_consumed_lifecycle(client: TestClient) -> None:
    lifecycle = consent_tests._assert_success(
        client.post("/test/substitution_readback")
    ).json()
    assert lifecycle["request_absent"] is True
    assert lifecycle["all42_scrubbed"] is True
    assert lifecycle["secondary_unchanged"] is True
    assert lifecycle["permits_empty"] is True
    assert lifecycle["exchanges_empty"] is True
    assert lifecycle["completions_empty"] is True
    assert consent_tests._assert_success(client.post("/test/pair_readback")).json() == {
        "pair_count": 2,
        "parent_count": 2,
        "roles_fixed": True,
    }


@pytest.mark.parametrize("boundary_consent_app", ["exact-limit"], indirect=True)
def test_native_browser_pages_exact_four_mib_accepted_canonical_revision(
    boundary_consent_app,
):
    with TestClient(boundary_consent_app) as client:
        fixture = _browser_ack(client)
        oracle = fixture["boundary_oracle"]
        assert len(oracle) == _CANONICAL_PAYLOAD_LIMIT
        expected_whole = "sha256:" + hashlib.sha256(oracle).hexdigest()
        consent_tests._assert_success(client.post(
            "/test/arm_page_boundary", json={"mode": "partial"},
        ))
        page = consent_tests._assert_success(client.post("/test/consume")).json()
        expected_offset = 1
        expected_end = min(len(oracle), expected_offset + _PAGE_LIMIT)
        expected_page = oracle[expected_offset:expected_end]
        assert page["total_bytes"] == len(oracle)
        assert page["offset"] == expected_offset
        assert page["end"] == expected_end
        assert page["page_bytes"] == len(expected_page) <= _PAGE_LIMIT
        assert page["whole_sha256"] == expected_whole
        assert page["page_sha256"] == "sha256:" + hashlib.sha256(expected_page).hexdigest()
        assert page["oracle_equal"] is True
        assert page["manifest_equal"] is True
        assert page["durable_revision_document"] is True
        assert page["receipt_opaque"] is True
        assert page["completion_consumed"] is True
        assert page["completion_replay"] == "permit_invalid"
        assert page["permit_replay"] == "permit_invalid"
        _assert_consumed_lifecycle(client)


@pytest.mark.parametrize("boundary_consent_app", ["deep-ancestry"], indirect=True)
def test_native_browser_pages_head_with_more_than_32_accepted_ancestors_at_eof(
    boundary_consent_app,
):
    with TestClient(boundary_consent_app) as client:
        fixture = _browser_ack(client)
        oracle = fixture["boundary_oracle"]
        assert fixture["boundary_ancestor_count"] > 32
        expected_whole = "sha256:" + hashlib.sha256(oracle).hexdigest()
        consent_tests._assert_success(client.post(
            "/test/arm_page_boundary", json={"mode": "eof"},
        ))
        page = consent_tests._assert_success(client.post("/test/consume")).json()
        assert page["total_bytes"] == len(oracle)
        assert page["offset"] == page["end"] == len(oracle)
        assert page["page_bytes"] == 0
        assert page["whole_sha256"] == expected_whole
        assert page["page_sha256"] == "sha256:" + hashlib.sha256(b"").hexdigest()
        assert page["oracle_equal"] is True
        assert page["manifest_equal"] is True
        assert page["durable_revision_document"] is True
        assert page["receipt_opaque"] is True
        assert page["completion_consumed"] is True
        assert page["completion_replay"] == "permit_invalid"
        assert page["permit_replay"] == "permit_invalid"
        _assert_consumed_lifecycle(client)
