import base64
from contextlib import contextmanager
import json
from types import SimpleNamespace

import bcrypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.auth import AuthManager
from core.database import ApiToken, Base, Session as DbSession
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    McpContextReadLease,
    issue_mcp_bearer_proof,
)
from src.mcp_context_contract import (
    MCP_CONTEXT_PACK_SCHEMA,
    MCP_CONTEXT_SCOPE,
    McpContextContractError,
    decode_context_cursor,
    encode_context_cursor,
)
from src import mcp_context_cursor_authority as cursor_module
from src.mcp_context_cursor_authority import (
    McpContextCursorAuthority,
    McpContextCursorAuthorityError,
)


PACK = "pack.1"
SELECTION = "sha256:" + "1" * 64
SNAPSHOT = "sha256:" + "2" * 64
PAGE_BYTES = 16_384


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp-cursor-native")
    local_engine = create_engine(
        f"sqlite:///{root / 'cursor.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=local_engine)
    Base.metadata.create_all(local_engine)
    manager = AuthManager(str(root / "auth.json"))
    assert manager.setup("admin", "correct horse battery staple") is True
    sid = manager.subject_id_for_username("admin")
    raw = "ody_" + "a" * 43
    with LocalSession() as db:
        db.add(ApiToken(
            id="cursor-token",
            owner="admin",
            name="cursor fixture",
            token_hash=bcrypt.hashpw(raw.encode("ascii"), bcrypt.gensalt(rounds=4)).decode("ascii"),
            token_prefix=raw[:8],
            scopes=MCP_CONTEXT_SCOPE,
            owner_subject_id=sid,
            is_active=True,
        ))
        db.add(DbSession(
            id="chat.cursor", name="cursor fixture", endpoint_url="local",
            model="fixture", owner="admin",
        ))
        db.commit()
    state = SimpleNamespace(
        _token_cache_generation=1,
        _token_cache_dirty=False,
        mcp_context_plugin_enabled=True,
        allow_context_reads=True,
        _process_epoch="epoch_" + "b" * 32,
    )
    identity = McpAuthenticatedIdentity(
        "cursor-token", "admin", sid, 1,
        manager.security_action_revision(), MCP_CONTEXT_SCOPE,
    )
    native_clock = [100.0]
    authority = McpContextAuthority(
        manager,
        LocalSession,
        app_state=state,
        monotonic=lambda: native_clock[0],
        wall_time=lambda: native_clock[0],
    )
    proof = issue_mcp_bearer_proof(raw)
    binding = authority.issue_binding(identity, proof, "chat.cursor")
    lease = authority.issue_read_lease(binding, "request.cursor", ttl_seconds=900)
    fixture = SimpleNamespace(
        authority=authority, binding=binding, lease=lease, clock=native_clock,
        manager=manager, session_factory=LocalSession, state=state,
        identity=identity, raw=raw,
    )
    yield fixture
    authority.close()
    local_engine.dispose()


def _issue_kwargs(**changes):
    values = {
        "pack_id": PACK,
        "selection": SELECTION,
        "snapshot": SNAPSHOT,
        "target_index": 2,
        "offset": 17,
        "page_bytes": PAGE_BYTES,
    }
    values.update(changes)
    return values


def _verify_kwargs(**changes):
    values = {
        "pack_id": PACK,
        "selection": SELECTION,
        "snapshot": SNAPSHOT,
        "page_bytes": PAGE_BYTES,
    }
    values.update(changes)
    return values


def _alternate(char):
    return "A" if char != "A" else "B"


@contextmanager
def _isolated_native(native, *, request_id="request.isolated", ttl_seconds=900):
    clock = [100.0]
    authority = McpContextAuthority(
        native.manager,
        native.session_factory,
        app_state=native.state,
        monotonic=lambda: clock[0],
        wall_time=lambda: clock[0],
    )
    try:
        proof = issue_mcp_bearer_proof(native.raw)
        binding = authority.issue_binding(native.identity, proof, "chat.cursor")
        lease = authority.issue_read_lease(binding, request_id, ttl_seconds=ttl_seconds)
        yield SimpleNamespace(authority=authority, binding=binding, lease=lease, clock=clock)
    finally:
        authority.close()


def _signed_payload(cursor, native, kind, payload, *, aad=None):
    prefix = "m1" if kind == "manifest" else "c1"
    domain = cursor_module._MANIFEST_MAC_DOMAIN if kind == "manifest" else cursor_module._CONTENT_MAC_DOMAIN
    key_name = "_manifest_key" if kind == "manifest" else "_content_key"
    key = object.__getattribute__(cursor, key_name)
    binding = McpContextAuthority.recheck_lease(native.authority, native.lease)
    if aad is None:
        aad = cursor_module._binding_aad(binding)
    payload_segment = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    tag = cursor_module._b64encode(cursor_module._mac(key, domain, payload, aad))
    return f"{prefix}.{payload_segment}.{tag}"


def _assert_invalid(call):
    with pytest.raises(McpContextCursorAuthorityError) as error:
        call()
    assert error.value.code == "invalid_cursor"
    assert error.value.__cause__ is None
    assert "cursor-token" not in repr(error.value)


def test_both_domains_roundtrip_replay_and_have_no_capability_registry(native):
    clock = [100.0]
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: clock[0])
    before = (len(native.authority._bindings), len(native.authority._leases))
    manifest = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    content = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    assert manifest.startswith("m1.") and content.startswith("c1.")
    assert len(manifest) <= 2048 and len(content) <= 2048
    assert all(len(token.split(".")) == 3 and len(token.split(".")[2]) == 43 for token in (manifest, content))
    m1 = cursor.verify_manifest_cursor(manifest, native.lease, **_verify_kwargs())
    c1 = cursor.verify_content_cursor(content, native.lease, **_verify_kwargs())
    assert (m1.kind, c1.kind) == ("manifest", "content")
    assert m1.request_id == native.lease.request_id
    assert m1.recipient == native.binding.token_id
    assert m1.chat == native.binding.chat_session_id
    assert m1.process_epoch == native.binding.process_epoch
    assert m1.schema == MCP_CONTEXT_PACK_SCHEMA
    assert type(m1.expiry) is int and m1.expiry == 1000
    # Stateless same-token replay remains safe while the same native lease is live.
    assert cursor.verify_manifest_cursor(manifest, native.lease, **_verify_kwargs()) == m1
    assert (len(native.authority._bindings), len(native.authority._leases)) == before
    assert not any("replay" in name or "used" in name or "pending" in name for name in cursor.__slots__)


def test_keys_mac_domains_and_every_binding_aad_field_are_distinct(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    manifest_key = object.__getattribute__(cursor, "_manifest_key")
    content_key = object.__getattribute__(cursor, "_content_key")
    aad = cursor_module._binding_aad(native.binding)
    payload = b'{"canonical":"payload"}'
    manifest = cursor_module._mac(manifest_key, cursor_module._MANIFEST_MAC_DOMAIN, payload, aad)
    content = cursor_module._mac(content_key, cursor_module._CONTENT_MAC_DOMAIN, payload, aad)
    assert manifest_key != content_key and manifest != content
    for index in range(4):
        changed = list(aad)
        changed[index] += "x"
        assert cursor_module._mac(
            manifest_key, cursor_module._MANIFEST_MAC_DOMAIN, payload, tuple(changed)
        ) != manifest


@pytest.mark.parametrize(
    "index",
    (0, 1, 2, 3),
    ids=["owner_sid", "token_id", "chat_id", "process_epoch"],
)
def test_tokens_signed_for_wrong_binding_aad_are_rejected(native, index):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    payload = cursor_module._b64decode(token.split(".")[1])
    aad = list(cursor_module._binding_aad(native.binding))
    aad[index] += "x"
    signed = _signed_payload(cursor, native, "manifest", payload, aad=tuple(aad))
    _assert_invalid(lambda: cursor.verify_manifest_cursor(signed, native.lease, **_verify_kwargs()))


def test_cross_kind_unsigned_legacy_and_outer_tampering_are_rejected(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    manifest = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    content = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    _assert_invalid(lambda: cursor.verify_manifest_cursor(content, native.lease, **_verify_kwargs()))
    _assert_invalid(lambda: cursor.verify_content_cursor(manifest, native.lease, **_verify_kwargs()))
    legacy = manifest.split(".")[1]
    assert decode_context_cursor(legacy).kind == "manifest"
    _assert_invalid(lambda: cursor.verify_manifest_cursor(legacy, native.lease, **_verify_kwargs()))
    prefix, payload, tag = manifest.split(".")
    candidates = (
        f"x1.{payload}.{tag}",
        f"{prefix}.{_alternate(payload[0]) + payload[1:]}.{tag}",
        f"{prefix}.{payload}.{_alternate(tag[0]) + tag[1:]}",
        f"{prefix}.{payload}=.{tag}",
        f"{prefix}.{payload}.{tag}=",
        f"{prefix}.{payload}.{tag}.extra",
        "m1." + "A" * 2044 + "." + "A" * 43,
    )
    for candidate in candidates:
        _assert_invalid(lambda value=candidate: cursor.verify_manifest_cursor(value, native.lease, **_verify_kwargs()))


def test_every_payload_and_tag_byte_tamper_is_rejected(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    prefix, payload_segment, tag_segment = token.split(".")
    payload = cursor_module._b64decode(payload_segment)
    tag = cursor_module._b64decode(tag_segment)
    for index in range(len(payload)):
        changed = payload[:index] + bytes((payload[index] ^ 1,)) + payload[index + 1:]
        candidate = f"{prefix}.{cursor_module._b64encode(changed)}.{tag_segment}"
        _assert_invalid(
            lambda value=candidate: cursor.verify_manifest_cursor(
                value, native.lease, **_verify_kwargs()
            )
        )
    for index in range(len(tag)):
        changed = tag[:index] + bytes((tag[index] ^ 1,)) + tag[index + 1:]
        candidate = f"{prefix}.{payload_segment}.{cursor_module._b64encode(changed)}"
        _assert_invalid(
            lambda value=candidate: cursor.verify_manifest_cursor(
                value, native.lease, **_verify_kwargs()
            )
        )


def test_noncanonical_unpadded_base64_is_rejected(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    prefix, payload_segment, tag_segment = token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    final_index = alphabet.index(tag_segment[-1])
    assert final_index % 4 == 0
    noncanonical_tag = tag_segment[:-1] + alphabet[final_index + 1]
    assert base64.b64decode(
        noncanonical_tag + "=", altchars=b"-_", validate=True
    ) == cursor_module._b64decode(tag_segment)
    _assert_invalid(
        lambda: cursor.verify_manifest_cursor(
            f"{prefix}.{payload_segment}.{noncanonical_tag}",
            native.lease,
            **_verify_kwargs(),
        )
    )
    _assert_invalid(lambda: cursor_module._b64decode("AB"))


def test_invalid_mac_never_invokes_payload_decoder(native, monkeypatch):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    prefix, payload, tag = token.split(".")
    calls = []

    def forbidden_decoder(_value):
        calls.append(True)
        raise AssertionError("decoder must not run before MAC acceptance")

    monkeypatch.setattr(cursor_module, "decode_context_cursor", forbidden_decoder)
    candidate = f"{prefix}.{payload}.{_alternate(tag[0]) + tag[1:]}"
    _assert_invalid(lambda: cursor.verify_manifest_cursor(candidate, native.lease, **_verify_kwargs()))
    assert calls == []


def test_authenticated_duplicate_trailing_and_noncanonical_payloads_are_rejected(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    canonical = cursor_module._b64decode(token.split(".")[1])
    parsed = json.loads(canonical)
    reverse_order = dict(reversed(tuple(parsed.items())))
    noncanonical = json.dumps(reverse_order, separators=(",", ":")).encode("ascii")
    duplicate = canonical.replace(b'"chat":', b'"chat":"chat.cursor","chat":', 1)
    variants = (
        canonical + b" ",
        duplicate,
        noncanonical,
        canonical + b"{}",
    )
    for payload in variants:
        signed = _signed_payload(cursor, native, "manifest", payload)
        _assert_invalid(lambda value=signed: cursor.verify_manifest_cursor(value, native.lease, **_verify_kwargs()))


def test_new_instance_and_close_invalidate_process_ephemeral_tokens(native):
    issuer = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = issuer.issue_content_cursor(native.lease, **_issue_kwargs())
    restarted = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    _assert_invalid(lambda: restarted.verify_content_cursor(token, native.lease, **_verify_kwargs()))
    issuer.close()
    with pytest.raises(McpContextCursorAuthorityError) as error:
        issuer.verify_content_cursor(token, native.lease, **_verify_kwargs())
    assert error.value.code == "context_unavailable"
    with pytest.raises(McpContextCursorAuthorityError) as error:
        issuer.issue_content_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable"


def test_expiry_is_strict_and_replay_requires_a_current_cursor_expiry(native):
    clock = [100.0]
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: clock[0])
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    assert cursor.verify_content_cursor(token, native.lease, **_verify_kwargs()).expiry == 1000
    clock[0] = 1000.0
    _assert_invalid(lambda: cursor.verify_content_cursor(token, native.lease, **_verify_kwargs()))


def test_expiry_uses_both_minimum_branches_and_floors_fractional_values(native):
    with _isolated_native(native, request_id="request.short", ttl_seconds=300.75) as isolated:
        cursor = McpContextCursorAuthority(isolated.authority, wall_time=lambda: 100.25)
        token = cursor.issue_manifest_cursor(isolated.lease, **_issue_kwargs())
        assert cursor.verify_manifest_cursor(
            token, isolated.lease, **_verify_kwargs()
        ).expiry == 400
    with _isolated_native(native, request_id="request.cap", ttl_seconds=900) as isolated:
        cursor = McpContextCursorAuthority(isolated.authority, wall_time=lambda: 99.75)
        token = cursor.issue_manifest_cursor(isolated.lease, **_issue_kwargs())
        assert cursor.verify_manifest_cursor(
            token, isolated.lease, **_verify_kwargs()
        ).expiry == 999


@pytest.mark.parametrize(
    "bad",
    (True, float("nan"), float("inf"), -float("inf")),
    ids=["bool", "nan", "positive_inf", "negative_inf"],
)
def test_clock_non_builtin_bool_and_nonfinite_values_fail_closed(native, bad):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: bad)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable"
    assert error.value.__cause__ is None


def test_huge_integer_clock_and_lease_expiry_fail_with_fixed_context_error(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 10**10_000)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable" and error.value.__cause__ is None

    lease = object.__new__(McpContextReadLease)
    object.__setattr__(lease, "_McpContextReadLease__request_id", "request.hostile")
    object.__setattr__(lease, "_McpContextReadLease__deadline_wall", 10**10_000)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor_module._lease_projection(lease)
    assert error.value.code == "context_unavailable" and error.value.__cause__ is None


def test_raising_and_regressing_clocks_fail_without_raw_causes(native):
    def raising():
        raise RuntimeError("secret clock detail")
    cursor = McpContextCursorAuthority(native.authority, wall_time=raising)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable" and error.value.__cause__ is None
    clock = [100.0]
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: clock[0])
    cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    clock[0] = 99.0
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable"


def test_hostile_wall_clock_close_is_fenced_before_issue(native):
    holder = []
    def close_during_clock():
        holder[0].close()
        return 100.0
    cursor = McpContextCursorAuthority(native.authority, wall_time=close_during_clock)
    holder.append(cursor)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs())
    assert error.value.code == "context_unavailable"


class _StringChild(str):
    pass


class _IntegerChild(int):
    pass


@pytest.mark.parametrize("field,value", (
    ("pack_id", _StringChild(PACK)),
    ("selection", _StringChild(SELECTION)),
    ("snapshot", _StringChild(SNAPSHOT)),
    ("target_index", True),
    ("target_index", _IntegerChild(1)),
    ("offset", True),
    ("offset", _IntegerChild(1)),
    ("page_bytes", True),
    ("page_bytes", _IntegerChild(1)),
), ids=[
    "pack_string_subclass",
    "selection_string_subclass",
    "snapshot_string_subclass",
    "target_bool",
    "target_int_subclass",
    "offset_bool",
    "offset_int_subclass",
    "page_bytes_bool",
    "page_bytes_int_subclass",
])
def test_issue_accepts_only_exact_builtin_scalars(native, field, value):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_manifest_cursor(native.lease, **_issue_kwargs(**{field: value}))
    assert error.value.code == "invalid_request"


def test_exact_native_authority_and_final_cursor_class(native):
    class NativeChild(McpContextAuthority):
        pass
    child = object.__new__(NativeChild)
    with pytest.raises(ValueError):
        McpContextCursorAuthority(child)
    with pytest.raises(TypeError):
        class CursorChild(McpContextCursorAuthority):
            pass
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    assert repr(cursor) == "<McpContextCursorAuthority redacted>"


def test_native_auth_denial_is_preserved_after_bounded_outer_parse(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    with pytest.raises(McpContextAuthorityError) as error:
        cursor.verify_content_cursor(token, object(), **_verify_kwargs())
    assert error.value.code == "unauthorized"
    assert error.value.__cause__ is None


def test_cancel_revocation_and_native_expiry_after_issue_are_rechecked(native):
    with _isolated_native(native, request_id="request.cancel") as isolated:
        cursor = McpContextCursorAuthority(isolated.authority, wall_time=lambda: 100.0)
        token = cursor.issue_content_cursor(isolated.lease, **_issue_kwargs())
        isolated.authority.cancel_lease(isolated.lease)
        with pytest.raises(McpContextAuthorityError) as error:
            cursor.verify_content_cursor(token, isolated.lease, **_verify_kwargs())
        assert error.value.code == "cancelled" and error.value.__cause__ is None

    with _isolated_native(native, request_id="request.revoked") as isolated:
        cursor = McpContextCursorAuthority(isolated.authority, wall_time=lambda: 100.0)
        token = cursor.issue_content_cursor(isolated.lease, **_issue_kwargs())
        native.state.allow_context_reads = False
        try:
            with pytest.raises(McpContextAuthorityError) as error:
                cursor.verify_content_cursor(token, isolated.lease, **_verify_kwargs())
            assert error.value.code == "disabled" and error.value.__cause__ is None
        finally:
            native.state.allow_context_reads = True

    with _isolated_native(native, request_id="request.expired") as isolated:
        cursor = McpContextCursorAuthority(isolated.authority, wall_time=lambda: 100.0)
        token = cursor.issue_content_cursor(isolated.lease, **_issue_kwargs())
        isolated.clock[0] = 1000.0
        with pytest.raises(McpContextAuthorityError) as error:
            cursor.verify_content_cursor(token, isolated.lease, **_verify_kwargs())
        assert error.value.code == "timeout" and error.value.__cause__ is None


def test_final_native_recheck_catches_cancel_from_wall_callback(native):
    with _isolated_native(native, request_id="request.final-race") as isolated:
        cancel = [False]

        def wall_time():
            if cancel[0]:
                cancel[0] = False
                isolated.authority.cancel_lease(isolated.lease)
            return 100.0

        cursor = McpContextCursorAuthority(isolated.authority, wall_time=wall_time)
        token = cursor.issue_content_cursor(isolated.lease, **_issue_kwargs())
        cancel[0] = True
        with pytest.raises(McpContextAuthorityError) as error:
            cursor.verify_content_cursor(token, isolated.lease, **_verify_kwargs())
        assert error.value.code == "cancelled" and error.value.__cause__ is None


@pytest.mark.parametrize(
    "changes",
    (
        {"pack_id": "pack.other"},
        {"selection": "sha256:" + "3" * 64},
        {"snapshot": "sha256:" + "4" * 64},
        {"page_bytes": PAGE_BYTES - 1},
    ),
    ids=["pack_id", "selection", "snapshot", "page_bytes"],
)
def test_verify_rejects_wrong_current_inputs(native, changes):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    _assert_invalid(
        lambda: cursor.verify_content_cursor(
            token, native.lease, **_verify_kwargs(**changes)
        )
    )


def test_verify_rejects_authenticated_wrong_request_id(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    payload = json.loads(cursor_module._b64decode(token.split(".")[1]))
    payload["request_id"] = "request.other"
    signed = _signed_payload(
        cursor,
        native,
        "content",
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    _assert_invalid(lambda: cursor.verify_content_cursor(signed, native.lease, **_verify_kwargs()))


@pytest.mark.parametrize(
    "pack_id",
    ("p" * 513, "p" * 100_000, "p\N{LATIN SMALL LETTER A WITH DIAERESIS}ck"),
    ids=["over_512", "very_large", "non_ascii"],
)
def test_verify_pack_id_is_bounded_before_matching(native, pack_id):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    _assert_invalid(
        lambda: cursor.verify_content_cursor(
            token, native.lease, **_verify_kwargs(pack_id=pack_id)
        )
    )


def test_mutated_frozen_envelope_is_revalidated(native):
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs())
    envelope = decode_context_cursor(token.split(".")[1])
    object.__setattr__(envelope, "offset", True)
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor_module._canonical_envelope_segment(envelope)
    assert error.value.code == "invalid_cursor"
    with pytest.raises(McpContextContractError):
        encode_context_cursor(envelope)


def test_phase_b_json_safe_offset_boundary_and_exact_integer_expiry(native):
    """Final four-path contract: Phase B raises the legacy 2**31 offset cap."""
    cursor = McpContextCursorAuthority(native.authority, wall_time=lambda: 100.0)
    token = cursor.issue_content_cursor(native.lease, **_issue_kwargs(offset=2**53 - 1))
    assert cursor.verify_content_cursor(token, native.lease, **_verify_kwargs()).offset == 2**53 - 1
    with pytest.raises(McpContextCursorAuthorityError) as error:
        cursor.issue_content_cursor(native.lease, **_issue_kwargs(offset=2**53))
    assert error.value.code == "invalid_request"

    payload = json.loads(cursor_module._b64decode(token.split(".")[1]))
    payload["expiry"] = float(payload["expiry"])
    signed = _signed_payload(
        cursor, native, "content",
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    _assert_invalid(lambda: cursor.verify_content_cursor(signed, native.lease, **_verify_kwargs()))
