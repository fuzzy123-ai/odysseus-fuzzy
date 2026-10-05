from contextlib import contextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes.clarification_routes import setup_clarification_routes
from src.clarification_store import ClarificationStore
from src.auth_helpers import SessionPrincipal
from src.clarification_attention import build_session_clarification_attention


def _session_factory(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from core.auth import AuthManager
    manager = AuthManager(str(tmp_path / "legacy-route-auth.json"))
    for owner in ("alice", "bob"):
        assert manager.create_user(owner, "legacy-route-password-123", is_admin=owner == "alice")
    factory.auth_manager = manager
    return factory


def _app(factory, *, user="alice"):
    app = FastAPI()
    app.state.auth_manager = factory.auth_manager
    app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=factory)))

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = user
        return await call_next(request)

    return app


def _client(factory, user="alice"):
    client = TestClient(_app(factory, user=user))
    token = factory.auth_manager.create_session(user, "legacy-route-password-123")
    assert token
    client.cookies.set("odysseus_session", token)
    return client


def _request():
    return {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a local document review flow.",
        "questions": [
            {
                "key": "target_documents",
                "type": "short_text",
                "prompt": "Which documents should be reviewed first?",
                "required": True,
                "reason": "The document source changes scope and privacy boundaries.",
                "category": "scope",
            },
            {
                "key": "tone",
                "type": "single_select",
                "prompt": "Which review tone should be used?",
                "required": False,
                "reason": "This only changes wording.",
                "options": [{"label": "Concise"}, {"label": "Detailed", "recommended": True}],
            },
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }


def _create(client):
    response = client.post(
        "/api/sessions/sess-1/clarification",
        json={"request": _request(), "project_slug": "demo"},
    )
    assert response.status_code == 200, response.text
    return response.json()["run"]


def test_clarification_routes_create_read_active_and_owner_scope(tmp_path):
    factory = _session_factory(tmp_path)
    alice = _client(factory, "alice")
    run = _create(alice)

    active = alice.get("/api/sessions/sess-1/clarification")
    assert active.status_code == 200
    assert active.json()["active"] is True
    assert active.json()["clarification"]["clarification_id"] == run["clarification_id"]
    assert active.json()["clarification"]["owner"] == "alice"

    detail = alice.get(f"/api/clarifications/{run['clarification_id']}")
    assert detail.status_code == 200
    assert detail.json()["clarification"]["status"] == "clarifying"

    bob = _client(factory, "bob")
    denied = bob.get(f"/api/clarifications/{run['clarification_id']}")
    assert denied.status_code == 404


def test_clarification_routes_answer_replay_conflict_and_revision(tmp_path):
    client = _client(_session_factory(tmp_path), "alice")
    run = _create(client)
    clarification_id = run["clarification_id"]

    answer = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports folder"},
            "expected_version": 1,
            "idempotency_key": "idem-answer-1",
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["run"]["version"] == 2
    assert answer.json()["event"]["event_type"] == "question_answered"

    replay = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports folder"},
            "expected_version": 1,
            "idempotency_key": "idem-answer-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    stale = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "tone",
            "answer": "Concise",
            "expected_version": 1,
            "idempotency_key": "idem-answer-2",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_conflict"
    assert stale.json()["detail"]["current_version"] == 2

    revision = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports and invoices"},
            "expected_version": 2,
            "idempotency_key": "idem-answer-3",
        },
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["event"]["event_type"] == "answer_revised"
    assert revision.json()["run"]["answers"]["target_documents"]["answer"]["text"] == "reports and invoices"


def test_clarification_routes_secret_answer_returns_secure_handoff_intent(tmp_path):
    client = _client(_session_factory(tmp_path), "alice")
    run = _create(client)

    response = client.post(
        f"/api/clarifications/{run['clarification_id']}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "api_key=super-secret"},
            "expected_version": 1,
            "idempotency_key": "idem-handoff",
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "secure_handoff_required"
    assert detail["secure_handoff"]["value_visible"] is False
    assert detail["secure_handoff"]["value_stored"] is False
    assert "super-secret" not in response.text


def test_clarification_routes_actions_pause_reopen_cancel_and_complete(tmp_path):
    client = _client(_session_factory(tmp_path), "alice")
    run = _create(client)
    clarification_id = run["clarification_id"]

    pause = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "pause", "expected_version": 1, "idempotency_key": "idem-pause"},
    )
    assert pause.status_code == 200, pause.text
    assert pause.json()["run"]["status"] == "paused"

    pause_replay = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "pause", "expected_version": 1, "idempotency_key": "idem-pause"},
    )
    assert pause_replay.status_code == 200
    assert pause_replay.json()["idempotent_replay"] is True

    reopen = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "reopen", "expected_version": 2, "idempotency_key": "idem-reopen"},
    )
    assert reopen.status_code == 200, reopen.text
    assert reopen.json()["run"]["status"] == "clarifying"

    answer = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": "reports",
            "expected_version": 3,
            "idempotency_key": "idem-answer",
        },
    )
    assert answer.status_code == 200, answer.text

    complete = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={
            "action": "complete",
            "understanding_summary": "Review reports first.",
            "expected_version": 4,
            "idempotency_key": "idem-complete",
        },
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["run"]["status"] == "ready_for_plan"
    assert complete.json()["run"]["ready_for_plan"] is True

    cancelled_run = _create(client)
    cancel = client.post(
        f"/api/clarifications/{cancelled_run['clarification_id']}/actions",
        json={"action": "cancel", "expected_version": 1, "idempotency_key": "idem-cancel"},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["run"]["status"] == "cancelled"


def test_clarification_routes_reject_bearer_and_missing_cookie(tmp_path):
    client = TestClient(_app(_session_factory(tmp_path), user="alice"))
    denied = client.post("/api/sessions/sess-1/clarification", json={"request": _request()})
    assert denied.status_code == 401
    client.cookies.set("odysseus_session", "alice")
    mixed = client.post("/api/sessions/sess-1/clarification", headers={"Authorization": "Bearer forged"}, json={"request": _request()})
    assert mixed.status_code == 401

def test_real_auth_cookie_sid_route_lifecycle_denies_forgery_and_preserves_sid_on_rename(tmp_path):
    from core.auth import AuthManager
    engine=create_engine(f"sqlite:///{tmp_path / 'route-auth.db'}",connect_args={"check_same_thread":False});Base.metadata.create_all(bind=engine);factory=sessionmaker(bind=engine,autocommit=False,autoflush=False)
    manager=AuthManager(str(tmp_path / "auth.json"));assert manager.create_user("alice","password-123",is_admin=True);token=manager.create_session("alice","password-123");assert token
    app=FastAPI();app.state.auth_manager=manager;app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=factory)))
    client=TestClient(app);client.cookies.set("odysseus_session",token)
    created=client.post("/api/sessions/auth-session/clarification",json={"request":_request()});assert created.status_code==200
    run=created.json()["run"];assert "owner_" not in repr(created.json())
    assert TestClient(app).get(f"/api/clarifications/{run['clarification_id']}").status_code==401
    forged=TestClient(app);forged.cookies.set("odysseus_session","forged")
    assert forged.get(f"/api/clarifications/{run['clarification_id']}").status_code==401
    assert client.get(f"/api/clarifications/{run['clarification_id']}",headers={"Authorization":"Bearer bad"}).status_code==401
    assert manager.rename_user("alice","alice-renamed",requesting_user="alice") is True
    assert client.get(f"/api/clarifications/{run['clarification_id']}").status_code==200

def test_real_auth_restart_and_same_alias_recreate_cannot_access_old_sid_run(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from core.auth import AuthManager
    import core.database as database_module
    engine=create_engine(f"sqlite:///{tmp_path / 'restart-route.db'}",connect_args={"check_same_thread":False});Base.metadata.create_all(bind=engine);factory=sessionmaker(bind=engine,autocommit=False,autoflush=False)
    auth_path=str(tmp_path / "restart-auth.json");manager=AuthManager(auth_path);assert manager.create_user("alice","password-123",is_admin=True);token=manager.create_session("alice","password-123")
    app=FastAPI();app.state.auth_manager=manager;app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=factory)));client=TestClient(app);client.cookies.set("odysseus_session",token)
    run=client.post("/api/sessions/restart-session/clarification",json={"request":_request()}).json()["run"]
    restarted=AuthManager(auth_path);app.state.auth_manager=restarted
    assert client.get(f"/api/clarifications/{run['clarification_id']}").status_code==200
    @contextmanager
    def local_db():
        db=factory()
        try: yield db; db.commit()
        finally: db.close()
    monkeypatch.setattr(database_module,"get_db_session",local_db)
    assert restarted.delete_user("alice",requesting_user="alice") is False
    # A separate admin performs deletion; recreated same alias receives a new SID.
    assert restarted.create_user("admin","password-456",is_admin=True)
    assert restarted.delete_user("alice",requesting_user="admin") is True
    assert restarted.create_user("alice","password-789",is_admin=False)
    new_token=restarted.create_session("alice","password-789");new_client=TestClient(app);new_client.cookies.set("odysseus_session",new_token)
    assert new_client.get(f"/api/clarifications/{run['clarification_id']}").status_code==404
    assert new_client.get(f"/api/clarifications/{run['clarification_id']}/events").status_code==404
    assert new_client.post(f"/api/clarifications/{run['clarification_id']}/answers",json={"question_id":"target_documents","answer":{"text":"reports"},"expected_version":1,"idempotency_key":"new-sid"}).status_code==404
    assert new_client.get("/api/sessions/restart-session/clarification").json()["active"] is False

def test_real_auth_rename_renders_new_alias_without_changing_sid_or_events(tmp_path):
    from core.auth import AuthManager
    engine=create_engine(f"sqlite:///{tmp_path / 'rename-route.db'}",connect_args={"check_same_thread":False});Base.metadata.create_all(bind=engine);factory=sessionmaker(bind=engine,autocommit=False,autoflush=False)
    manager=AuthManager(str(tmp_path / "rename-auth.json"));assert manager.create_user("alice","password-123",is_admin=True);token=manager.create_session("alice","password-123");old_sid=manager.strict_session_identity(token)[1]
    app=FastAPI();app.state.auth_manager=manager;store=ClarificationStore(session_factory=factory);app.include_router(setup_clarification_routes(store=store));client=TestClient(app);client.cookies.set("odysseus_session",token);run=client.post("/api/sessions/rename-session/clarification",json={"request":_request()}).json()["run"]
    assert manager.rename_user("alice","alice-renamed",requesting_user="alice")
    assert manager.strict_session_identity(token)[1] == old_sid
    fresh=client.get(f"/api/clarifications/{run['clarification_id']}").json()["clarification"];events=client.get(f"/api/clarifications/{run['clarification_id']}/events").json()["events"]
    assert fresh["owner"]=="alice-renamed" and all(item["owner"]=="alice-renamed" for item in events)
    assert old_sid not in repr(fresh) and old_sid not in repr(events)


def test_denied_cookie_and_bearer_requests_do_not_query_store():
    class SpyStore:
        def __getattr__(self,name):
            def denied(*args,**kwargs): raise AssertionError("store must not be queried")
            return denied
    app=FastAPI();app.state.auth_manager=type("Auth",(),{"is_configured":True,"strict_session_identity":lambda self,token: (_ for _ in ()).throw(ValueError("bad"))})()
    app.include_router(setup_clarification_routes(store=SpyStore()))
    client=TestClient(app)
    assert client.get("/api/clarifications/old-run-001").status_code==401
    assert client.get("/api/clarifications/old-run-001",headers={"Authorization":"Bearer forged"}).status_code==401

def test_new_sid_has_no_direct_store_or_attention_visibility(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'direct-sid.db'}");Base.metadata.create_all(bind=engine);store=ClarificationStore(session_factory=sessionmaker(bind=engine));old=SessionPrincipal("alice","owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","browser_session");new=SessionPrincipal("alice","owner_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","browser_session")
    store.create_run(principal=old,session_id="direct-sid",request=_request())
    assert store.list_active_runs(principal=new)==() and store.list_active_metadata(principal=new)==() and store.list_inbox_metadata(principal=new)==()
    assert build_session_clarification_attention(principal=new,session_id="direct-sid",store=store)["active"] is False

def test_real_auth_revoke_between_resolution_and_guard_blocks_all_mutating_routes(tmp_path, monkeypatch):
    """A cookie resolved before revocation cannot reach a clarification store."""
    import threading
    import routes.clarification_routes as clarification_routes
    from core.auth import AuthManager

    engine = create_engine(
        f"sqlite:///{tmp_path / 'guard-before.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    base = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "guard-before-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    factory_calls: list[object] = []

    def counted_factory():
        factory_calls.append(object())
        return base()

    store = ClarificationStore(session_factory=counted_factory)
    app = FastAPI()
    app.state.auth_manager = manager
    app.include_router(setup_clarification_routes(store=store))
    real_require = clarification_routes.require_trusted_session_principal
    resolved = threading.Event()
    release = threading.Event()

    def pause_after_real_resolution(request):
        principal = real_require(request)
        resolved.set()
        if not release.wait(timeout=5):
            raise RuntimeError("guard resolution release timeout")
        return principal

    monkeypatch.setattr(clarification_routes, "require_trusted_session_principal", pause_after_real_resolution)
    seed_store = ClarificationStore(session_factory=base)
    cases = (
        ("/api/clarifications", lambda run: {"session_id": "before-create", "request": _request()}),
        ("/api/sessions/before-session/clarification", lambda run: {"request": _request()}),
        ("/api/clarifications/{id}/answers", lambda run: {
            "question_id": "target_documents", "answer": {"text": "reports"},
            "expected_version": 1, "idempotency_key": "before-answer",
        }),
        ("/api/clarifications/{id}/actions", lambda run: {
            "action": "pause", "expected_version": 1, "idempotency_key": "before-action",
        }),
    )
    for index, (path_template, payload_for) in enumerate(cases):
        token = manager.create_session("alice", "password-123")
        assert token
        run = None
        if "{id}" in path_template:
            principal = SessionPrincipal("alice", manager.strict_session_identity(token)[1], "browser_session")
            run = seed_store.create_run(
                principal=principal, session_id=f"before-{index}", request=_request(),
            ).run
        path = path_template.format(id=run["clarification_id"] if run else "")
        response: list[object] = []
        errors: list[BaseException] = []
        resolved.clear()
        release.clear()

        def invoke() -> None:
            try:
                with TestClient(app) as request_client:
                    request_client.cookies.set("odysseus_session", token)
                    response.append(request_client.post(path, json=payload_for(run)))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        revoker = threading.Thread(target=lambda: manager.revoke_token(token))
        revoker_started = False
        worker.start()
        try:
            assert resolved.wait(timeout=5)
            revoker.start()
            revoker_started = True
            revoker.join(timeout=5)
            assert not revoker.is_alive()
        finally:
            release.set()
            worker.join(timeout=10)
            if revoker_started:
                revoker.join(timeout=10)
        assert not worker.is_alive()
        assert not errors and len(response) == 1 and response[0].status_code == 401
        # Runs seeded for answer/action are intentionally excluded from this count.
        assert not factory_calls


def test_real_auth_guard_holds_revoke_through_paused_mutation_commit(tmp_path):
    """A real browser guard keeps revocation outside every durable mutation."""
    import threading
    from core.auth import AuthManager

    engine = create_engine(
        f"sqlite:///{tmp_path / 'guard-during.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    base = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "guard-during-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    entered = threading.Event()
    release = threading.Event()

    class PausedSession:
        def __init__(self):
            self.inner = base()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("commit release timeout")
            return self.inner.commit()

        def close(self):
            self.inner.close()

    app = FastAPI()
    app.state.auth_manager = manager
    app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=PausedSession)))
    seed_store = ClarificationStore(session_factory=base)
    cases = (
        ("/api/clarifications", lambda run, index: {"session_id": f"during-create-{index}", "request": _request()}),
        ("/api/sessions/during-session/clarification", lambda run, index: {"request": _request()}),
        ("/api/clarifications/{id}/answers", lambda run, index: {
            "question_id": "target_documents", "answer": {"text": "reports"},
            "expected_version": 1, "idempotency_key": f"during-answer-{index}",
        }),
        ("/api/clarifications/{id}/actions", lambda run, index: {
            "action": "pause", "expected_version": 1, "idempotency_key": f"during-action-{index}",
        }),
    )
    for index, (path_template, payload_for) in enumerate(cases):
        token = manager.create_session("alice", "password-123")
        assert token
        run = None
        if "{id}" in path_template:
            principal = SessionPrincipal("alice", manager.strict_session_identity(token)[1], "browser_session")
            run = seed_store.create_run(
                principal=principal, session_id=f"during-{index}", request=_request(),
            ).run
        path = path_template.format(id=run["clarification_id"] if run else "")
        response: list[object] = []
        errors: list[BaseException] = []
        revoke_finished = threading.Event()
        revoke_entered = threading.Event()
        entered.clear()
        release.clear()

        def invoke() -> None:
            try:
                with TestClient(app) as request_client:
                    request_client.cookies.set("odysseus_session", token)
                    response.append(request_client.post(path, json=payload_for(run, index)))
            except BaseException as exc:
                errors.append(exc)

        def revoke() -> None:
            try:
                revoke_entered.set()
                manager.revoke_token(token)
            except BaseException as exc:
                errors.append(exc)
            finally:
                revoke_finished.set()

        worker = threading.Thread(target=invoke)
        revoker = threading.Thread(target=revoke)
        revoker_started = False
        worker.start()
        try:
            assert entered.wait(timeout=5)
            revoker.start()
            revoker_started = True
            assert revoke_entered.wait(timeout=5)
            authority_acquired = manager._session_authority_lock.acquire(blocking=False)
            try:
                assert not authority_acquired
            finally:
                if authority_acquired:
                    manager._session_authority_lock.release()
            assert not revoke_finished.wait(timeout=0.2)
        finally:
            release.set()
            worker.join(timeout=10)
            if revoker_started:
                revoker.join(timeout=10)
        assert not worker.is_alive() and not revoker.is_alive()
        assert not errors and len(response) == 1 and response[0].status_code == 200
        assert revoke_finished.is_set()
        with TestClient(app) as denied:
            denied.cookies.set("odysseus_session", token)
            assert denied.get("/api/clarifications/after-revoke").status_code == 401


def test_real_auth_delete_user_between_resolution_and_guard_blocks_all_mutating_routes(tmp_path, monkeypatch):
    """Deleting the account before guard entry cannot fall through to SQL."""
    import threading
    import core.database as database_module
    import routes.clarification_routes as clarification_routes
    from core.auth import AuthManager

    engine = create_engine(
        f"sqlite:///{tmp_path / 'delete-before.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    base = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def local_db():
        db = base()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(database_module, "get_db_session", local_db)
    real_require = clarification_routes.require_trusted_session_principal
    cases = (
        ("/api/clarifications", lambda run: {"session_id": "delete-before-create", "request": _request()}),
        ("/api/sessions/delete-before-session/clarification", lambda run: {"request": _request()}),
        ("/api/clarifications/{id}/answers", lambda run: {
            "question_id": "target_documents", "answer": {"text": "reports"},
            "expected_version": 1, "idempotency_key": "delete-before-answer",
        }),
        ("/api/clarifications/{id}/actions", lambda run: {
            "action": "pause", "expected_version": 1, "idempotency_key": "delete-before-action",
        }),
    )
    for index, (path_template, payload_for) in enumerate(cases):
        case_dir = tmp_path / f"delete-before-{index}"
        case_dir.mkdir()
        manager = AuthManager(str(case_dir / "auth.json"))
        assert manager.create_user("admin", "password-456", is_admin=True)
        assert manager.create_user("alice", "password-123")
        token = manager.create_session("alice", "password-123")
        assert token
        factory_calls: list[object] = []

        def counted_factory():
            factory_calls.append(object())
            return base()

        app = FastAPI()
        app.state.auth_manager = manager
        app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=counted_factory)))
        resolved = threading.Event()
        release = threading.Event()

        def pause_after_real_resolution(request):
            principal = real_require(request)
            resolved.set()
            if not release.wait(timeout=5):
                raise RuntimeError("delete-before guard release timeout")
            return principal

        monkeypatch.setattr(clarification_routes, "require_trusted_session_principal", pause_after_real_resolution)
        run = None
        if "{id}" in path_template:
            principal = SessionPrincipal("alice", manager.strict_session_identity(token)[1], "browser_session")
            run = ClarificationStore(session_factory=base).create_run(
                principal=principal, session_id=f"delete-before-{index}", request=_request(),
            ).run
        path = path_template.format(id=run["clarification_id"] if run else "")
        response: list[object] = []
        errors: list[BaseException] = []
        deleted: list[bool] = []

        def invoke() -> None:
            try:
                with TestClient(app) as request_client:
                    request_client.cookies.set("odysseus_session", token)
                    response.append(request_client.post(path, json=payload_for(run)))
            except BaseException as exc:
                errors.append(exc)

        def delete_user() -> None:
            try:
                deleted.append(manager.delete_user("alice", requesting_user="admin"))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        deleter = threading.Thread(target=delete_user)
        deleter_started = False
        worker.start()
        try:
            assert resolved.wait(timeout=5)
            deleter.start()
            deleter_started = True
            deleter.join(timeout=5)
            assert not deleter.is_alive()
        finally:
            release.set()
            worker.join(timeout=10)
            if deleter_started:
                deleter.join(timeout=10)
        assert not worker.is_alive()
        assert not errors and deleted == [True]
        assert len(response) == 1 and response[0].status_code == 401
        assert not factory_calls


def test_real_auth_guard_holds_delete_user_through_paused_mutation_commit(tmp_path, monkeypatch):
    """Account deletion serializes behind each guarded durable mutation."""
    import threading
    import core.database as database_module
    from core.auth import AuthManager

    engine = create_engine(
        f"sqlite:///{tmp_path / 'delete-during.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    base = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def local_db():
        db = base()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(database_module, "get_db_session", local_db)
    entered = threading.Event()
    release = threading.Event()

    class PausedSession:
        def __init__(self):
            self.inner = base()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("delete-during commit release timeout")
            return self.inner.commit()

        def close(self):
            self.inner.close()

    cases = (
        ("/api/clarifications", lambda run, index: {"session_id": f"delete-during-create-{index}", "request": _request()}),
        ("/api/sessions/delete-during-session/clarification", lambda run, index: {"request": _request()}),
        ("/api/clarifications/{id}/answers", lambda run, index: {
            "question_id": "target_documents", "answer": {"text": "reports"},
            "expected_version": 1, "idempotency_key": f"delete-during-answer-{index}",
        }),
        ("/api/clarifications/{id}/actions", lambda run, index: {
            "action": "pause", "expected_version": 1, "idempotency_key": f"delete-during-action-{index}",
        }),
    )
    for index, (path_template, payload_for) in enumerate(cases):
        case_dir = tmp_path / f"delete-during-{index}"
        case_dir.mkdir()
        manager = AuthManager(str(case_dir / "auth.json"))
        assert manager.create_user("admin", "password-456", is_admin=True)
        assert manager.create_user("alice", "password-123")
        token = manager.create_session("alice", "password-123")
        assert token
        app = FastAPI()
        app.state.auth_manager = manager
        app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=PausedSession)))
        run = None
        if "{id}" in path_template:
            principal = SessionPrincipal("alice", manager.strict_session_identity(token)[1], "browser_session")
            run = ClarificationStore(session_factory=base).create_run(
                principal=principal, session_id=f"delete-during-{index}", request=_request(),
            ).run
        path = path_template.format(id=run["clarification_id"] if run else "")
        response: list[object] = []
        errors: list[BaseException] = []
        delete_entered = threading.Event()
        delete_finished = threading.Event()
        entered.clear()
        release.clear()

        def invoke() -> None:
            try:
                with TestClient(app) as request_client:
                    request_client.cookies.set("odysseus_session", token)
                    response.append(request_client.post(path, json=payload_for(run, index)))
            except BaseException as exc:
                errors.append(exc)

        def delete_user() -> None:
            try:
                delete_entered.set()
                assert manager.delete_user("alice", requesting_user="admin") is True
            except BaseException as exc:
                errors.append(exc)
            finally:
                delete_finished.set()

        worker = threading.Thread(target=invoke)
        deleter = threading.Thread(target=delete_user)
        deleter_started = False
        worker.start()
        try:
            assert entered.wait(timeout=5)
            deleter.start()
            deleter_started = True
            assert delete_entered.wait(timeout=5)
            authority_acquired = manager._session_authority_lock.acquire(blocking=False)
            try:
                assert not authority_acquired
            finally:
                if authority_acquired:
                    manager._session_authority_lock.release()
            assert not delete_finished.wait(timeout=0.2)
        finally:
            release.set()
            worker.join(timeout=10)
            if deleter_started:
                deleter.join(timeout=10)
        assert not worker.is_alive() and not deleter.is_alive()
        assert not errors and len(response) == 1 and response[0].status_code == 200
        assert delete_finished.is_set()
        with TestClient(app) as denied:
            denied.cookies.set("odysseus_session", token)
            assert denied.get("/api/clarifications/after-delete").status_code == 401


def test_disabled_auth_local_null_scope_is_visible_only_to_local_and_never_adopted(tmp_path, monkeypatch):
    """The explicit local principal sees NULL/local rows; a browser SID never does."""
    from core.auth import AuthManager
    from core.database import ClarificationRun

    monkeypatch.setenv("AUTH_ENABLED", "false")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'local-null.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    store = ClarificationStore(session_factory=factory)
    local = SessionPrincipal("local", None, "local")
    app = FastAPI()
    app.include_router(setup_clarification_routes(store=store))
    with TestClient(app) as local_client:
        created = local_client.post(
            "/api/sessions/local-null/clarification", json={"request": _request()},
        )
        assert created.status_code == 200
        local_run = created.json()["run"]
        assert local_client.get(f"/api/clarifications/{local_run['clarification_id']}").status_code == 200
        assert local_client.get(f"/api/clarifications/{local_run['clarification_id']}/events").status_code == 200
        active = local_client.get("/api/sessions/local-null/clarification")
        assert active.status_code == 200 and active.json()["active"] is True
    assert store.list_active_runs(principal=local)
    assert store.list_active_metadata(principal=local)
    assert store.list_inbox_metadata(principal=local)
    assert build_session_clarification_attention(
        principal=local, session_id="local-null", store=store,
    )["active"] is True
    db = factory()
    try:
        local_row = db.query(ClarificationRun).filter(
            ClarificationRun.id == local_run["clarification_id"],
        ).one()
        assert local_row.owner == "local" and local_row.creator_subject_id is None
    finally:
        db.close()

    legacy = store.create_run(
        principal=local, session_id="legacy-null", request=_request(),
    ).run
    db = factory()
    try:
        row = db.query(ClarificationRun).filter(ClarificationRun.id == legacy["clarification_id"]).one()
        row.owner = "alice"
        row.creator_subject_id = None
        db.commit()
    finally:
        db.close()

    monkeypatch.setenv("AUTH_ENABLED", "true")
    unconfigured_dir = tmp_path / "unconfigured"
    unconfigured_dir.mkdir()
    unconfigured = AuthManager(str(unconfigured_dir / "auth.json"))
    unconfigured_app = FastAPI()
    unconfigured_app.state.auth_manager = unconfigured
    unconfigured_app.include_router(setup_clarification_routes(store=store))
    with TestClient(
        unconfigured_app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as loopback_client:
        assert loopback_client.get(
            f"/api/clarifications/{local_run['clarification_id']}",
        ).status_code == 200
        assert loopback_client.get(
            f"/api/clarifications/{local_run['clarification_id']}/events",
        ).status_code == 200
        loopback_active = loopback_client.get("/api/sessions/local-null/clarification")
        assert loopback_active.status_code == 200 and loopback_active.json()["active"] is True
        assert loopback_client.get(
            f"/api/clarifications/{local_run['clarification_id']}",
            headers={"X-Forwarded-For": "203.0.113.9"},
        ).status_code == 401

    configured_dir = tmp_path / "configured"
    configured_dir.mkdir()
    manager = AuthManager(str(configured_dir / "auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    configured_app = FastAPI()
    configured_app.state.auth_manager = manager
    configured_app.include_router(setup_clarification_routes(store=store))
    with TestClient(configured_app) as configured_client:
        configured_client.cookies.set("odysseus_session", token)
        assert configured_client.get(f"/api/clarifications/{local_run['clarification_id']}").status_code == 404
        assert configured_client.get(f"/api/clarifications/{local_run['clarification_id']}/events").status_code == 404
        assert configured_client.get(f"/api/clarifications/{legacy['clarification_id']}").status_code == 404
        assert configured_client.get(f"/api/clarifications/{legacy['clarification_id']}/events").status_code == 404
        for run in (local_run, legacy):
            denied_mutation = configured_client.post(
                f"/api/clarifications/{run['clarification_id']}/actions",
                json={"action": "pause", "expected_version": 1, "idempotency_key": f"no-adopt-{run['clarification_id']}"},
            )
            assert denied_mutation.status_code == 404
        for session_id in ("local-null", "legacy-null"):
            session_read = configured_client.get(f"/api/sessions/{session_id}/clarification")
            assert session_read.status_code == 200 and session_read.json()["active"] is False
    browser = SessionPrincipal("alice", manager.strict_session_identity(token)[1], "browser_session")
    assert store.list_active_runs(principal=browser) == ()
    assert store.list_active_metadata(principal=browser) == ()
    assert store.list_inbox_metadata(principal=browser) == ()
    for session_id in ("local-null", "legacy-null"):
        assert build_session_clarification_attention(
            principal=browser, session_id=session_id, store=store,
        )["active"] is False
