#!/usr/bin/env python3
"""Fail-closed, redacted provenance collector for one Compose candidate.

The module is intentionally inert by default.  It has no import-time side
effects and does not contact a network unless its command line receives both
``--execute`` and the exact, future ledger binding declared below.  Unit tests
use an injected ``Fetcher`` with synthetic responses; that is the only
supported offline fixture mode.

No response body, header value, URL, exception text, or provider output is
included in the result.  The public result is a small canonical JSON envelope
whose digest covers every disclosed field.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as element_tree
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_compose_candidate_provenance.v1"
CANDIDATE_SCHEMA = "odysseus.compose_candidate_selection.v1"
RESULT_SCHEMA = "odysseus.compose_candidate_selection_result.v1"

# This is an identifier for a future, separately recorded live-go ledger.  It
# is *not* a grant by itself.  A caller must bind this exact value and a
# non-expired timestamp when --execute is used.
FUTURE_GRANT_ID = "SEC159-COMPOSE-CANDIDATE-PROVENANCE-EGRESS-RECOVERY-GO"

SUBJECT = "containers/podman-compose upstream project"
USER_AGENT = "Odysseus-Redacted-Provenance/1.0"
REQUEST_TIMEOUT_SECONDS = 10.0

PYPI_JSON_URL = "https://pypi.org/pypi/podman-compose/json"
GITHUB_ATOM_URL = "https://github.com/containers/podman-compose/commits/v{version}.atom"
RAW_README_URL = "https://raw.githubusercontent.com/containers/podman-compose/{commit}/README.md"
RAW_SOURCE_URL = "https://raw.githubusercontent.com/containers/podman-compose/{commit}/podman_compose.py"
OFFLINE_FIXTURE_CONTRACT = "SEC155-Gate-B-offline-synthetic-fixture-contract"
AST_PROOF_CONTRACT = "SEC155-Gate-B-deterministic-ast-proof-contract"

_HEX = frozenset("0123456789abcdef")
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_VERSION = re.compile(r"^[0-9][0-9A-Za-z._-]{0,63}$")
_SAFE_FILENAME = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,255}$")
_RAW_PATH = re.compile(r"^/containers/podman-compose/[0-9a-fA-F]{40}/[^/].+$")
_CANDIDATE_STATUSES = frozenset({"unselected", "eligible", "rejected", "blocked"})
_OUTER_STATUSES = frozenset({"not_executed", "completed", "blocked"})
_EXECUTION_STATUSES = frozenset({
    "invalid_limits", "attempt_already_consumed", "subject_boundary_violation", "fixture_mode_required",
    "execution_disabled", "invalid_or_expired_grant", "completed", "request_boundary_violation",
    "time_budget_exceeded", "request_budget_exceeded", "page_budget_exceeded", "origin_budget_exceeded",
    "fetch_error", "invalid_fetch_response", "redirect_invalid", "redirect_boundary_violation",
    "redirect_limit_exceeded", "unexpected_http_status", "content_type_rejected", "body_unavailable",
    "body_too_large", "body_budget_exceeded", "aggregate_budget_exceeded", "malformed_pypi_json",
    "release_mapping_missing", "release_sha256_missing_or_ambiguous", "malformed_tag_atom",
    "tag_entry_missing", "tag_commit_missing_or_ambiguous", "malformed_readme", "readme_subject_marker_missing",
    "malformed_provider_source", "provider_chain_incomplete",
})

CANDIDATE_RECORD_KEYS = frozenset({
    "schema", "candidate_status", "implementation_identity", "supported_distribution_channel",
    "entrypoint_provider_chain", "package_or_artifact_identity", "version", "architecture",
    "immutable_identity", "approved_repository_or_channel", "signature_or_key_verification_mechanism",
    "installed_identity_predicates", "offline_fixture_contract", "ast_proof_contract",
    "decision_authority", "provenance_evidence", "rejection_or_block_reason",
})
RESULT_ENVELOPE_KEYS = frozenset({
    "schema", "candidate_status", "required_field_status", "provider_chain_status",
    "immutable_identity_status", "signature_verification_status", "offline_fixture_status",
    "ast_no_deps_service_only_status", "ast_dependency_expansion_status", "service_selection_status",
    "no_build_status", "force_recreate_status", "debian_1_3_0_adverse_status",
    "debian_1_3_0_missing_proof", "evidence_reference", "stop_reason",
})
AUDIT_KEYS = frozenset({
    "attempts_consumed", "retry_permitted", "request_count", "page_count", "body_count",
    "origin_count", "body_bytes", "execution_status", "request_budget_status",
    "page_budget_status", "body_budget_status", "aggregate_budget_status", "origin_budget_status",
    "time_budget_status", "redaction_status", "network_mode",
})
ENVELOPE_KEYS = frozenset({"schema_id", "status", "candidate_record", "result_envelope", "audit", "evidence_sha256"})


@dataclass(frozen=True)
class ProvenanceLimits:
    """Upper bounds from SEC156/SEC157; lower values are useful in tests."""

    max_requests: int = 12
    max_pages: int = 8
    max_bodies: int = 4
    max_body_bytes: int = 524_288
    max_aggregate_bytes: int = 2_097_152
    max_origins: int = 3
    max_seconds: int = 600


@dataclass(frozen=True)
class FetchResponse:
    """Synthetic or real response data, never copied into an output envelope."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None


Fetcher = Callable[[str, str, float, Mapping[str, str]], FetchResponse]


class _Stop(Exception):
    """A bounded, deliberately non-diagnostic terminal transport condition."""

    def __init__(self, code: str, *, field_status: str = "incomplete", provider_status: str = "unknown",
                 immutable_status: str = "missing", signature_status: str = "unavailable") -> None:
        self.code = code
        self.field_status = field_status
        self.provider_status = provider_status
        self.immutable_status = immutable_status
        self.signature_status = signature_status


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_content_type(headers: Mapping[str, str]) -> str | None:
    for key, value in headers.items():
        if type(key) is str and key.lower() == "content-type" and type(value) is str:
            return value.split(";", 1)[0].strip().lower()
    return None


def _header(headers: Mapping[str, str], wanted: str) -> str | None:
    for key, value in headers.items():
        if type(key) is str and key.lower() == wanted and type(value) is str:
            return value
    return None


def _valid_sha256(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(character in _HEX for character in value.lower())


def allowed_url(url: Any) -> bool:
    """Accept only the three SEC157 origins and their exact bounded paths."""
    if type(url) is not str or len(url) > 2048:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except (TypeError, ValueError):
        return False
    try:
        has_port = parsed.port is not None
    except ValueError:
        return False
    if (
        parsed.scheme != "https" or parsed.query or parsed.fragment or parsed.username is not None
        or parsed.password is not None or has_port or "%" in parsed.path
    ):
        return False
    host, path = parsed.hostname, parsed.path
    if host == "github.com" and parsed.netloc == "github.com":
        return path == "/containers/podman-compose" or path.startswith("/containers/podman-compose/")
    if host == "raw.githubusercontent.com" and parsed.netloc == "raw.githubusercontent.com":
        return bool(_RAW_PATH.fullmatch(path))
    if host == "pypi.org" and parsed.netloc == "pypi.org":
        return path == "/pypi/podman-compose/json" or path.startswith("/project/podman-compose/")
    return False


def _origin(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def _parse_expiry(value: Any) -> dt.datetime | None:
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def valid_execution_grant(grant_id: Any, expires_at: Any, *, now: dt.datetime | None = None) -> bool:
    """Check the exact future binding without contacting a remote system."""
    if grant_id != FUTURE_GRANT_ID:
        return False
    expiry = _parse_expiry(expires_at)
    current = now or dt.datetime.now(dt.timezone.utc)
    if expiry is None or current.tzinfo is None:
        return False
    remaining = (expiry - current).total_seconds()
    return 0 < remaining <= ProvenanceLimits().max_seconds


def _limits_valid(limits: ProvenanceLimits) -> bool:
    maximum = ProvenanceLimits()
    return all(
        type(getattr(limits, name)) is int and 0 < getattr(limits, name) <= getattr(maximum, name)
        for name in ("max_requests", "max_pages", "max_bodies", "max_body_bytes", "max_aggregate_bytes", "max_origins", "max_seconds")
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _real_fetch(method: str, url: str, timeout: float, headers: Mapping[str, str]) -> FetchResponse:
    """Fetch one URL without following redirects or exposing response details."""
    request = urllib.request.Request(url, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            response_headers = dict(response.headers.items())
            body = response.read(ProvenanceLimits().max_body_bytes + 1) if method == "GET" else None
            return FetchResponse(int(response.getcode()), response_headers, body)
    except urllib.error.HTTPError as error:
        # Redirect and non-2xx bodies are intentionally not read.
        return FetchResponse(int(error.code), dict(error.headers.items()), None)
    except Exception as exc:
        raise _Stop("fetch_error") from exc


def _base_candidate(status: str, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "schema": CANDIDATE_SCHEMA,
        "candidate_status": status,
        "implementation_identity": None,
        "supported_distribution_channel": None,
        "entrypoint_provider_chain": None,
        "package_or_artifact_identity": None,
        "version": None,
        "architecture": None,
        "immutable_identity": None,
        "approved_repository_or_channel": None,
        "signature_or_key_verification_mechanism": None,
        "installed_identity_predicates": [],
        "offline_fixture_contract": None,
        "ast_proof_contract": None,
        "decision_authority": None,
        "provenance_evidence": None,
        "rejection_or_block_reason": reason,
    }


def _base_result(status: str, *, stop_reason: str | None, required_field_status: str = "not_run",
                 provider_chain_status: str = "not_run", immutable_status: str = "not_run",
                 signature_status: str = "not_run") -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "candidate_status": status,
        "required_field_status": required_field_status,
        "provider_chain_status": provider_chain_status,
        "immutable_identity_status": immutable_status,
        "signature_verification_status": signature_status,
        "offline_fixture_status": "not_run",
        "ast_no_deps_service_only_status": "not_run",
        "ast_dependency_expansion_status": "not_run",
        "service_selection_status": "not_run",
        "no_build_status": "not_run",
        "force_recreate_status": "not_run",
        "debian_1_3_0_adverse_status": "needs_live_observation",
        "debian_1_3_0_missing_proof": "source_up_no_deps_guard_missing",
        "evidence_reference": None,
        "stop_reason": stop_reason,
    }


def _audit(*, attempts: int, request_count: int, page_count: int, body_count: int, origins: set[str],
           body_bytes: int, execution_status: str, limits: ProvenanceLimits, elapsed: float, network_mode: str) -> dict[str, Any]:
    return {
        "attempts_consumed": attempts,
        "retry_permitted": False,
        "request_count": request_count,
        "page_count": page_count,
        "body_count": body_count,
        "origin_count": len(origins),
        "body_bytes": body_bytes,
        "execution_status": execution_status,
        "request_budget_status": "within_limit" if request_count <= limits.max_requests else "exceeded",
        "page_budget_status": "within_limit" if page_count <= limits.max_pages else "exceeded",
        "body_budget_status": "within_limit" if body_count <= limits.max_bodies else "exceeded",
        "aggregate_budget_status": "within_limit" if body_bytes <= limits.max_aggregate_bytes else "exceeded",
        "origin_budget_status": "within_limit" if len(origins) <= limits.max_origins else "exceeded",
        "time_budget_status": "within_limit" if elapsed <= limits.max_seconds else "exceeded",
        "redaction_status": "fixed_key_only",
        "network_mode": network_mode,
    }


def _envelope(status: str, candidate: Mapping[str, Any], result: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "candidate_record": dict(candidate),
        "result_envelope": dict(result),
        "audit": dict(audit),
    }
    payload["evidence_sha256"] = _canonical_digest(payload)
    return payload


class ProvenanceTransport:
    """One-use collector.  Reusing one instance never triggers a retry."""

    def __init__(self, *, limits: ProvenanceLimits = ProvenanceLimits(), clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = limits
        self._clock = clock
        self._attempt_consumed = False

    def _not_executed(self, code: str, *, request_count: int = 0, page_count: int = 0, body_count: int = 0,
                      origins: set[str] | None = None, body_bytes: int = 0) -> dict[str, Any]:
        audit = _audit(
            attempts=int(self._attempt_consumed), request_count=request_count, page_count=page_count,
            body_count=body_count, origins=origins or set(), body_bytes=body_bytes,
            execution_status=code, limits=self._limits, elapsed=0.0, network_mode="disabled",
        )
        return _envelope("not_executed", _base_candidate("unselected"), _base_result("unselected", stop_reason=code), audit)

    def run(
        self,
        *,
        execute: bool = False,
        grant_id: str | None = None,
        expires_at: str | None = None,
        fetcher: Fetcher | None = None,
        fixture_mode: bool = False,
        now: dt.datetime | None = None,
        subject: str = SUBJECT,
    ) -> dict[str, Any]:
        """Collect once, or return a terminal redacted envelope without a request.

        ``fetcher`` is intentionally accepted only with ``fixture_mode=True``.
        Production execution uses the real fetcher and demands the exact grant
        binding.  No branch retries a request after an error.
        """
        if not _limits_valid(self._limits):
            return self._not_executed("invalid_limits")
        if self._attempt_consumed:
            return self._not_executed("attempt_already_consumed")
        if subject != SUBJECT:
            return self._not_executed("subject_boundary_violation")
        if fetcher is not None:
            if not fixture_mode or execute:
                return self._not_executed("fixture_mode_required")
            selected_fetcher = fetcher
            network_mode = "synthetic_fixture"
        else:
            if not execute:
                return self._not_executed("execution_disabled")
            if not valid_execution_grant(grant_id, expires_at, now=now):
                return self._not_executed("invalid_or_expired_grant")
            selected_fetcher = _real_fetch
            network_mode = "explicit_readonly"

        self._attempt_consumed = True
        started = self._clock()
        request_count = page_count = body_count = body_bytes = 0
        origins: set[str] = set()

        def stop(code: str, *, field_status: str = "incomplete", provider_status: str = "unknown",
                 immutable_status: str = "missing", signature_status: str = "unavailable") -> None:
            raise _Stop(code, field_status=field_status, provider_status=provider_status,
                        immutable_status=immutable_status, signature_status=signature_status)

        def fetch(method: str, url: str, accepted_types: frozenset[str] | None) -> bytes | None:
            nonlocal request_count, page_count, body_count, body_bytes
            if method not in {"GET", "HEAD"} or not allowed_url(url):
                stop("request_boundary_violation")
            redirect_count = 0
            current_url = url
            while True:
                if self._clock() - started > self._limits.max_seconds:
                    stop("time_budget_exceeded")
                if request_count >= self._limits.max_requests:
                    stop("request_budget_exceeded")
                if page_count >= self._limits.max_pages:
                    stop("page_budget_exceeded")
                origin = _origin(current_url)
                if origin not in origins and len(origins) >= self._limits.max_origins:
                    stop("origin_budget_exceeded")
                request_count += 1
                page_count += 1
                origins.add(origin)
                try:
                    response = selected_fetcher(method, current_url, REQUEST_TIMEOUT_SECONDS, {
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json, application/atom+xml, application/xml, text/plain, text/x-python, application/octet-stream",
                    })
                except _Stop:
                    raise
                except Exception as exc:
                    raise _Stop("fetch_error") from exc
                if not isinstance(response, FetchResponse) or type(response.status) is not int or type(response.headers) is not dict:
                    stop("invalid_fetch_response")
                if response.status in {301, 302, 303, 307, 308}:
                    location = _header(response.headers, "location")
                    if type(location) is not str or len(location) > 2048:
                        stop("redirect_invalid")
                    redirected = urllib.parse.urljoin(current_url, location)
                    # The redirect is validated before any body is read or inspected.
                    if not allowed_url(redirected):
                        stop("redirect_boundary_violation")
                    redirect_count += 1
                    if redirect_count > 3:
                        stop("redirect_limit_exceeded")
                    current_url = redirected
                    continue
                if response.status != 200:
                    stop("unexpected_http_status")
                if method == "HEAD":
                    return None
                if accepted_types is None or _normalise_content_type(response.headers) not in accepted_types:
                    stop("content_type_rejected")
                body = response.body
                if type(body) is not bytes:
                    stop("body_unavailable")
                # The real fetcher reads cap+1 bytes.  Synthetic fixtures are
                # checked with the same cap, and their data is never retained.
                if len(body) > self._limits.max_body_bytes:
                    stop("body_too_large")
                if body_count >= self._limits.max_bodies:
                    stop("body_budget_exceeded")
                if body_bytes + len(body) > self._limits.max_aggregate_bytes:
                    stop("aggregate_budget_exceeded")
                body_count += 1
                body_bytes += len(body)
                return body

        try:
            pypi_body = fetch("GET", PYPI_JSON_URL, frozenset({"application/json"}))
            version, filename, digest = _parse_pypi_release(pypi_body)
            atom_body = fetch("GET", GITHUB_ATOM_URL.format(version=version), frozenset({"application/atom+xml", "application/xml", "text/xml"}))
            commit = _parse_atom_commit(atom_body)
            readme_body = fetch("GET", RAW_README_URL.format(commit=commit), frozenset({"text/plain", "text/markdown", "application/octet-stream"}))
            _validate_readme(readme_body)
            source_body = fetch("GET", RAW_SOURCE_URL.format(commit=commit), frozenset({"text/plain", "text/x-python", "application/octet-stream"}))
            _validate_provider_source(source_body)
            candidate = _base_candidate("eligible")
            candidate.update({
                "implementation_identity": "containers/podman-compose",
                "supported_distribution_channel": "pypi.org project metadata",
                "entrypoint_provider_chain": "podman-compose CLI -> podman_compose.py:compose_up -> podman CLI",
                "package_or_artifact_identity": filename,
                "version": version,
                "architecture": "not_applicable_source_distribution",
                "immutable_identity": f"sha256:{digest}",
                "approved_repository_or_channel": "github.com/containers/podman-compose; pypi.org/pypi/podman-compose/json",
                "signature_or_key_verification_mechanism": "not_applicable_digest_only_identity",
                "installed_identity_predicates": [
                    "entrypoint_identity_matches", "provider_chain_matches", "version_matches", "immutable_identity_matches",
                ],
                "provenance_evidence": "redacted_complete_pypi_tag_raw_chain",
                "offline_fixture_contract": OFFLINE_FIXTURE_CONTRACT,
                "ast_proof_contract": AST_PROOF_CONTRACT,
            })
            result = _base_result(
                "eligible", stop_reason=None, required_field_status="complete", provider_chain_status="complete",
                immutable_status="verified", signature_status="unavailable",
            )
            elapsed = self._clock() - started
            audit = _audit(
                attempts=1, request_count=request_count, page_count=page_count, body_count=body_count,
                origins=origins, body_bytes=body_bytes, execution_status="completed", limits=self._limits,
                elapsed=elapsed, network_mode=network_mode,
            )
            return _envelope("completed", candidate, result, audit)
        except _Stop as stopped:
            elapsed = self._clock() - started
            audit = _audit(
                attempts=1, request_count=request_count, page_count=page_count, body_count=body_count,
                origins=origins, body_bytes=body_bytes, execution_status=stopped.code, limits=self._limits,
                elapsed=elapsed, network_mode=network_mode,
            )
            candidate = _base_candidate("blocked", reason=stopped.code)
            result = _base_result(
                "blocked", stop_reason=stopped.code, required_field_status=stopped.field_status,
                provider_chain_status=stopped.provider_status, immutable_status=stopped.immutable_status,
                signature_status=stopped.signature_status,
            )
            return _envelope("blocked", candidate, result, audit)


def _parse_pypi_release(body: bytes | None) -> tuple[str, str, str]:
    if type(body) is not bytes:
        raise _Stop("body_unavailable")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Stop("malformed_pypi_json") from exc
    if type(payload) is not dict or type(payload.get("info")) is not dict or type(payload["info"].get("version")) is not str:
        raise _Stop("malformed_pypi_json")
    version = payload["info"]["version"]
    if not _SAFE_VERSION.fullmatch(version) or type(payload.get("releases")) is not dict:
        raise _Stop("malformed_pypi_json")
    release = payload["releases"].get(version)
    if type(release) is not list:
        raise _Stop("release_mapping_missing", immutable_status="missing")
    source_artifacts: list[tuple[str, str]] = []
    for artifact in release:
        if type(artifact) is not dict or artifact.get("packagetype") != "sdist":
            continue
        filename = artifact.get("filename")
        digests = artifact.get("digests")
        digest = digests.get("sha256") if type(digests) is dict else None
        if type(filename) is str and _SAFE_FILENAME.fullmatch(filename) and _valid_sha256(digest):
            source_artifacts.append((filename, digest.lower()))
    if len(source_artifacts) != 1:
        raise _Stop("release_sha256_missing_or_ambiguous", immutable_status="missing")
    return version, source_artifacts[0][0], source_artifacts[0][1]


def _xml_local_name(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if type(tag) is str else ""


def _parse_atom_commit(body: bytes | None) -> str:
    if type(body) is not bytes:
        raise _Stop("body_unavailable", provider_status="unknown")
    lowered = body.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise _Stop("malformed_tag_atom", provider_status="unknown")
    try:
        root = element_tree.fromstring(body)
    except element_tree.ParseError as exc:
        raise _Stop("malformed_tag_atom", provider_status="unknown") from exc
    entry = next((node for node in root.iter() if _xml_local_name(node.tag) == "entry"), None)
    if entry is None:
        raise _Stop("tag_entry_missing", provider_status="unknown")
    commits: set[str] = set()
    for node in entry.iter():
        local_name = _xml_local_name(node.tag)
        text = node.text.strip() if type(node.text) is str else ""
        if local_name == "id":
            match = re.fullmatch(r"tag:github\.com,2008:Grit::Commit/([0-9a-fA-F]{40})", text)
            if match:
                commits.add(match.group(1).lower())
        elif local_name == "link":
            href = node.attrib.get("href") if type(node.attrib) is dict else None
            if type(href) is str:
                match = re.fullmatch(
                    r"https://github\.com/containers/podman-compose/commit/([0-9a-fA-F]{40})", href,
                )
                if match:
                    commits.add(match.group(1).lower())
    if len(commits) != 1:
        raise _Stop("tag_commit_missing_or_ambiguous", provider_status="unknown")
    return next(iter(commits))


def _safe_source_text(body: bytes | None, code: str) -> str:
    if type(body) is not bytes:
        raise _Stop(code, provider_status="unknown")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Stop(code, provider_status="unknown") from exc
    if not text or any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise _Stop(code, provider_status="unknown")
    return text


def _validate_readme(body: bytes | None) -> None:
    text = _safe_source_text(body, "malformed_readme")
    if "podman-compose" not in text.lower():
        raise _Stop("readme_subject_marker_missing", provider_status="unknown")


def _validate_provider_source(body: bytes | None) -> None:
    text = _safe_source_text(body, "malformed_provider_source")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise _Stop("malformed_provider_source", provider_status="unknown") from exc
    compose_up = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "compose_up"]
    provider_calls: list[tuple[tuple[str, ...], frozenset[str]]] = []
    for call in ast.walk(compose_up[0]) if len(compose_up) == 1 else ():
        if not isinstance(call, ast.Call):
            continue
        segments: list[str] = []
        attributes: set[str] = set()
        function: ast.AST = call.func
        while isinstance(function, ast.Attribute):
            segments.append(function.attr.lower())
            attributes.add(function.attr.lower())
            function = function.value
        if isinstance(function, ast.Name):
            segments.append(function.id.lower())
            provider_calls.append((tuple(reversed(segments)), frozenset(attributes)))
    # This establishes only the entrypoint/provider chain.  It is explicitly
    # not an AST proof of --no-deps behaviour, which remains not_run.
    if len(compose_up) != 1 or not any(
        len(segments) >= 3 and "podman" in attributes and segments[-1] in {"run", "output"}
        for segments, attributes in provider_calls
    ):
        raise _Stop("provider_chain_incomplete", provider_status="unknown")


def validate_envelope(payload: Any) -> bool:
    """Strictly validate the only public envelope accepted by later gates."""
    if type(payload) is not dict or set(payload) != ENVELOPE_KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID or payload.get("status") not in _OUTER_STATUSES:
        return False
    candidate, result, audit = payload.get("candidate_record"), payload.get("result_envelope"), payload.get("audit")
    if type(candidate) is not dict or set(candidate) != CANDIDATE_RECORD_KEYS or candidate.get("schema") != CANDIDATE_SCHEMA:
        return False
    candidate_status = candidate.get("candidate_status")
    if candidate_status not in _CANDIDATE_STATUSES or type(candidate.get("installed_identity_predicates")) is not list:
        return False
    candidate_data_keys = CANDIDATE_RECORD_KEYS - {"schema", "candidate_status", "installed_identity_predicates", "rejection_or_block_reason"}
    if candidate_status in {"unselected", "blocked"} and any(candidate.get(key) is not None for key in candidate_data_keys):
        return False
    if candidate_status in {"unselected", "blocked"} and candidate["installed_identity_predicates"]:
        return False
    if candidate_status == "eligible":
        if candidate.get("rejection_or_block_reason") is not None:
            return False
        if candidate.get("installed_identity_predicates") != [
            "entrypoint_identity_matches", "provider_chain_matches", "version_matches", "immutable_identity_matches",
        ]:
            return False
        nonempty = candidate_data_keys - {"decision_authority"}
        if any(type(candidate.get(key)) is not str or not candidate[key] or len(candidate[key]) > 512 for key in nonempty):
            return False
        if candidate.get("decision_authority") is not None:
            return False
    if candidate_status == "blocked" and candidate.get("rejection_or_block_reason") not in _EXECUTION_STATUSES:
        return False
    if candidate_status == "unselected" and candidate.get("rejection_or_block_reason") is not None:
        return False
    if type(result) is not dict or set(result) != RESULT_ENVELOPE_KEYS or result.get("schema") != RESULT_SCHEMA:
        return False
    if result.get("candidate_status") != candidate_status:
        return False
    if result.get("required_field_status") not in {"complete", "incomplete", "conflicting", "not_run"}:
        return False
    if result.get("provider_chain_status") not in {"complete", "unknown", "conflicting", "not_run"}:
        return False
    if result.get("immutable_identity_status") not in {"verified", "missing", "mutable", "not_run"}:
        return False
    if result.get("signature_verification_status") not in {"verified", "unavailable", "failed", "not_run"}:
        return False
    proof_keys = {
        "offline_fixture_status", "ast_no_deps_service_only_status", "ast_dependency_expansion_status",
        "service_selection_status", "no_build_status", "force_recreate_status",
    }
    if any(result.get(key) not in {"pass", "fail", "not_run"} for key in proof_keys):
        return False
    if result.get("debian_1_3_0_adverse_status") not in {"needs_live_observation", "fail", "not_run"}:
        return False
    if result.get("debian_1_3_0_missing_proof") != "source_up_no_deps_guard_missing":
        return False
    if type(audit) is not dict or set(audit) != AUDIT_KEYS or audit.get("retry_permitted") is not False:
        return False
    counters = ("attempts_consumed", "request_count", "page_count", "body_count", "origin_count", "body_bytes")
    if any(type(audit.get(key)) is not int or audit[key] < 0 for key in counters):
        return False
    if any(audit[key] > getattr(ProvenanceLimits(), attribute) for key, attribute in {
        "request_count": "max_requests", "page_count": "max_pages", "body_count": "max_bodies",
        "origin_count": "max_origins", "body_bytes": "max_aggregate_bytes",
    }.items()):
        return False
    if audit.get("execution_status") not in _EXECUTION_STATUSES or audit.get("network_mode") not in {"disabled", "synthetic_fixture", "explicit_readonly"}:
        return False
    if audit.get("redaction_status") != "fixed_key_only":
        return False
    if any(audit.get(key) not in {"within_limit", "exceeded"} for key in {
        "request_budget_status", "page_budget_status", "body_budget_status", "aggregate_budget_status",
        "origin_budget_status", "time_budget_status",
    }):
        return False
    stop_reason = result.get("stop_reason")
    if stop_reason is not None and stop_reason not in _EXECUTION_STATUSES:
        return False
    if payload["status"] == "completed":
        if candidate_status != "eligible" or audit["execution_status"] != "completed" or audit["attempts_consumed"] != 1:
            return False
    elif payload["status"] == "blocked":
        if candidate_status != "blocked" or audit["attempts_consumed"] != 1 or stop_reason != audit["execution_status"]:
            return False
    elif candidate_status != "unselected" or audit["attempts_consumed"] != 0 or stop_reason != audit["execution_status"]:
        return False
    if payload.get("evidence_sha256") != _canonical_digest(payload) or not _valid_sha256(payload.get("evidence_sha256")):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one explicitly granted redacted Compose provenance collection.")
    parser.add_argument("--execute", action="store_true", help="allow the future, separately granted read-only collection")
    parser.add_argument("--grant-id", help="must equal the exact future provenance grant id")
    parser.add_argument("--expires-at", help="future grant expiry in timezone-aware ISO-8601 form")
    args = parser.parse_args(argv)
    payload = ProvenanceTransport().run(execute=args.execute, grant_id=args.grant_id, expires_at=args.expires_at)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
