from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    HarbourAuthorityError,
    TrustedPrincipal,
    authenticate_principal,
    require_owner,
)


NOW = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
CONTEXT = "sha256:" + "1" * 64


def _issuer(*, secret: bytes = b"a" * 32) -> AuthenticatedPrincipalIssuer:
    return AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="key-1",
        secret=secret,
        clock=lambda: NOW,
    )


def test_trusted_principal_is_sealed_immutable_and_content_free() -> None:
    issuer = _issuer()
    principal = issuer.issue(
        subject_id="alice-user",
        owner_id="alice",
        auth_context_digest=CONTEXT,
        nonce="nonce-0001",
    )

    with pytest.raises(HarbourAuthorityError) as direct:
        TrustedPrincipal(
            issuer_id="odysseus-auth",
            key_id="key-1",
            subject_id="alice-user",
            owner_id="alice",
            auth_context_digest=CONTEXT,
            issued_at=principal.issued_at,
            expires_at=principal.expires_at,
            nonce="nonce-0001",
            attestation=principal.attestation,
        )
    assert direct.value.code == "unauthenticated"

    with pytest.raises(TypeError):
        type("ForgedPrincipal", (TrustedPrincipal,), {})
    with pytest.raises(AttributeError):
        principal._owner_id = "bob"
    assert "alice" not in repr(principal)
    assert principal.attestation not in repr(principal)


def test_principal_verification_returns_only_persistence_safe_reference() -> None:
    issuer = _issuer()
    principal = issuer.issue(
        subject_id="alice-user",
        owner_id="alice",
        auth_context_digest=CONTEXT,
        nonce="nonce-0002",
    )
    reference = authenticate_principal(
        principal,
        trusted_issuers=(issuer,),
        now=NOW + timedelta(seconds=1),
    )

    assert reference.owner_id == "alice"
    assert reference.subject_id == "alice-user"
    assert reference.principal_digest.startswith("sha256:")
    assert not hasattr(reference, "attestation")
    assert not hasattr(reference, "key_id")
    assert "alice" not in repr(reference)
    assert require_owner(reference, "alice") == "alice"


@pytest.mark.parametrize("hostile", ["alice", {"owner_id": "alice"}, None])
def test_strings_mappings_and_missing_principals_fail_unauthenticated(hostile: object) -> None:
    with pytest.raises(HarbourAuthorityError) as raised:
        authenticate_principal(hostile, trusted_issuers=(_issuer(),), now=NOW)
    assert raised.value.code == "unauthenticated"
    assert raised.value.__cause__ is None


def test_wrong_issuer_tampering_and_expiry_fail_unauthenticated() -> None:
    issuer = _issuer()
    principal = issuer.issue(
        subject_id="alice-user",
        owner_id="alice",
        auth_context_digest=CONTEXT,
        ttl_seconds=2,
        nonce="nonce-0003",
    )

    with pytest.raises(HarbourAuthorityError) as wrong_issuer:
        authenticate_principal(
            principal,
            trusted_issuers=(_issuer(secret=b"b" * 32),),
            now=NOW,
        )
    assert wrong_issuer.value.code == "unauthenticated"

    with pytest.raises(HarbourAuthorityError) as expired:
        authenticate_principal(
            principal,
            trusted_issuers=(issuer,),
            now=NOW + timedelta(seconds=2),
        )
    assert expired.value.code == "unauthenticated"

    object.__setattr__(principal, "_owner_id", "bob")
    with pytest.raises(HarbourAuthorityError) as tampered:
        authenticate_principal(principal, trusted_issuers=(issuer,), now=NOW)
    assert tampered.value.code == "unauthenticated"


def test_owner_substitution_is_denied_without_detail() -> None:
    issuer = _issuer()
    reference = authenticate_principal(
        issuer.issue(
            subject_id="alice-user",
            owner_id="alice",
            auth_context_digest=CONTEXT,
            nonce="nonce-0004",
        ),
        trusted_issuers=(issuer,),
        now=NOW,
    )
    with pytest.raises(HarbourAuthorityError) as raised:
        require_owner(reference, "bob")
    assert raised.value.code == "owner_scope_denied"
    assert raised.value.args == ("owner_scope_denied",)

