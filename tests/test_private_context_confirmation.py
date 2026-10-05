from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import traceback

import pytest
import src.private_context_confirmation as confirmation_module

from src.private_context_confirmation import (
    Challenge,
    ConfirmationError,
    ContextManifest,
    DisclosureBinding,
    ManifestItem,
    PrivateContextConfirmation,
    StoredConfirmationPair,
)


class Repository:
    def __init__(self) -> None:
        self.rows: StoredConfirmationPair | None = None
        self.next_id = 10
        self.fail_insert = False
        self.return_missing = False
        self.load_calls = 0
        self.repository_error = None

    def insert_confirmation(self, request, seal_pair) -> None:
        if self.fail_insert:
            raise RuntimeError("rollback")
        if self.repository_error is not None:
            raise self.repository_error
        user_id, acknowledgement_id = self.next_id, self.next_id + 1
        self.next_id += 2
        pair = seal_pair(user_id, acknowledgement_id)
        self.rows = StoredConfirmationPair(
            request.sid,
            request.chat_session_id,
            user_id,
            acknowledgement_id,
            "user",
            "assistant",
            request.user_content,
            request.acknowledgement_content,
            pair.user_envelope,
            pair.acknowledgement_envelope,
        )

    def load_confirmation_rows(self, sid, chat_session_id, user_message_id, acknowledgement_message_id):
        self.load_calls += 1
        if self.repository_error is not None:
            raise self.repository_error
        if self.return_missing or self.rows is None:
            return None
        return self.rows


def binding() -> DisclosureBinding:
    return DisclosureBinding("owner_0123456789abcdef0123456789abcdef", "chat.1", "session.1", 4)


def manifest() -> ContextManifest:
    item = ManifestItem(
        "forge", "selection.1", "revision.1", "1" * 64, "2" * 64, "3" * 64, "private", "provenance.1"
    )
    return ContextManifest((item,))


def consume(manager, challenge, current_binding=None, current_manifest=None):
    return manager.consume_challenge(challenge.token, current_binding or binding(), current_manifest or manifest())


def test_round_trip_reloads_rows_for_every_verification() -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    assert manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest()) == verified
    assert manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest()) == verified
    assert repository.load_calls == 3


def test_wrong_context_does_not_consume_the_challenge() -> None:
    manager = PrivateContextConfirmation(Repository())
    challenge = manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="challenge_context_mismatch"):
        consume(manager, challenge, DisclosureBinding("owner_0123456789abcdef0123456789abcdef", "other", "session.1", 4))
    assert consume(manager, challenge).disclosure_message_id > 0


@pytest.mark.parametrize("mutation", ["content", "envelope", "role", "ids"])
def test_row_tampering_invalidates_active_disclosure(mutation: str) -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    row = repository.rows
    assert row is not None
    values = {name: getattr(row, name) for name in row.__dataclass_fields__}
    if mutation == "content":
        values["user_content"] = "changed"
    elif mutation == "envelope":
        values["user_envelope"] = "pce1." + "0" * 64
    elif mutation == "role":
        values["acknowledgement_role"] = "user"
    else:
        values["acknowledgement_message_id"] += 10
    repository.rows = StoredConfirmationPair(**values)
    with pytest.raises(ConfirmationError, match="readback_failed"):
        manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest())
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest())


def test_insert_failure_and_missing_readback_leave_challenge_spent() -> None:
    repository = Repository()
    repository.fail_insert = True
    manager = PrivateContextConfirmation(repository)
    challenge = manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="persistence_failed"):
        consume(manager, challenge)
    with pytest.raises(ConfirmationError, match="challenge_spent"):
        consume(manager, challenge)

    repository = Repository()
    repository.return_missing = True
    manager = PrivateContextConfirmation(repository)
    challenge = manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="readback_failed"):
        consume(manager, challenge)
    with pytest.raises(ConfirmationError, match="challenge_spent"):
        consume(manager, challenge)


def test_concurrent_consumers_create_exactly_one_pair() -> None:
    manager = PrivateContextConfirmation(Repository())
    challenge = manager.issue_challenge(binding(), manifest())
    outcomes = []

    def attempt() -> None:
        try:
            outcomes.append(("ok", consume(manager, challenge)))
        except ConfirmationError as error:
            outcomes.append(("error", error.code))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [outcome[0] for outcome in outcomes].count("ok") == 1
    assert {outcome[1] for outcome in outcomes if outcome[0] == "error"} == {"challenge_spent"}


def test_concurrent_repository_callbacks_can_seal_only_once() -> None:
    class CallbackRaceRepository(Repository):
        def insert_confirmation(self, request, seal_pair) -> None:
            barrier = threading.Barrier(2)
            results = []

            def invoke(user_id: int, acknowledgement_id: int) -> None:
                barrier.wait()
                try:
                    results.append((user_id, acknowledgement_id, seal_pair(user_id, acknowledgement_id)))
                except BaseException as error:
                    results.append(error)

            threads = [
                threading.Thread(target=invoke, args=(20, 21)),
                threading.Thread(target=invoke, args=(22, 23)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            successes = [result for result in results if isinstance(result, tuple)]
            assert len(successes) == 1
            user_id, acknowledgement_id, pair = successes[0]
            self.rows = StoredConfirmationPair(
                request.sid,
                request.chat_session_id,
                user_id,
                acknowledgement_id,
                "user",
                "assistant",
                request.user_content,
                request.acknowledgement_content,
                pair.user_envelope,
                pair.acknowledgement_envelope,
            )

    repository = CallbackRaceRepository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    assert verified.disclosure_message_id in {20, 22}


def test_forged_sealer_transport_is_opaque_to_error_boundary() -> None:
    class ForgedTransport(confirmation_module._SealerFailure):
        @property
        def code(self):
            raise AssertionError("forged transport code was inspected")

    class ForgedRepository(Repository):
        def insert_confirmation(self, request, seal_pair) -> None:
            raise ForgedTransport()

    manager = PrivateContextConfirmation(ForgedRepository())
    with pytest.raises(ConfirmationError, match="persistence_failed") as raised:
        consume(manager, manager.issue_challenge(binding(), manifest()))
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("repository_error", [RuntimeError("provider-secret"), ConfirmationError("readback_failed")])
def test_repository_errors_map_to_closed_codes_without_raw_traceback(repository_error) -> None:
    repository = Repository()
    repository.repository_error = repository_error
    manager = PrivateContextConfirmation(repository)
    with pytest.raises(ConfirmationError) as raised:
        consume(manager, manager.issue_challenge(binding(), manifest()))
    assert raised.value.code == "persistence_failed"
    rendered = "".join(traceback.format_exception(raised.value))
    assert "provider-secret" not in rendered

    with pytest.raises(ValueError):
        ConfirmationError("repository-controlled-code")


def test_repository_error_during_active_verification_is_closed() -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    repository.repository_error = ConfirmationError("readback_failed")
    with pytest.raises(ConfirmationError, match="disclosure_unverified") as raised:
        manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest())
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("drift", ["auth_session", "security_revision", "manifest"])
def test_auth_security_and_manifest_drift_does_not_consume_challenge(drift: str) -> None:
    manager = PrivateContextConfirmation(Repository())
    challenge = manager.issue_challenge(binding(), manifest())
    if drift == "auth_session":
        current_binding = DisclosureBinding(binding().owner_sid, binding().chat_session_id, "session.2", 4)
        current_manifest = manifest()
    elif drift == "security_revision":
        current_binding = DisclosureBinding(binding().owner_sid, binding().chat_session_id, binding().auth_session_binding, 5)
        current_manifest = manifest()
    else:
        current_binding = binding()
        current_manifest = ContextManifest(
            (ManifestItem("forge", "selection.1", "revision.1", "1" * 64, "2" * 64, "4" * 64, "private", "provenance.1"),)
        )
    with pytest.raises(ConfirmationError, match="challenge_context_mismatch"):
        consume(manager, challenge, current_binding, current_manifest)
    assert consume(manager, challenge).disclosure_message_id > 0


@pytest.mark.parametrize("mutation", ["delete", "acknowledgement"])
def test_delete_or_acknowledgement_tamper_invalidates_active_grant(mutation: str) -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    if mutation == "delete":
        repository.rows = None
    else:
        row = repository.rows
        assert row is not None
        values = {name: getattr(row, name) for name in row.__dataclass_fields__}
        values["acknowledgement_content"] = "tampered"
        repository.rows = StoredConfirmationPair(**values)
    with pytest.raises(ConfirmationError, match="readback_failed"):
        manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest())
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        manager.verify_disclosure(verified.disclosure_message_id, binding(), manifest())


def test_active_grant_does_not_survive_process_restart() -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    verified = consume(manager, manager.issue_challenge(binding(), manifest()))
    restarted = PrivateContextConfirmation(repository)
    with pytest.raises(ConfirmationError, match="disclosure_unverified"):
        restarted.verify_disclosure(verified.disclosure_message_id, binding(), manifest())


def test_expiry_and_backward_wall_clock_fail_closed() -> None:
    manager = PrivateContextConfirmation(Repository(), ttl_seconds=1)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    manager._wall_clock = lambda: now
    manager._monotonic_clock = lambda: 10.0
    challenge = manager.issue_challenge(binding(), manifest())
    manager._wall_clock = lambda: now + timedelta(seconds=1)
    with pytest.raises(ConfirmationError, match="invalid_challenge"):
        consume(manager, challenge)

    manager = PrivateContextConfirmation(Repository())
    manager._wall_clock = lambda: now
    manager._monotonic_clock = lambda: 10.0
    challenge = manager.issue_challenge(binding(), manifest())
    manager._wall_clock = lambda: now - timedelta(microseconds=1)
    with pytest.raises(ConfirmationError, match="invalid_challenge"):
        consume(manager, challenge)


def test_monotonic_deadline_equality_expires_challenge() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monotonic = [10.0]
    repository = Repository()
    manager = PrivateContextConfirmation(repository, ttl_seconds=1)
    manager._wall_clock = lambda: now
    manager._monotonic_clock = lambda: monotonic[0]
    original_insert = repository.insert_confirmation

    def advance_after_insert(request, sealer) -> None:
        original_insert(request, sealer)
        monotonic[0] = 11.0

    repository.insert_confirmation = advance_after_insert
    challenge = manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="challenge_expired"):
        consume(manager, challenge)


def test_restart_and_token_tamper_do_not_grant() -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository)
    challenge = manager.issue_challenge(binding(), manifest())
    replacement = "1" if challenge.token[-1] != "1" else "2"
    with pytest.raises(ConfirmationError, match="invalid_challenge"):
        consume(manager, Challenge(challenge.token[:-1] + replacement, challenge.issued_at, challenge.expires_at))
    other = PrivateContextConfirmation(repository)
    with pytest.raises(ConfirmationError, match="invalid_challenge"):
        consume(other, challenge)


def test_manifest_and_constructor_limits_are_strict() -> None:
    with pytest.raises(ConfirmationError, match="invalid_limits"):
        PrivateContextConfirmation(Repository(), ttl_seconds=True)
    with pytest.raises(ConfirmationError, match="invalid_manifest"):
        ContextManifest(())
    with pytest.raises(ConfirmationError, match="invalid_manifest"):
        ContextManifest((ManifestItem("forge", "a", "b", "1" * 64, "2" * 64, "3" * 64, "public", "c"),))


def test_capacity_only_prunes_expired_entries_and_nonce_collisions_do_not_evict(monkeypatch) -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository, ttl_seconds=1, max_entries=1)
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    manager._wall_clock = lambda: current[0]
    manager._monotonic_clock = lambda: 10.0
    manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="challenge_capacity"):
        manager.issue_challenge(binding(), manifest())
    current[0] += timedelta(seconds=1)
    assert manager.issue_challenge(binding(), manifest()).token.startswith("pc1.")

    manager = PrivateContextConfirmation(repository, max_entries=2)
    monkeypatch.setattr("src.private_context_confirmation.secrets.token_urlsafe", lambda _: "A" * 43)
    manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="challenge_unavailable"):
        manager.issue_challenge(binding(), manifest())


@pytest.mark.parametrize("ids", [(1, 1), (True, 2), (0, 2), ((1 << 63), 2)])
def test_repository_sealer_rejects_reuse_and_noncanonical_ids(ids) -> None:
    class BadRepository:
        def insert_confirmation(self, request, seal_pair) -> None:
            seal_pair(*ids)

        def load_confirmation_rows(self, *args):
            return None

    manager = PrivateContextConfirmation(BadRepository())
    challenge = manager.issue_challenge(binding(), manifest())
    with pytest.raises(ConfirmationError, match="invalid_message_ids"):
        consume(manager, challenge)
    with pytest.raises(ConfirmationError, match="challenge_spent"):
        consume(manager, challenge)

    class ReuseRepository:
        def insert_confirmation(self, request, seal_pair) -> None:
            seal_pair(1, 2)
            seal_pair(3, 4)

        def load_confirmation_rows(self, *args):
            return None

    manager = PrivateContextConfirmation(ReuseRepository())
    with pytest.raises(ConfirmationError, match="seal_reused"):
        consume(manager, manager.issue_challenge(binding(), manifest()))


def test_expiry_during_persistence_and_caller_object_mutation_fail_closed() -> None:
    repository = Repository()
    manager = PrivateContextConfirmation(repository, ttl_seconds=1)
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    manager._wall_clock = lambda: now[0]
    manager._monotonic_clock = lambda: 1.0
    original_insert = repository.insert_confirmation

    def expire_after_insert(request, sealer) -> None:
        original_insert(request, sealer)
        now[0] += timedelta(seconds=1)

    repository.insert_confirmation = expire_after_insert
    with pytest.raises(ConfirmationError, match="challenge_expired"):
        consume(manager, manager.issue_challenge(binding(), manifest()))

    manager = PrivateContextConfirmation(Repository())
    original_binding, original_manifest = binding(), manifest()
    challenge = manager.issue_challenge(original_binding, original_manifest)
    object.__setattr__(original_binding, "chat_session_id", "changed")
    object.__setattr__(original_manifest.items[0], "selection_ref", "changed")
    assert consume(manager, challenge, binding(), manifest()).disclosure_message_id > 0
