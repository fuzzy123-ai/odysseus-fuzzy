from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import FrozenInstanceError
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.auth import AuthManager
from core.database import Base, ChatMessage, Session
from src.private_context_confirmation import (
    ConfirmationError,
    ContextManifest,
    DisclosureBinding,
    ManifestItem,
    SignedConfirmationPair,
)
import src.private_context_confirmation_app as private_confirmation_app
from src.private_context_confirmation_app import (
    ChatConfirmationRepository,
    ContextSelection,
    CookieConfirmationAuthority,
    ForgeSelectionLocator,
    PrivateContextConfirmationApp,
    SourceSelection,
)


class MarkerDomainError(Exception):
    pass


class GuardExit(Exception):
    pass


class ConstructorProducer:
    def capture(self, binding: DisclosureBinding, selection: ContextSelection) -> ContextManifest:
        return ContextManifest(
            tuple(
                ManifestItem(
                    item.source,
                    item.selection_ref,
                    item.revision_ref,
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                    "private",
                    "constructor.producer",
                )
                for item in selection.items
            )
        )


def make_selection(ref: str = "selection.1") -> ContextSelection:
    return ContextSelection((SourceSelection("forge", ref, "revision.1"),))


def make_two_source_selection() -> ContextSelection:
    return ContextSelection(
        (
            SourceSelection("forge", "selection.1", "revision.1"),
            SourceSelection("planning", "selection.2", "revision.2"),
        )
    )


def _forge_locator() -> ForgeSelectionLocator:
    return ForgeSelectionLocator("repo_1", "pv_" + "a" * 32, "src/main.py")


def test_forge_locator_is_immutable_redacted_and_three_arg_compatible() -> None:
    locator = _forge_locator()
    selection = SourceSelection("forge", "selection.1", "revision.1")
    assert selection.forge_locator is None
    assert repr(locator) == "ForgeSelectionLocator(<redacted>)"
    assert "repo_1" not in repr(locator)
    assert "src/main.py" not in repr(locator)
    with pytest.raises(FrozenInstanceError):
        locator.path = "other.py"


def test_forge_locator_rejects_malformed_ids_paths_and_foreign_sources() -> None:
    invalid = (
        ("", "pv_" + "a" * 32, "src/main.py"),
        ("repo/1", "pv_" + "a" * 32, "src/main.py"),
        ("repo_1", "pv_" + "a" * 31, "src/main.py"),
        ("repo_1", "pv_" + "a" * 32, "../secret.txt"),
        ("repo_1", "pv_" + "a" * 32, ".env/secret"),
        ("repo_1", "pv_" + "a" * 32, "a" * 241),
        ("repo_1", "pv_" + "a" * 32, "café.txt"),
    )
    for values in invalid:
        with pytest.raises(ConfirmationError, match="invalid_context"):
            ForgeSelectionLocator(*values)

    class LocatorChild(ForgeSelectionLocator):
        pass

    with pytest.raises(ConfirmationError, match="invalid_context"):
        SourceSelection("forge", "selection.1", "revision.1", LocatorChild(
            "repo_1", "pv_" + "a" * 32, "src/main.py",
        ))
    with pytest.raises(ConfirmationError, match="invalid_context"):
        SourceSelection("planning", "selection.1", "revision.1", _forge_locator())


def test_locator_participates_in_source_selection_equality() -> None:
    plain = SourceSelection("forge", "selection.1", "revision.1")
    located = SourceSelection("forge", "selection.1", "revision.1", _forge_locator())
    assert plain != located
    assert ContextSelection((plain,)) != ContextSelection((located,))


@pytest.fixture
def real_context(tmp_path):
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", "Password-123", is_admin=True)
    token = auth.create_session("alice", "Password-123")
    assert token

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    db.add(
        Session(
            id="chat-owned",
            name="Owned",
            endpoint_url="local",
            model="test",
            owner="alice",
            message_count=0,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    db.commit()
    db.close()
    yield auth, token, session_factory, engine
    engine.dispose()


def make_app(auth, session_factory, producer=None, *, ttl_seconds=300, max_entries=1024):
    return PrivateContextConfirmationApp(
        session_factory,
        auth,
        producer if producer is not None else ConstructorProducer(),
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
    )


def test_real_sqlite_paired_rows_round_trip_and_string_ids(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory)
    selection = make_selection()

    challenge = app.issue_for_selection(token, "chat-owned", selection)
    verified = app.consume_challenge(token, "chat-owned", challenge)
    assert app.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection) == verified

    db = session_factory()
    parent = db.query(Session).filter(Session.id == "chat-owned").one()
    rows = db.query(ChatMessage).filter(ChatMessage.session_id == "chat-owned").order_by(ChatMessage.id).all()
    assert parent.message_count == 2
    assert len(rows) == 2
    assert all(
        row.id.isdecimal()
        and str(int(row.id)) == row.id
        and 1 <= int(row.id) <= (1 << 53) - 1
        for row in rows
    )
    assert len({row.id for row in rows}) == 2
    metadata = json.loads(rows[0].meta_data)
    assert set(metadata) == {"private_context_envelope"}
    assert metadata["private_context_envelope"].startswith("pce1.")
    db.close()


def test_missing_producer_is_fail_closed(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = PrivateContextConfirmationApp(session_factory, auth)
    with pytest.raises(ConfirmationError, match="invalid_context"):
        app.issue_for_selection(token, "chat-owned", make_selection())


def test_repository_denies_calls_outside_trusted_cookie_scope(real_context) -> None:
    auth, _token, session_factory, _engine = real_context
    authority = CookieConfirmationAuthority(auth)
    repository = ChatConfirmationRepository(session_factory, authority)
    with pytest.raises(ConfirmationError, match="invalid_binding"):
        repository.load_confirmation_rows("owner_" + "a" * 32, "chat-owned", 1, 2)


def test_forged_binding_is_rejected_inside_cookie_scope(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    authority = CookieConfirmationAuthority(auth)
    with authority.hold(token, "chat-owned") as binding:
        forged = DisclosureBinding(
            binding.owner_sid,
            binding.chat_session_id,
            "forged-session",
            binding.security_revision,
        )
        with pytest.raises(ConfirmationError, match="invalid_binding"):
            authority.require_current(forged)


def test_hold_rethrows_body_domain_error_and_clears_scope(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    authority = CookieConfirmationAuthority(auth)
    marker = MarkerDomainError("domain failure")
    with pytest.raises(MarkerDomainError) as failure:
        with authority.hold(token, "chat-owned"):
            raise marker
    assert failure.value is marker
    with pytest.raises(ConfirmationError, match="invalid_binding"):
        authority.current_binding()


def test_fork_preserves_binding_family_with_independent_holds(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory)
    family = app._authority
    fork = family.fork()
    assert fork is not family
    assert fork._auth_manager is family._auth_manager is auth
    assert fork._key == family._key
    other_cookie = auth.create_session("alice", "Password-123")
    assert other_cookie and other_cookie != token

    with family.hold(token, "chat-owned") as parent_binding:
        with fork.hold(token, "chat-owned") as child_binding:
            assert child_binding == parent_binding
    with fork.hold(other_cookie, "chat-owned") as other_binding:
        assert other_binding != parent_binding

    with fork.hold(token, "chat-owned") as binding:
        auth._bump_security_action_revision()
        with pytest.raises(ConfirmationError, match="invalid_binding"):
            fork.recheck(binding)


def test_foreign_shared_and_ghost_sessions_are_rejected(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    db = session_factory()
    db.add_all(
        [
            Session(id="chat-shared", name="Shared", endpoint_url="local", model="test", owner=None),
            Session(id="chat-foreign", name="Foreign", endpoint_url="local", model="test", owner="other"),
        ]
    )
    db.commit()
    db.close()
    app = make_app(auth, session_factory)
    for chat_id in ("chat-shared", "chat-foreign", "chat-ghost"):
        with pytest.raises(ConfirmationError, match="invalid_binding"):
            app.issue_for_selection(token, chat_id, make_selection(chat_id))


def test_cookie_security_revision_drift_invalidates_pending_challenge(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory)
    challenge = app.issue_for_selection(token, "chat-owned", make_selection())
    auth._bump_security_action_revision()
    with pytest.raises(ConfirmationError, match="challenge_context_mismatch"):
        app.consume_challenge(token, "chat-owned", challenge)


def test_cookie_guard_entry_revision_drift_is_rejected(real_context, monkeypatch) -> None:
    auth, token, session_factory, _engine = real_context
    authority = CookieConfirmationAuthority(auth)
    original_guard = auth.guard_current_admin_subject

    def drifting_guard(expected_subject_id):
        from contextlib import contextmanager

        @contextmanager
        def entered():
            auth._bump_security_action_revision()
            with original_guard(expected_subject_id) as alias:
                yield alias

        return entered()

    monkeypatch.setattr(auth, "guard_current_admin_subject", drifting_guard)
    with pytest.raises(ConfirmationError, match="invalid_binding"):
        with authority.hold(token, "chat-owned"):
            pass


def test_cookie_guard_exit_failure_is_fixed_invalid_binding_and_scope_closes(real_context, monkeypatch) -> None:
    auth, token, _session_factory, _engine = real_context
    authority = CookieConfirmationAuthority(auth)
    original_guard = auth.guard_current_admin_subject

    def replaced_guard_exit(expected_subject_id):
        from contextlib import contextmanager

        @contextmanager
        def entered():
            with original_guard(expected_subject_id) as alias:
                yield alias
            raise GuardExit("guard cleanup")

        return entered()

    monkeypatch.setattr(auth, "guard_current_admin_subject", replaced_guard_exit)
    with pytest.raises(ConfirmationError, match="invalid_binding"):
        with authority.hold(token, "chat-owned"):
            pass
    with pytest.raises(ConfirmationError, match="invalid_binding"):
        authority.current_binding()


def test_transaction_rollback_leaves_no_paired_rows(real_context) -> None:
    auth, token, session_factory, _engine = real_context

    def failing_factory():
        db = session_factory()

        def fail_commit():
            raise RuntimeError("database rollback")

        db.commit = fail_commit
        return db

    app = make_app(auth, failing_factory)
    challenge = app.issue_for_selection(token, "chat-owned", make_selection())
    with pytest.raises(ConfirmationError, match="persistence_failed"):
        app.consume_challenge(token, "chat-owned", challenge)
    db = session_factory()
    assert db.query(ChatMessage).filter(ChatMessage.session_id == "chat-owned").count() == 0
    db.close()


@pytest.mark.parametrize("mutation", ["copy", "edit", "delete"])
def test_copy_edit_delete_tamper_is_rejected_on_fresh_readback(real_context, mutation: str) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory)
    selection = make_selection()
    verified = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    db = session_factory()
    row = db.query(ChatMessage).filter(ChatMessage.id == str(verified.disclosure_message_id)).one()
    if mutation == "copy":
        row.id = "99"
    elif mutation == "edit":
        row.content = "edited"
    else:
        db.delete(row)
    db.commit()
    db.close()
    with pytest.raises(ConfirmationError, match="readback_failed"):
        app.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection)
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        app.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection)


def test_expected_subset_is_proved_against_fresh_full_manifest(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    selection = make_two_source_selection()
    app = make_app(auth, session_factory)
    verified = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    full = ConstructorProducer().capture(None, selection)
    subset = ContextManifest((full.items[0],))
    assert app.verify_disclosure(
        token,
        "chat-owned",
        verified.disclosure_message_id,
        selection,
        expected_subset=subset,
    ) == verified


@pytest.mark.parametrize("field", [
    "source", "selection_ref", "revision_ref", "boundary_sha256",
    "record_sha256", "payload_sha256", "classification", "provenance_ref",
])
def test_expected_subset_compares_every_manifest_field(real_context, field: str) -> None:
    auth, token, session_factory, _engine = real_context
    selection = make_two_source_selection()
    app = make_app(auth, session_factory)
    verified = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    full = ConstructorProducer().capture(None, selection)
    original = full.items[0]
    values = {
        "source": original.source,
        "selection_ref": original.selection_ref,
        "revision_ref": original.revision_ref,
        "boundary_sha256": original.boundary_sha256,
        "record_sha256": original.record_sha256,
        "payload_sha256": original.payload_sha256,
        "classification": original.classification,
        "provenance_ref": original.provenance_ref,
    }
    values[field] = {
        "source": "knowledge",
        "selection_ref": "foreign-selection",
        "revision_ref": "foreign-revision",
        "boundary_sha256": "4" * 64,
        "record_sha256": "5" * 64,
        "payload_sha256": "6" * 64,
        "classification": "public",
        "provenance_ref": "foreign-provenance",
    }[field]
    forged = ManifestItem(**values)
    subset = ContextManifest(
        tuple(sorted(
            (forged, full.items[1]),
            key=lambda item: json.dumps(item._canonical(), separators=(",", ":"), sort_keys=True),
        ))
    )
    with pytest.raises(ConfirmationError, match="invalid_context"):
        app.verify_disclosure(
            token,
            "chat-owned",
            verified.disclosure_message_id,
            selection,
            expected_subset=subset,
        )


def test_duplicate_metadata_key_is_rejected_on_fresh_readback(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory)
    selection = make_selection()
    verified = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    db = session_factory()
    row = db.query(ChatMessage).filter(ChatMessage.id == str(verified.disclosure_message_id)).one()
    signed_envelope = json.loads(row.meta_data)["private_context_envelope"]
    row.meta_data = (
        '{"private_context_envelope":"pce1.forged",'
        '"private_context_envelope":' + json.dumps(signed_envelope, separators=(",", ":")) + '}'
    )
    db.commit()
    db.close()
    with pytest.raises(ConfirmationError, match="readback_failed"):
        app.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection)
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        app.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection)


def test_deleted_pair_gets_distinct_fresh_ids_for_new_grant(real_context, monkeypatch) -> None:
    auth, token, session_factory, _engine = real_context
    values = iter((100, 101, 200, 201))
    monkeypatch.setattr(private_confirmation_app.secrets, "randbelow", lambda _bound: next(values))
    app = make_app(auth, session_factory)
    selection = make_selection()
    first = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    db = session_factory()
    db.query(ChatMessage).filter(ChatMessage.session_id == "chat-owned").delete(synchronize_session=False)
    db.commit()
    db.close()
    second = app.consume_challenge(
        token,
        "chat-owned",
        app.issue_for_selection(token, "chat-owned", selection),
    )
    assert first.disclosure_message_id != second.disclosure_message_id


def test_one_use_pending_selection_and_process_restart_fail_closed(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    selection = make_selection()
    app = make_app(auth, session_factory)
    challenge = app.issue_for_selection(token, "chat-owned", selection)
    verified = app.consume_challenge(token, "chat-owned", challenge)
    with pytest.raises(ConfirmationError, match="invalid_challenge"):
        app.consume_challenge(token, "chat-owned", challenge)

    restarted = make_app(auth, session_factory)
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        restarted.verify_disclosure(token, "chat-owned", verified.disclosure_message_id, selection)


def test_producer_triples_must_match_selection(real_context) -> None:
    auth, token, session_factory, _engine = real_context

    class WrongProducer:
        def capture(self, binding, selection):
            return ContextManifest(
                (ManifestItem("planning", "other", "revision.1", "1" * 64, "2" * 64, "3" * 64, "private", "p"),)
            )

    app = make_app(auth, session_factory, WrongProducer())
    with pytest.raises(ConfirmationError, match="invalid_context"):
        app.issue_for_selection(token, "chat-owned", make_selection())


def test_pending_capacity_does_not_evict_live_challenge(real_context) -> None:
    auth, token, session_factory, _engine = real_context
    app = make_app(auth, session_factory, max_entries=1)
    first = app.issue_for_selection(token, "chat-owned", make_selection())
    with pytest.raises(ConfirmationError, match="challenge_capacity"):
        app.issue_for_selection(token, "chat-owned", make_selection("selection.2"))
    message_id = app.consume_challenge(token, "chat-owned", first).disclosure_message_id
    assert type(message_id) is int and 1 <= message_id <= (1 << 53) - 1
