"""Focused contract checks for the mixed context content assembler."""
from __future__ import annotations

from tests.test_context_manifest_producer import real_three_domain_context

import base64
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.context_manifest_producer import CombinedContextManifestProducer, CombinedContextSnapshot
from src.context_pack_assembler import ContextPackAssembler, ContextPackAssemblerError, _selection_digest
from src.forge_context_metadata import ForgeContextMetadata
from src.forge_context_page import ForgeContextPageConsumer
from src.harbour_knowledge_active_runtime import KnowledgeContextPageConsumer
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_context_reader import PlanningContextManifestProducer, PlanningContextPageConsumer
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY, DurablePlanningContextSource,
)
from src.planning_revision_store import DurablePlanningRevisionStore, PlanningRevisionStore
from src.project_forge_local import LocalProjectForge
from src.private_context_confirmation_app import (
    ContextSelection, CookieConfirmationAuthority, PrivateContextConfirmationApp,
    SourceSelection,
)
from tests.test_forge_context_page import _empty_forge_selection
from tests.test_harbour_knowledge_context_reader import (
    _public_selection_and_digest, public_context_fixture,
)
from src.harbour_knowledge_active_runtime import KnowledgeContextManifestProducer


def _assembler(context):
    confirmation = PrivateContextConfirmationApp(
        context["session_factory"], context["auth"], context["combined"],
    )
    return ContextPackAssembler(
        manifest_producer=context["combined"],
        confirmation_app=confirmation,
        forge_pages=ForgeContextPageConsumer(
            metadata_provider=context["forge"], local_forge=context["local_forge"],
            auth_manager=context["auth"], session_factory=context["session_factory"],
            confirmation_app=confirmation, manifest_producer=context["combined"],
        ),
        planning_pages=PlanningContextPageConsumer(
            context["planning_producer"], context["auth"], confirmation, context["session_factory"],
        ),
        knowledge_pages=KnowledgeContextPageConsumer(
            context["knowledge_composition"], context["auth"], confirmation, context["session_factory"],
        ),
    ), confirmation


def _public_knowledge_assembler(public_context_fixture, tmp_path):
    auth, _subject, composition, session_factory = public_context_fixture
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = tmp_path / "public-pack-workspace"
    server_projects_root = tmp_path / "public-pack-server-projects"
    local_forge = LocalProjectForge(
        root=tmp_path / "public-pack-forge",
        source_roots=(workspace_root, server_projects_root),
    )
    forge = ForgeContextMetadata(
        local_forge=local_forge,
        registry_path=tmp_path / "public-pack-registry.json",
        auth_manager=auth,
        workspace_root=workspace_root,
        server_projects_root=server_projects_root,
    )
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="public-pack-planning", key_id="public-pack-planning-v1",
        secret=b"public-pack-planning-test-secret-0001",
    )
    legacy = PlanningRevisionStore([], cursor_secret=b"public-pack-legacy-secret")
    durable = DurablePlanningRevisionStore(
        tmp_path / "public-pack-planning", trusted_issuers=(issuer,),
    )
    runtime = AppOwnedPlanningRuntime(
        repo_root=str(repo_root), legacy_store=legacy,
        durable_store=durable, issuer=issuer,
    )
    durable_source = DurablePlanningContextSource(
        runtime=runtime, auth_manager=auth, project_id="harbour-one",
        policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    )
    planning = PlanningContextManifestProducer(
        runtime, auth, None, "harbour-one", durable_source=durable_source,
    )
    knowledge = KnowledgeContextManifestProducer(composition)
    combined = CombinedContextManifestProducer(
        auth_manager=auth, forge=forge, planning=planning, knowledge=knowledge,
    )
    confirmation = PrivateContextConfirmationApp(session_factory, auth, combined)
    assembler = ContextPackAssembler(
        manifest_producer=combined,
        confirmation_app=confirmation,
        forge_pages=ForgeContextPageConsumer(
            metadata_provider=forge, local_forge=local_forge,
            auth_manager=auth, session_factory=session_factory,
            confirmation_app=confirmation, manifest_producer=combined,
        ),
        planning_pages=PlanningContextPageConsumer(
            planning, auth, confirmation, session_factory,
        ),
        knowledge_pages=KnowledgeContextPageConsumer(
            composition, auth, confirmation, session_factory,
        ),
    )
    token, selection = _public_selection_and_digest(
        composition, auth, "public-knowledge-chat",
    )
    return assembler, token, selection, forge, planning


def test_private_three_domain_selection_drains_every_target_to_true_eof(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    read_calls = {"forge": [], "planning": [], "knowledge": []}
    concrete_readers = (
        ("forge", type(assembler._forge_pages)),
        ("planning", type(assembler._planning_pages)),
        ("knowledge", type(assembler._knowledge_pages)),
    )
    for source, reader_type in concrete_readers:
        original = reader_type.read_page

        def counted(self, cookie, request, *, _source=source, _original=original):
            read_calls[_source].append((request.target.selection_ref, request.offset, request.limit))
            return _original(self, cookie, request)

        monkeypatch.setattr(reader_type, "read_page", counted)
    cursor = None
    seen = set()
    page_evidence = {}
    for _ in range(8):
        page = assembler.page(
            context["cookie"], context["chat_id"], context["selection"], 1_000_000,
            cursor=cursor, disclosure_message_id=verified.disclosure_message_id,
        )
        assert page["schema"] == "odysseus.context_pack.v1"
        assert set(page["page"]) == {"target_index", "offset", "end", "total", "whole_sha256", "page_sha256", "byteform", "content_b64"}
        assert len(page["items"]) == 3
        assert page["page"]["byteform"] in {"git_blob", "durable_revision_document", "knowledge_payload"}
        content = base64.b64decode(page["page"]["content_b64"])
        assert page["read_only"] is True
        assert page["authority_granted"] is False
        assert page["send_performed"] is False
        assert len(content) <= 1_000_000
        page_evidence[page["page"]["target_index"]] = (
            page["page"]["offset"], page["page"]["end"],
            page["page"]["total"], len(content),
        )
        seen.add(page["page"]["target_index"])
        if page["complete"]:
            assert page["next_cursor"] == ""
            break
        cursor = page["next_cursor"]
        assert cursor
    else:
        pytest.fail("bounded mixed selection did not reach complete=True")
    assert seen == {0, 1, 2}
    assert set(page_evidence) == {0, 1, 2}
    assert all(
        offset == 0 and end == total == content_length
        for offset, end, total, content_length in page_evidence.values()
    )
    assert {source: len(calls) for source, calls in read_calls.items()} == {
        "forge": 1, "planning": 1, "knowledge": 1,
    }
    expected_refs = {
        item.source: item.selection_ref for item in context["selection"].items
    }
    assert {
        source: {entry[0] for entry in calls}
        for source, calls in read_calls.items()
    } == {source: {expected_refs[source]} for source in expected_refs}
    assert all(
        entry[1] == 0 and entry[2] == 1_000_000
        for calls in read_calls.values() for entry in calls
    )


def test_cursor_is_opaque_and_bound_to_selection_and_owner(real_three_domain_context):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    first = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        disclosure_message_id=verified.disclosure_message_id,
    )
    assert first["next_cursor"]
    continued = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        cursor=first["next_cursor"], disclosure_message_id=verified.disclosure_message_id,
    )
    assert continued["page"]["target_index"] >= first["page"]["target_index"]
    with pytest.raises(ContextPackAssemblerError) as error:
        assembler.page(
            context["cookie"], context["chat_id"], context["selection"], 17,
            cursor=first["next_cursor"][:-1] + ("A" if first["next_cursor"][-1] != "A" else "B"),
            disclosure_message_id=verified.disclosure_message_id,
        )
    assert error.value.code == "invalid_cursor"


def test_empty_cursor_rejected_before_any_concrete_page_read(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    calls = []
    original = type(assembler._forge_pages).read_page

    def unexpected_read(self, cookie, request):
        calls.append(request.target.selection_ref)
        return original(self, cookie, request)

    monkeypatch.setattr(type(assembler._forge_pages), "read_page", unexpected_read)
    selection = ContextSelection((context["forge_selection"],))
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler.page(
            context["cookie"], context["chat_id"], selection, 17, cursor="",
        )
    assert calls == []


def test_real_cursor_rejects_other_session_and_disclosure_before_read(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    challenge = confirmation.issue_for_selection(
        context["cookie"], context["chat_id"], context["selection"],
    )
    verified = confirmation.consume_challenge(
        context["cookie"], context["chat_id"], challenge,
    )
    calls = []
    original = type(assembler._forge_pages).read_page

    def counted_read(self, cookie, request):
        calls.append((cookie, request.target.selection_ref, request.offset))
        return original(self, cookie, request)

    monkeypatch.setattr(type(assembler._forge_pages), "read_page", counted_read)
    first = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        disclosure_message_id=verified.disclosure_message_id,
    )
    assert first["next_cursor"]
    assert len(calls) == 1
    other_cookie = context["auth"].create_session("fuzzy", "Password-123")
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler.page(
            other_cookie, context["chat_id"], context["selection"], 17,
            cursor=first["next_cursor"],
            disclosure_message_id=verified.disclosure_message_id,
        )
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler.page(
            context["cookie"], context["chat_id"], context["selection"], 17,
            cursor=first["next_cursor"],
            disclosure_message_id=verified.disclosure_message_id + 1,
        )
    assert len(calls) == 1
    continued = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        cursor=first["next_cursor"],
        disclosure_message_id=verified.disclosure_message_id,
    )
    assert continued["page"]["target_index"] >= first["page"]["target_index"]
    assert len(calls) == 2


def test_cursor_rejects_duplicate_and_noncanonical_payloads(real_three_domain_context):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    valid = {
        "v": 1, "pack": "sha256:" + "a" * 64, "owner": "owner", "chat": "chat",
        "auth": "auth", "security": 1, "selection": "sha256:" + "b" * 64,
        "disclosure": None, "index": 0, "offset": 0,
    }
    canonical = json.dumps(valid, sort_keys=True, separators=(",", ":")).encode()
    signed = canonical + b"." + __import__("hmac").new(assembler._cursor_key, canonical, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(signed).rstrip(b"=").decode()
    assert assembler._decode_cursor(token)["v"] == 1
    duplicate = b'{"auth":"auth","auth":"auth","chat":"chat","disclosure":null,"index":0,"offset":0,"owner":"owner","pack":"sha256:' + b"a" * 64 + b'","security":1,"selection":"sha256:' + b"b" * 64 + b'","v":1}'
    duplicate_signed = duplicate + b"." + __import__("hmac").new(assembler._cursor_key, duplicate, hashlib.sha256).digest()
    duplicate_token = base64.urlsafe_b64encode(duplicate_signed).rstrip(b"=").decode()
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler._decode_cursor(duplicate_token)


def test_normalized_page_binds_whole_hash_and_rejects_empty_non_eof(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    target = context["forge_selection"]
    with pytest.raises(ContextPackAssemblerError, match="context_unavailable"):
        monkeypatch.setattr(type(assembler._forge_pages), "read_page", lambda *args: SimpleNamespace(
            payload_bytes=b"", total=3, offset=0, end=0,
            whole_sha256="sha256:" + "a" * 64, page_sha256="sha256:" + "0" * 64,
            manifest_digest="0" * 64,
        ))
        fake_item = SimpleNamespace(payload_sha256="a" * 64)
        assembler._read_one(context["cookie"], context["chat_id"], type(context["selection"])((target,)), target, 0, "0" * 64, 0, 17, None, fake_item)


def _cursor_selection(ref_length: int):
    items = tuple(
        SourceSelection(
            "knowledge", f"{index:03d}" + "s" * (ref_length - 3),
            f"{index:03d}" + "r" * (ref_length - 3),
        )
        for index in range(300)
    )
    return ContextSelection(items)


@pytest.mark.parametrize("ref_length", [71, 256])
def test_cursor_round_trip_binds_full_300_target_selection(real_three_domain_context, ref_length):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    selection = _cursor_selection(ref_length)
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        value = {
            "v": 1, "pack": "sha256:" + "a" * 64,
            "owner": binding.owner_sid, "chat": context["chat_id"],
            "auth": binding.auth_session_binding, "security": binding.security_revision,
            "selection": _selection_digest(selection),
            "disclosure": None, "index": 299, "offset": 17,
        }
        token = assembler._encode_cursor(value)
        assert len(token.encode("ascii")) <= 16_384
        decoded = assembler._decode_cursor(token)
        assert decoded["selection"] == value["selection"]
        assert assembler._start_position(
            decoded, value["pack"], binding, context["chat_id"], selection, None,
        ) == (299, 17)
        changed = type(selection)(selection.items[:-1] + (SourceSelection(
            "knowledge", "999" + "s" * (ref_length - 3),
            "999" + "r" * (ref_length - 3),
        ),))
        with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
            assembler._start_position(
                decoded, value["pack"], binding, context["chat_id"], changed, None,
            )


def test_cursor_encoder_rejects_oversized_nonselection_fields(real_three_domain_context):
    assembler, _confirmation = _assembler(real_three_domain_context)
    value = {
        "v": 1, "pack": "sha256:" + "a" * 64, "owner": "x" * 20_000, "chat": "chat",
        "auth": "auth", "security": 1, "selection": "sha256:" + "b" * 64,
        "disclosure": None, "index": 0, "offset": 0,
    }
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler._encode_cursor(value)


@pytest.mark.parametrize("field_value", [
    ["knowledge", "source", "revision"], "sha256:" + "A" * 64,
    "sha256:" + "c" * 63, "sha256x" + "d" * 64,
])
def test_cursor_selection_digest_forms_fail_before_reader(real_three_domain_context, field_value):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    selection = context["selection"]
    binding = SimpleNamespace(owner_sid="owner", auth_session_binding="auth", security_revision=7)
    value = {
        "v": 1, "pack": "sha256:" + "a" * 64, "owner": "owner", "chat": "chat",
        "auth": "auth", "security": 7, "selection": field_value,
        "disclosure": None, "index": 0, "offset": 0,
    }
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    signed = payload + b"." + hmac.new(assembler._cursor_key, payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(signed).rstrip(b"=").decode()
    decoded = assembler._decode_cursor(token)
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler._start_position(decoded, value["pack"], binding, "chat", selection, None)


@pytest.mark.parametrize("scope", [
    ("wrong-owner", "auth", 7, "chat", None),
    ("owner", "wrong-auth", 7, "chat", None),
    ("owner", "auth", 8, "chat", None),
    ("owner", "auth", 7, "wrong-chat", None),
    ("owner", "auth", 7, "chat", 99),
])
def test_cursor_scope_and_disclosure_changes_fail_closed(real_three_domain_context, scope):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    selection = context["selection"]
    binding = SimpleNamespace(owner_sid="owner", auth_session_binding="auth", security_revision=7)
    value = {
        "v": 1, "pack": "sha256:" + "a" * 64, "owner": "owner", "chat": "chat",
        "auth": "auth", "security": 7, "selection": _selection_digest(selection),
        "disclosure": None, "index": 0, "offset": 0,
    }
    decoded = assembler._decode_cursor(assembler._encode_cursor(value))
    wrong_binding = SimpleNamespace(owner_sid=scope[0], auth_session_binding=scope[1], security_revision=scope[2])
    with pytest.raises(ContextPackAssemblerError, match="invalid_cursor"):
        assembler._start_position(decoded, value["pack"], wrong_binding, scope[3], selection, scope[4])


@pytest.mark.parametrize("mutation", [
    {"offset": 1, "end": 1},
    {"page_sha256": "sha256:" + "A" * 64},
    {"whole_sha256": "sha256:" + "b" * 64},
    {"byteform": "forged"},
    {"manifest_digest": "1" * 64},
])
def test_instrumented_native_page_boundary_rejects_hostile_fields(real_three_domain_context, monkeypatch, mutation):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    target = context["forge_selection"]
    selection = ContextSelection((target,))
    good = {
        "payload_bytes": b"x", "total": 1, "offset": 0, "end": 1,
        "whole_sha256": "sha256:" + "a" * 64,
        "page_sha256": "sha256:" + hashlib.sha256(b"x").hexdigest(),
        "manifest_digest": "0" * 64, "byteform": "git_blob",
    }
    good.update(mutation)
    monkeypatch.setattr(type(assembler._forge_pages), "read_page", lambda *args: SimpleNamespace(**good))
    with pytest.raises(ContextPackAssemblerError, match="context_unavailable"):
        assembler._read_one(context["cookie"], context["chat_id"], selection, target, 0, "0" * 64, 0, 17, None, SimpleNamespace(payload_sha256="a" * 64))


def test_reader_failure_returns_no_content(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, _confirmation = _assembler(context)
    monkeypatch.setattr(type(assembler._forge_pages), "read_page", lambda *args: (_ for _ in ()).throw(RuntimeError("reader")))
    with pytest.raises(ContextPackAssemblerError, match="context_unavailable"):
        assembler.page(context["cookie"], context["chat_id"], ContextSelection((context["forge_selection"],)), 17)


def test_noncurrent_target_drift_invalidates_pack_without_returning_page(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    original = type(context["combined"]).capture_snapshot
    calls = {"count": 0}

    def drift(self, binding, selection):
        snapshot = original(self, binding, selection)
        calls["count"] += 1
        if calls["count"] == 2:
            digests = list(snapshot.target_manifest_digests)
            digests[-1] = "1" * 64
            return CombinedContextSnapshot(snapshot.items, tuple(digests))
        return snapshot

    monkeypatch.setattr(type(context["combined"]), "capture_snapshot", drift)
    with pytest.raises(ContextPackAssemblerError, match="context_changed"):
        assembler.page(
            context["cookie"], context["chat_id"], context["selection"], 17,
            disclosure_message_id=verified.disclosure_message_id,
        )


def test_real_empty_forge_target_reaches_honest_eof(real_three_domain_context, tmp_path):
    context = real_three_domain_context
    selection, manifest = _empty_forge_selection(context, tmp_path)
    assembler, _confirmation = _assembler(context)
    selected = type(context["selection"])((selection,))
    page = assembler.page(context["cookie"], context["chat_id"], selected, 17)
    assert page["complete"] is True
    assert page["next_cursor"] == ""
    assert page["page"]["offset"] == page["page"]["end"] == page["page"]["total"] == 0
    assert page["page"]["whole_sha256"] == "sha256:" + hashlib.sha256(b"").hexdigest()


def test_public_knowledge_reader_is_public_and_requires_no_disclosure(public_context_fixture):
    auth, _subject, composition, session_factory = public_context_fixture
    token, selection = _public_selection_and_digest(composition, auth, "public-knowledge-chat")
    from src.harbour_knowledge_active_runtime import KnowledgeContextManifestProducer, KnowledgeContextPageConsumer, KnowledgeContextPageRequest
    producer = KnowledgeContextManifestProducer(composition)
    confirmation = PrivateContextConfirmationApp(session_factory, auth, producer)
    consumer = KnowledgeContextPageConsumer(composition, auth, confirmation, session_factory)
    with consumer._authority.hold(token, "public-knowledge-chat") as binding:
        manifest = producer.capture_metadata(binding, selection)
    assert manifest.items[0].classification == "public"
    page = consumer.read_page(token, KnowledgeContextPageRequest(selection, selection.items[0], "public-knowledge-chat", manifest.digest, 0, 1_000_000))
    assert page.payload_bytes and page.end == page.total


def test_public_knowledge_selection_runs_through_assembler(public_context_fixture, tmp_path, monkeypatch):
    assembler, token, selection, forge, planning = _public_knowledge_assembler(
        public_context_fixture, tmp_path,
    )

    def unexpected_forge_read(*args, **kwargs):
        raise AssertionError("unselected Forge provider was read")

    def unexpected_planning_read(*args, **kwargs):
        raise AssertionError("unselected Planning provider was read")

    monkeypatch.setattr(type(forge), "capture_version_metadata", unexpected_forge_read)
    monkeypatch.setattr(type(planning), "capture_metadata", unexpected_planning_read)
    page = assembler.page(token, "public-knowledge-chat", selection, 1_000_000)
    assert page["complete"] is True
    assert page["next_cursor"] == ""
    assert len(page["items"]) == 1
    assert page["items"][0]["source"] == "knowledge"
    assert page["items"][0]["classification"] == "public"
    assert page["page"]["byteform"] == "knowledge_payload"
    assert base64.b64decode(page["page"]["content_b64"])
    assert page["read_only"] is True
    assert page["authority_granted"] is False
    assert page["send_performed"] is False
    assert "owner_sid" not in json.dumps(page)


def test_native_source_tombstone_between_read_and_recapture_denies_without_page(real_three_domain_context, monkeypatch):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    selection = ContextSelection((context["knowledge_target"],))
    challenge = confirmation.issue_for_selection(
        context["cookie"], context["chat_id"], selection,
    )
    verified = confirmation.consume_challenge(
        context["cookie"], context["chat_id"], challenge,
    )
    composition = context["knowledge_composition"]
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        subject, store, principal, *_ = composition._context_runtime(binding)
        record = store.capture_index_snapshot(principal, owner_id=subject).records[0]
        head = store.get_head(principal, owner_id=subject, knowledge_id=record.knowledge_id)
    original = type(assembler._knowledge_pages).read_page
    mutated = {"done": False}

    def read_then_tombstone(self, cookie, request):
        page = original(self, cookie, request)
        if not mutated["done"]:
            with CookieConfirmationAuthority(context["auth"]).hold(
                context["cookie"], context["chat_id"],
            ) as mutation_binding:
                _subject, mutation_store, mutation_principal, *_ = composition._context_runtime(mutation_binding)
                mutation_store.tombstone(
                    mutation_principal, owner_id=subject,
                    knowledge_id=record.knowledge_id, expected_head=head,
                    idempotency_key="context-pack-native-drift",
                )
            mutated["done"] = True
        return page

    monkeypatch.setattr(type(assembler._knowledge_pages), "read_page", read_then_tombstone)
    with pytest.raises(ContextPackAssemblerError) as error:
        assembler.page(
            context["cookie"], context["chat_id"], selection, 1_000_000,
            disclosure_message_id=verified.disclosure_message_id,
        )
    assert mutated["done"] is True
    assert error.value.code in {"context_changed", "context_unavailable"}


def test_public_only_pack_does_not_require_private_manifest(real_three_domain_context):
    context = real_three_domain_context
    selection = type(context["selection"])((context["forge_selection"],))
    assembler, _confirmation = _assembler(context)
    page = assembler.page(context["cookie"], context["chat_id"], selection, 17)
    assert page["items"][0]["classification"] == "public"
    assert page["complete"] is True or page["next_cursor"]
    assert "owner_sid" not in json.dumps(page)


def test_pack_identity_is_stable_for_same_full_selection(real_three_domain_context):
    context = real_three_domain_context
    assembler, confirmation = _assembler(context)
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    first = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        disclosure_message_id=verified.disclosure_message_id,
    )
    repeated = assembler.page(
        context["cookie"], context["chat_id"], context["selection"], 17,
        disclosure_message_id=verified.disclosure_message_id,
    )
    assert repeated["pack_id"] == first["pack_id"]
