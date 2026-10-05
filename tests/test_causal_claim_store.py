import hashlib
import importlib
import inspect
import json
import sqlite3
import threading

import pytest


def _module():
    return importlib.import_module("src.causal_claim_store")


def _ref(character: str) -> str:
    return "sha256:" + character * 64


def _claim(*, owner="owner_a", claim_id="claim_a", version=1, actor="actor_a"):
    return {
        "schema": "odysseus.causal_claim.v1",
        "claim_id": claim_id,
        "owner": owner,
        "policy": "policy_a",
        "source_version": _ref("1"),
        "claim_version": version,
        "candidate_actor": actor,
        "evidence_refs": [_ref("2")],
    }


def _transition(report, *, event_id="event_a", to_state="under_review", reviewer="reviewer_a", relation="none", related=None, prior=None, reason=None):
    return {
        "schema": "odysseus.causal_review_transition.v1",
        "event_id": event_id,
        "claim_ref": report["claim_ref"],
        "expected_head_digest": report["head_digest"],
        "from_state": report["state"],
        "to_state": to_state,
        "reviewer": reviewer,
        "evidence_refs": [_ref("3")],
        "relation_kind": relation,
        "related_claim_ref": related,
        "prior_claim_version": prior,
        "reason_code": reason,
    }


def _store(module):
    return module.CausalClaimStore(sqlite3.connect(":memory:"), initialize=True)


def _error(module, code, callback):
    with pytest.raises(module.CausalClaimStoreError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def _event_digest(event):
    payload = {key: event[key] for key in event if key != "event_digest"}
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    framed = b"odysseus.causal_review_event.v1\x00" + len(encoded).to_bytes(8, "big") + encoded
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def test_nmg05a_public_exports_signatures_schemas_and_exact_field_sets():
    module = _module()
    required = {
        "CAUSAL_CLAIM_SCHEMA", "CAUSAL_REVIEW_TRANSITION_SCHEMA", "CAUSAL_CLAIM_INPUT_FIELDS",
        "CAUSAL_CLAIM_REPORT_FIELDS", "CAUSAL_REVIEW_TRANSITION_FIELDS", "CAUSAL_REVIEW_EVENT_FIELDS",
        "CAUSAL_EPISTEMIC_STATES", "CAUSAL_EPISTEMIC_TRANSITIONS", "CAUSAL_RELATION_KINDS",
        "CAUSAL_CLAIM_STORE_ERROR_CODES", "MAX_CAUSAL_EVIDENCE_REFS", "MAX_CAUSAL_EVENTS_PER_CLAIM",
        "CausalClaimStoreError", "CausalClaimStore",
    }
    assert required <= set(vars(module))
    assert module.CAUSAL_CLAIM_SCHEMA == "odysseus.causal_claim.v1"
    assert module.CAUSAL_REVIEW_TRANSITION_SCHEMA == "odysseus.causal_review_transition.v1"
    assert tuple(module.CAUSAL_CLAIM_INPUT_FIELDS) == tuple(_claim())
    assert tuple(module.CAUSAL_CLAIM_REPORT_FIELDS) == (
        "schema", "claim_ref", "claim_id", "owner", "policy", "source_version", "claim_version",
        "candidate_actor", "evidence_refs", "state", "head_sequence", "head_digest",
    )
    assert tuple(module.CAUSAL_REVIEW_TRANSITION_FIELDS) == tuple(_transition({"claim_ref": _ref("a"), "head_digest": _ref("b"), "state": "candidate"}))
    assert inspect.signature(module.CausalClaimStore).parameters["connection"].kind is inspect.Parameter.POSITIONAL_ONLY


def test_nmg05a_adopts_exact_connection_initializes_only_empty_schema_and_closes():
    module = _module()
    _error(module, "invalid_connection", lambda: module.CausalClaimStore(object(), initialize=True))
    connection = sqlite3.connect(":memory:")
    store = module.CausalClaimStore(connection, initialize=True)
    assert connection.execute("PRAGMA application_id").fetchone()[0] == 1329876533
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    store.close()
    store.close()
    _error(module, "closed", lambda: store.create_claim(_claim()))
    dirty = sqlite3.connect(":memory:")
    dirty.execute("CREATE TABLE foreign_table (x INTEGER)")
    _error(module, "invalid_schema", lambda: module.CausalClaimStore(dirty, initialize=True))


def test_nmg05a_reopen_rejects_wrong_extra_or_mutated_schema_objects(tmp_path):
    module = _module()
    database = tmp_path / "causal-claims.sqlite3"
    connection = sqlite3.connect(database)
    store = module.CausalClaimStore(connection, initialize=True)
    store.close()
    reopened = sqlite3.connect(database)
    assert module.CausalClaimStore(reopened, initialize=False).close() is None
    tampered = sqlite3.connect(database)
    tampered.execute("CREATE TABLE unexpected (x INTEGER)")
    tampered.commit()
    _error(module, "invalid_schema", lambda: module.CausalClaimStore(tampered, initialize=False))


@pytest.mark.parametrize("mutation", ("trigger", "index", "columns"))
def test_same_name_schema_changes_rejected_on_live_use_and_reopen(tmp_path, mutation):
    module = _module()
    database = tmp_path / "schema-identity.sqlite3"
    connection = sqlite3.connect(database)
    store = module.CausalClaimStore(connection, initialize=True)
    if mutation == "trigger":
        connection.execute("DROP TRIGGER causal_claims_no_update")
        connection.execute("CREATE TRIGGER causal_claims_no_update BEFORE UPDATE ON causal_claims BEGIN SELECT 1; END")
    elif mutation == "index":
        connection.execute("DROP INDEX causal_claims_claim_ref_uq")
        connection.execute("CREATE INDEX causal_claims_claim_ref_uq ON causal_claims (claim_ref)")
    else:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_master SET sql=replace(sql, 'claim_digest TEXT NOT NULL', 'claim_digest BLOB') WHERE name='causal_claims'")
        connection.execute("PRAGMA writable_schema = OFF")
    connection.commit()
    changes = connection.total_changes
    _error(module, "invalid_schema", lambda: store.capture_owner_snapshot("owner_a"))
    assert connection.total_changes == changes
    store.close()
    reopened = sqlite3.connect(database)
    try:
        _error(module, "invalid_schema", lambda: module.CausalClaimStore(reopened, initialize=False))
    finally:
        reopened.close()


@pytest.mark.parametrize("field", ("schema", "from_state", "to_state", "relation_kind"))
def test_transition_literal_subclasses_rejected_before_callbacks_or_writes(field):
    module = _module()
    callbacks = []

    class HostileLiteral(str):
        def __eq__(self, other):
            callbacks.append("eq")
            raise AssertionError("unexpected equality callback")

        def __ne__(self, other):
            callbacks.append("ne")
            raise AssertionError("unexpected inequality callback")

        def __hash__(self):
            callbacks.append("hash")
            raise AssertionError("unexpected hash callback")

    connection = sqlite3.connect(":memory:")
    with module.CausalClaimStore(connection, initialize=True) as store:
        report = store.create_claim(_claim())
        transition = _transition(report)
        transition[field] = HostileLiteral(transition[field])
        changes = connection.total_changes
        _error(module, "invalid_input", lambda: store.append_review_transition(transition))
        assert callbacks == [] and connection.total_changes == changes
        assert store.read_claim("owner_a", "claim_a", 1) == report


def test_claim_schema_and_colliding_keys_rejected_before_callbacks_or_writes():
    module = _module()
    callbacks = []

    class HostileSchema(str):
        def __ne__(self, other):
            callbacks.append("schema")
            raise AssertionError("unexpected schema callback")

    class CollidingKey:
        def __hash__(self):
            callbacks.append("hash")
            return hash("owner")

        def __eq__(self, other):
            callbacks.append("eq")
            raise AssertionError("unexpected key callback")

    connection = sqlite3.connect(":memory:")
    with module.CausalClaimStore(connection, initialize=True) as store:
        bad_schema = _claim()
        bad_schema["schema"] = HostileSchema(bad_schema["schema"])
        bad_key = _claim()
        del bad_key["owner"]
        bad_key[CollidingKey()] = "owner_a"
        callbacks.clear()
        changes = connection.total_changes
        for claim in (bad_schema, bad_key):
            _error(module, "invalid_input", lambda claim=claim: store.create_claim(claim))
        assert callbacks == [] and connection.total_changes == changes
        assert store.capture_owner_snapshot("owner_a").claims == ()


def test_nmg05a_create_is_owner_version_evidence_bound_idempotent_and_detached():
    module = _module()
    with _store(module) as store:
        claim = _claim()
        report = store.create_claim(claim)
        assert report["state"] == "candidate"
        assert report["head_sequence"] == 0
        assert report["head_digest"].startswith("sha256:")
        assert store.create_claim(dict(claim)) == report
        claim["evidence_refs"].append(_ref("4"))
        assert store.read_claim("owner_a", "claim_a", 1)["evidence_refs"] == (_ref("2"),)
        collision = _claim(actor="actor_b")
        _error(module, "conflict", lambda: store.create_claim(collision))


def test_nmg05a_exact_owner_read_and_bounded_history_never_enumerate_other_owners():
    module = _module()
    with _store(module) as store:
        report = store.create_claim(_claim())
        store.create_claim(_claim(owner="owner_b", claim_id="claim_b"))
        _error(module, "owner_mismatch", lambda: store.read_claim("owner_b", "claim_a", 1))
        _error(module, "not_found", lambda: store.read_claim("owner_a", "claim_a", 2))
        assert store.review_history("owner_a", "claim_a", 1, limit=0) == ()
        _error(module, "invalid_input", lambda: store.review_history("owner_a", "claim_a", 1, limit=True))
        assert report["owner"] == "owner_a"


def test_owner_snapshot_is_stable_bounded_and_detached():
    module = _module()
    with _store(module) as store:
        first = store.create_claim(_claim())
        store.append_review_transition(_transition(first))
        store.create_claim(_claim(owner="owner_b", claim_id="private_b"))
        snapshot = store.capture_owner_snapshot("owner_a")
        assert snapshot.claims[0][0]["claim_ref"] == first["claim_ref"]
        assert len(snapshot.claims[0][1]) == 1
        assert snapshot.snapshot_digest.startswith("sha256:")
        snapshot.claims[0][0]["state"] = "tampered"
        again = store.capture_owner_snapshot("owner_a")
        assert again.claims[0][0]["state"] == "under_review"
        assert again.snapshot_digest == snapshot.snapshot_digest
        _error(module, "budget_exceeded", lambda: store.capture_owner_snapshot("owner_a", max_events=0))


def test_nmg05a_transition_matrix_requires_independent_reviewer_and_evidence():
    module = _module()
    with _store(module) as store:
        report = store.create_claim(_claim())
        direct = _transition(report, to_state="supported")
        _error(module, "invalid_transition", lambda: store.append_review_transition(direct))
        self_review = _transition(report, reviewer="actor_a")
        _error(module, "independent_review_required", lambda: store.append_review_transition(self_review))
        owner_review = _transition(report, reviewer="owner_a")
        _error(module, "independent_review_required", lambda: store.append_review_transition(owner_review))
        missing_evidence = _transition(report)
        missing_evidence["evidence_refs"] = []
        _error(module, "evidence_required", lambda: store.append_review_transition(missing_evidence))
        reviewing = store.append_review_transition(_transition(report))
        supported = store.append_review_transition(_transition(reviewing, event_id="event_b", to_state="supported", reviewer="reviewer_b"))
        assert supported["state"] == "supported"


def test_nmg05a_conflict_alternative_retraction_and_supersession_bind_exact_related_claims():
    module = _module()
    with _store(module) as store:
        first = store.create_claim(_claim())
        second = store.create_claim(_claim(claim_id="claim_b", version=2))
        reviewing = store.append_review_transition(_transition(first))
        conflict = store.append_review_transition(_transition(reviewing, event_id="event_b", to_state="contested", relation="conflict", related=second["claim_ref"], reviewer="reviewer_b"))
        assert conflict["state"] == "contested"
        retracted = store.append_review_transition(_transition(conflict, event_id="event_c", to_state="retracted", relation="retraction", prior=1, reason="reason_a", reviewer="reviewer_c"))
        assert retracted["state"] == "retracted"
        successor = store.create_claim(_claim(claim_id="claim_c", version=3))
        active = store.create_claim(_claim(claim_id="claim_d", version=1))
        active = store.append_review_transition(_transition(active, event_id="event_d", reviewer="reviewer_d"))
        active = store.append_review_transition(_transition(active, event_id="event_e", to_state="supported", reviewer="reviewer_e"))
        superseded = store.append_review_transition(_transition(active, event_id="event_f", to_state="superseded", relation="supersession", related=successor["claim_ref"], prior=1, reviewer="reviewer_f"))
        assert superseded["state"] == "superseded"


def test_nmg05a_terminal_replay_duplicate_event_and_stale_head_fail_closed():
    module = _module()
    with _store(module) as store:
        report = store.create_claim(_claim())
        transition = _transition(report)
        reviewing = store.append_review_transition(transition)
        assert store.append_review_transition(transition) == reviewing
        event = store.review_history("owner_a", "claim_a", 1, limit=64)[0]
        assert tuple(event) == module.CAUSAL_REVIEW_EVENT_FIELDS
        assert event["prior_digest"] == report["head_digest"]
        assert event["event_digest"] == _event_digest(event)
        _error(module, "conflict", lambda: store.append_review_transition(_transition(report, event_id="event_b")))
        terminal = store.append_review_transition(_transition(reviewing, event_id="event_c", to_state="retracted", relation="retraction", prior=1, reason="reason_a", reviewer="reviewer_b"))
        _error(module, "terminal_claim", lambda: store.append_review_transition(_transition(terminal, event_id="event_d", to_state="under_review", reviewer="reviewer_c")))


def test_nmg05a_append_only_triggers_hash_chain_schema_and_reopen_detect_tampering():
    module = _module()
    connection = sqlite3.connect(":memory:")
    store = module.CausalClaimStore(connection, initialize=True)
    report = store.create_claim(_claim())
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE causal_claims SET policy = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM causal_claims")
    connection.execute("DROP TRIGGER causal_claims_no_update")
    _error(module, "invalid_schema", lambda: store.read_claim("owner_a", "claim_a", 1))
    assert report["head_sequence"] == 0


def test_nmg05a_two_connection_race_rollback_and_reopen_are_atomic(tmp_path):
    module = _module()
    database = tmp_path / "causal-race.sqlite3"
    left_store = module.CausalClaimStore(sqlite3.connect(database, check_same_thread=False), initialize=True)
    report = left_store.create_claim(_claim())
    right_store = module.CausalClaimStore(sqlite3.connect(database, check_same_thread=False), initialize=False)
    results = []
    barrier = threading.Barrier(2)

    def append(store, event_id):
        try:
            barrier.wait()
            results.append(store.append_review_transition(_transition(report, event_id=event_id)))
        except module.CausalClaimStoreError as exc:
            results.append(exc.code)

    left = threading.Thread(target=append, args=(left_store, "event_a"))
    right = threading.Thread(target=append, args=(right_store, "event_b"))
    left.start(); right.start(); left.join(); right.join()
    assert sorted(type(value) is dict for value in results) == [False, True]
    assert any(value == "conflict" for value in results)
    left_store.close()
    right_store.close()
    reopened = module.CausalClaimStore(sqlite3.connect(database), initialize=False)
    assert len(reopened.review_history("owner_a", "claim_a", 1, limit=64)) == 1
    reopened.close()


def test_nmg05a_hostile_exact_types_colliding_keys_baseexception_and_mutation_do_not_escape():
    module = _module()

    class DictSubclass(dict):
        pass

    with _store(module) as store:
        _error(module, "invalid_input", lambda: store.create_claim(DictSubclass(_claim())))
        bad = _claim(); bad["claim_version"] = True
        _error(module, "invalid_input", lambda: store.create_claim(bad))
        report = store.create_claim(_claim())
        report["evidence_refs"] += (_ref("4"),)
        assert store.read_claim("owner_a", "claim_a", 1)["evidence_refs"] == (_ref("2"),)

    prepared = module._claim_input(_claim())
    claim_ref = module._claim_ref(prepared["owner"], prepared["claim_id"], prepared["claim_version"])
    claim_digest = module._digest(module.CAUSAL_CLAIM_SCHEMA, prepared)
    malformed_or_noncanonical = ("{", "null", "{}", "[\"sha256:" + "2" * 64 + "\",\"sha256:" + "2" * 64 + "\"]", json.dumps([_ref("2")], indent=2))
    for evidence_json in malformed_or_noncanonical:
        connection = sqlite3.connect(":memory:")
        store = module.CausalClaimStore(connection, initialize=True)
        connection.execute("INSERT INTO causal_claims (owner, claim_id, claim_version, claim_ref, policy, source_version, candidate_actor, evidence_json, claim_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (prepared["owner"], prepared["claim_id"], prepared["claim_version"], claim_ref, prepared["policy"], prepared["source_version"], prepared["candidate_actor"], evidence_json, claim_digest))
        for _ in range(2):
            _error(module, "integrity_failed", lambda: store.read_claim("owner_a", "claim_a", 1))
        store.close()


def test_nmg05a_max_max_plus_one_overflow_and_zero_effect_boundaries():
    module = _module()
    with _store(module) as store:
        maximum = _claim(version=2**63 - 1)
        assert store.create_claim(maximum)["claim_version"] == 2**63 - 1
        for bad_version in (0, -1, 2**63, True):
            bad = _claim(version=bad_version)
            _error(module, "invalid_input", lambda bad=bad: store.create_claim(bad))
        too_many = _claim(); too_many["evidence_refs"] = [_ref("2")] * 17
        _error(module, "evidence_required", lambda: store.create_claim(too_many))
