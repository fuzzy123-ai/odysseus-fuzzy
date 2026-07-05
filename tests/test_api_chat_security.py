import ipaddress
import importlib.util
import sys
import types
from pathlib import Path

import pytest
import httpx


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://10.0.0.5/v1",
    "http://172.16.0.1/v1",
    "http://192.168.1.2/v1",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/",
    "http://[::1]:8000/v1",
    "http://[fc00::1]/v1",
    "http://224.0.0.1/v1",
    "http://0.0.0.0/v1",
    "file:///etc/passwd",
])
def test_public_url_validator_blocks_internal_targets(url):
    from src.url_security import is_public_http_url

    assert is_public_http_url(url) is False


def test_public_url_validator_allows_public_endpoint(monkeypatch):
    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )

    assert url_security.validate_public_http_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_public_url_validator_blocks_dns_to_private(monkeypatch):
    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("10.0.0.5")],
    )

    with pytest.raises(ValueError):
        url_security.validate_public_http_url("https://api.example.com/v1")


def test_pinned_public_transport_rejects_rebind_between_validation_and_pin(monkeypatch):
    from src import url_security

    answers = [
        [ipaddress.ip_address("93.184.216.34")],
        [ipaddress.ip_address("10.0.0.5")],
    ]

    def _resolve(_host):
        return answers.pop(0)

    monkeypatch.setattr(url_security, "_resolve_hostname_ips", _resolve)

    with pytest.raises(ValueError):
        url_security.PinnedPublicHttpTransport.for_url("https://api.example.com/v1")


@pytest.mark.asyncio
async def test_pinned_public_transport_rewrites_to_resolved_ip_without_new_dns(monkeypatch):
    from src import url_security

    resolve_calls = []

    def _resolve(host):
        resolve_calls.append(host)
        if len(resolve_calls) > 2:
            raise AssertionError("pinned transport must not resolve DNS during request send")
        return [ipaddress.ip_address("93.184.216.34")]

    captured = []

    async def _handler(request):
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(url_security, "_resolve_hostname_ips", _resolve)
    endpoint = url_security.PinnedPublicHttpTransport.for_url("https://api.example.com/v1")
    transport = url_security.PinnedPublicHttpTransport(
        endpoint.endpoint,
        transport=httpx.MockTransport(_handler),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://api.example.com/v1/chat/completions", json={"hello": "world"})

    assert response.status_code == 200
    assert captured[0].url.host == "93.184.216.34"
    assert captured[0].headers["host"] == "api.example.com"
    assert captured[0].extensions["sni_hostname"] == "api.example.com"
    assert resolve_calls == ["api.example.com", "api.example.com"]


def test_sync_pinned_public_transport_rewrites_to_resolved_ip_without_new_dns(monkeypatch):
    from src import url_security

    resolve_calls = []

    def _resolve(host):
        resolve_calls.append(host)
        if len(resolve_calls) > 2:
            raise AssertionError("pinned transport must not resolve DNS during request send")
        return [ipaddress.ip_address("93.184.216.34")]

    captured = []

    def _handler(request):
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(url_security, "_resolve_hostname_ips", _resolve)
    endpoint = url_security.PinnedPublicHttpSyncTransport.for_url("https://api.example.com/v1")
    transport = url_security.PinnedPublicHttpSyncTransport(
        endpoint.endpoint,
        transport=httpx.MockTransport(_handler),
    )
    with httpx.Client(transport=transport) as client:
        response = client.get("https://api.example.com/v1/models")

    assert response.status_code == 200
    assert captured[0].url.host == "93.184.216.34"
    assert captured[0].headers["host"] == "api.example.com"
    assert captured[0].extensions["sni_hostname"] == "api.example.com"
    assert resolve_calls == ["api.example.com", "api.example.com"]


def _load_webhook_routes_for_test(monkeypatch):
    # Load under a unique module name so each test gets a fresh module object
    # rather than a cached one from a previous monkeypatch run.
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = []
    core_db = types.ModuleType("core.database")
    core_db.SessionLocal = object
    core_db.Webhook = object
    core_db.ModelEndpoint = object
    core_middleware = types.ModuleType("core.middleware")
    core_middleware.require_admin = lambda request: None
    webhook_manager = types.ModuleType("src.webhook_manager")
    webhook_manager.WebhookManager = object
    webhook_manager.validate_webhook_url = lambda url: url
    webhook_manager.validate_events = lambda events: events

    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.database", core_db)
    monkeypatch.setitem(sys.modules, "core.middleware", core_middleware)
    monkeypatch.setitem(sys.modules, "src.webhook_manager", webhook_manager)

    module_name = "routes.webhook_routes_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parent.parent / "routes" / "webhook_routes.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Expr:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, row):
        return self.fn(row)

    def __or__(self, other):
        return _Expr(lambda row: self(row) or other(row))


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Expr(lambda row: getattr(row, self.name) == other)

    def desc(self):
        return ("desc", self.name)


class _ModelEndpoint:
    is_enabled = _Column("is_enabled")
    owner = _Column("owner")
    created_at = _Column("created_at")


class _Endpoint:
    def __init__(
        self,
        *,
        owner,
        is_enabled=True,
        created_at=1,
        base_url="https://api.example.com/v1",
        api_key=None,
    ):
        self.owner = owner
        self.is_enabled = is_enabled
        self.created_at = created_at
        self.base_url = base_url
        self.api_key = api_key


class _EndpointQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.orders = []

    def filter(self, *exprs):
        self.filters.extend(exprs)
        return self

    def order_by(self, *exprs):
        self.orders.extend(exprs)
        return self

    def first(self):
        rows = self.rows
        for expr in self.filters:
            rows = [row for row in rows if expr(row)]
        # Apply sort keys right-to-left so the leftmost key ends up as the
        # primary sort (stable-sort reversal idiom mirrors SQLAlchemy's
        # multi-column ORDER BY behaviour).
        for order in reversed(self.orders):
            reverse = False
            name = getattr(order, "name", None)
            if isinstance(order, tuple) and order[0] == "desc":
                reverse = True
                name = order[1]
            rows = sorted(rows, key=lambda row: getattr(row, name) is not None, reverse=reverse)
            if name != "owner":
                rows = sorted(rows, key=lambda row: getattr(row, name), reverse=reverse)
        return rows[0] if rows else None


class _DB:
    def __init__(self, rows):
        self.query_obj = _EndpointQuery(rows)
        self.closed = False

    def query(self, model):
        assert model is _ModelEndpoint
        return self.query_obj

    def close(self):
        self.closed = True


class _ChatSession:
    def __init__(self, endpoint_url, model):
        self.endpoint_url = endpoint_url
        self.model = model
        self.headers = {}
        self.history = []

    def add_message(self, message):
        self.history.append(message)


class _SessionManager:
    def __init__(self):
        self.created = []
        self.save_calls = 0

    def create_session(self, *, session_id, name, endpoint_url, model, owner):
        session = _ChatSession(endpoint_url, model)
        self.created.append({
            "session_id": session_id,
            "name": name,
            "endpoint_url": endpoint_url,
            "model": model,
            "owner": owner,
            "session": session,
        })
        return session

    def save_sessions(self):
        self.save_calls += 1


class _Request:
    def __init__(self, *, owner="alice"):
        self.state = types.SimpleNamespace(
            api_token=True,
            api_token_scopes=["chat"],
            api_token_owner=owner,
        )


class _WebhookManager:
    async def fire(self, event, payload):
        return None

    def fire_and_forget(self, event, payload):
        return None


def _install_sync_chat_stubs(monkeypatch):
    # FastAPI checks for python_multipart at import time when Form is used;
    # stub it so the optional dependency is not required in the test environment.
    python_multipart = types.ModuleType("python_multipart")
    python_multipart.__version__ = "0.0.13"
    core_models = types.ModuleType("core.models")

    class _ChatMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    async def _llm_call_async(endpoint_url, model, messages, headers=None, timeout=None, **kwargs):
        return "mocked response"

    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.normalize_base = lambda url: (url or "").strip().rstrip("/")
    endpoint_resolver.build_chat_url = lambda base_url: f"{base_url}/chat/completions"
    endpoint_resolver.build_models_url = lambda base_url: f"{base_url}/models"
    endpoint_resolver.build_headers = lambda api_key, base_url: {"Authorization": f"Bearer {api_key}"}

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = _llm_call_async
    core_models.ChatMessage = _ChatMessage

    monkeypatch.setitem(sys.modules, "python_multipart", python_multipart)
    monkeypatch.setitem(sys.modules, "core.models", core_models)
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)


def _sync_chat_endpoint(webhook_routes, session_manager):
    router = webhook_routes.setup_webhook_routes(
        _WebhookManager(),
        auth_manager=None,
        session_manager=session_manager,
    )
    for route in router.routes:
        if route.path == "/api/v1/chat":
            return route.endpoint
    raise AssertionError("sync chat route not found")


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:11434/v1",
    "http://localhost:11434/v1",
    "http://10.0.0.5/v1",
    "http://169.254.169.254/latest/meta-data/",
])
@pytest.mark.asyncio
async def test_api_chat_direct_base_url_rejects_local_private_targets(monkeypatch, base_url):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)

    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url=base_url,
        model="test-model",
        provider=None,
        session=None,
    )

    with pytest.raises(webhook_routes.HTTPException) as exc:
        await sync_chat(_Request(), body)

    assert exc.value.status_code == 400
    assert exc.value.detail == "base_url must point to a public HTTP(S) endpoint"
    assert session_manager.created == []


@pytest.mark.asyncio
async def test_api_chat_direct_base_url_allows_mocked_public_endpoint(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_API_TOKEN_DIRECT_BASE_URL_ENABLED", "true")
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)

    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(), body)

    assert response["response"] == "mocked response"
    assert response["model"] == "test-model"
    assert session_manager.created[0]["endpoint_url"] == "https://api.example.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_api_chat_direct_base_url_passes_pinned_transport_to_llm(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_API_TOKEN_DIRECT_BASE_URL_ENABLED", "true")
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    sentinel_transport = object()
    captured = {}

    class _TransportFactory:
        @staticmethod
        def for_url(url):
            captured["for_url"] = url
            return sentinel_transport

    async def _llm_call_async(endpoint_url, model, messages, headers=None, timeout=None, **kwargs):
        captured["endpoint_url"] = endpoint_url
        captured["transport"] = kwargs.get("transport")
        return "mocked response"

    monkeypatch.setattr(webhook_routes, "PinnedPublicHttpTransport", _TransportFactory)
    sys.modules["src.llm_core"].llm_call_async = _llm_call_async

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(), body)

    assert response["response"] == "mocked response"
    assert captured["for_url"] == "https://api.example.com/v1"
    assert captured["endpoint_url"] == "https://api.example.com/v1/chat/completions"
    assert captured["transport"] is sentinel_transport


@pytest.mark.asyncio
async def test_llm_async_call_uses_direct_pinned_transport_for_untrusted_url():
    from src.llm_async_call import llm_call_async_impl
    from src.url_security import PinnedPublicHttpEndpoint, PinnedPublicHttpTransport

    captured = []

    async def _handler(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    transport = PinnedPublicHttpTransport(
        PinnedPublicHttpEndpoint(
            url="https://api.example.com/v1",
            hostname="api.example.com",
            port=None,
            pinned_ips=("93.184.216.34",),
        ),
        transport=httpx.MockTransport(_handler),
    )

    async def _post(client, url, headers, **kwargs):
        return await client.post(url, headers=headers, **kwargs)

    def _shared_client():
        raise AssertionError("direct token URL must not use the shared DNS-resolving client")

    result = await llm_call_async_impl(
        "https://api.example.com/v1/chat/completions",
        "test-model",
        [{"role": "user", "content": "hello"}],
        temperature=1.0,
        max_tokens=0,
        headers={"Authorization": "Bearer test"},
        timeout=5,
        max_retries=1,
        session_id=None,
        retry_delay=0,
        dead_host_cooldown=1,
        http_exception_cls=RuntimeError,
        connect_error_classes=(httpx.ConnectError, httpx.ConnectTimeout),
        request_error_classes=(httpx.RequestError, httpx.HTTPStatusError),
        logger=types.SimpleNamespace(debug=lambda *_: None, warning=lambda *_: None, info=lambda *_: None),
        detect_provider_func=lambda _url: "openai",
        sanitize_messages_func=lambda messages: messages,
        visible_reasoning_guard_func=lambda messages, _model: messages,
        get_cache_key_func=lambda *_args: "cache-key",
        get_cached_response_func=lambda _key: None,
        set_cached_response_func=lambda _key, _value: None,
        stream_llm_func=None,
        normalize_anthropic_url_func=lambda url: url,
        build_anthropic_headers_func=lambda headers: headers or {},
        build_anthropic_payload_func=lambda *_args, **_kwargs: {},
        normalize_ollama_url_func=lambda url: url,
        build_ollama_payload_func=lambda *_args, **_kwargs: {},
        get_context_length_func=lambda *_args: 4096,
        provider_headers_func=lambda _provider, headers: headers or {},
        omit_temperature_func=lambda *_args: False,
        uses_max_completion_tokens_func=lambda _model: False,
        is_ollama_openai_compat_url_func=lambda _url: False,
        supports_thinking_func=lambda _model: False,
        mistral_reasoning_effort="low",
        apply_local_cache_affinity_func=lambda *_args: None,
        is_host_dead_func=lambda _url: False,
        host_key_func=lambda url: url,
        call_timeout_func=lambda _timeout: httpx.Timeout(5.0),
        note_model_activity_func=lambda *_args: None,
        get_http_client_func=_shared_client,
        httpx_post_async_func=_post,
        format_upstream_error_func=lambda status, text, url: text,
        clear_host_dead_func=lambda _url: None,
        parse_anthropic_response_func=lambda data: "",
        parse_ollama_response_func=lambda data: "",
        normalize_mistral_content_func=lambda content: (content, ""),
        parse_openai_message_func=lambda message, **_kwargs: message["content"],
        mark_host_dead_func=lambda _url: False,
        direct_transport=transport,
    )

    assert result == "ok"
    assert captured[0].url.host == "93.184.216.34"
    assert captured[0].headers["host"] == "api.example.com"
    assert captured[0].extensions["sni_hostname"] == "api.example.com"


@pytest.mark.asyncio
async def test_api_chat_direct_base_url_rejects_public_endpoint_by_default(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_API_TOKEN_DIRECT_BASE_URL_ENABLED", raising=False)
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)

    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider=None,
        session=None,
    )

    with pytest.raises(webhook_routes.HTTPException) as exc:
        await sync_chat(_Request(), body)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Direct base_url is disabled for API tokens"
    assert session_manager.created == []


def test_api_chat_fallback_endpoint_selection_for_owned_token(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    rows = [
        _Endpoint(owner="alice", is_enabled=False, created_at=0),
        _Endpoint(owner="bob", created_at=0),
        _Endpoint(owner=None, created_at=1),
        _Endpoint(owner="alice", created_at=2),
    ]

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)

    selected = webhook_routes._select_api_chat_fallback_endpoint(_DB(rows), "alice")

    assert selected.owner == "alice"
    assert selected.is_enabled is True
    assert selected.created_at == 2


def test_api_chat_fallback_without_owner_uses_shared_only(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    rows = [
        _Endpoint(owner="alice", created_at=0),
        _Endpoint(owner=None, is_enabled=False, created_at=1),
        _Endpoint(owner=None, created_at=2),
    ]

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)

    selected = webhook_routes._select_api_chat_fallback_endpoint(_DB(rows), None)

    assert selected.owner is None
    assert selected.is_enabled is True
    assert selected.created_at == 2


@pytest.mark.asyncio
async def test_api_chat_fallback_trusts_configured_local_endpoint(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    local_endpoint = _Endpoint(
        owner=None,
        base_url="http://localhost:11434/v1",
        api_key="configured-key",
    )
    db = _DB([local_endpoint])
    calls = []

    def _session_local():
        return db

    def _validate_public_http_url(url, *, max_length=2048):
        calls.append(url)
        raise AssertionError("configured fallback endpoint should not be publicly validated")

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)
    monkeypatch.setattr(webhook_routes, "SessionLocal", _session_local)
    monkeypatch.setattr(webhook_routes, "validate_public_http_url", _validate_public_http_url)

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        model="local-model",
        api_key=None,
        base_url=None,
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(owner=None), body)

    assert response["response"] == "mocked response"
    assert response["model"] == "local-model"
    assert session_manager.created[0]["endpoint_url"] == "http://localhost:11434/v1/chat/completions"
    assert calls == []
