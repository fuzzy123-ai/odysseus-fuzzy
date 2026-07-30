from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.homeserver import redacted_compose_candidate_provenance as provenance


def _response(body: bytes, content_type: str) -> provenance.FetchResponse:
    return provenance.FetchResponse(200, {"Content-Type": content_type}, body)


def _pypi_body(*, version: str = "1.2.3", digest: str | None = None) -> bytes:
    digest = digest or ("a" * 64)
    return json.dumps({
        "info": {"version": version},
        "releases": {version: [{"packagetype": "sdist", "filename": f"podman-compose-{version}.tar.gz", "digests": {"sha256": digest}}]},
    }).encode()


COMMIT = "b" * 40
ATOM = f"<feed><entry><id>tag:github.com,2008:Grit::Commit/{COMMIT}</id></entry></feed>".encode()
README = b"# podman-compose\n"
SOURCE = b"def compose_up(args):\n    return compose.podman.run(args)\n"


class FixtureFetcher:
    def __init__(self, routes: dict[str, provenance.FetchResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, float, dict[str, str]]] = []

    def __call__(self, method: str, url: str, timeout: float, headers: dict[str, str]) -> provenance.FetchResponse:
        self.calls.append((method, url, timeout, dict(headers)))
        if url not in self.routes:
            raise RuntimeError("fixture route unavailable: api_token=never-disclose")
        return self.routes[url]


def _happy_fetcher() -> FixtureFetcher:
    return FixtureFetcher({
        provenance.PYPI_JSON_URL: _response(_pypi_body(), "application/json"),
        provenance.GITHUB_ATOM_URL.format(version="1.2.3"): _response(ATOM, "application/atom+xml; charset=utf-8"),
        provenance.RAW_README_URL.format(commit=COMMIT): _response(README, "text/plain"),
        provenance.RAW_SOURCE_URL.format(commit=COMMIT): _response(SOURCE, "text/x-python"),
    })


def _run_fixture(fetcher: FixtureFetcher, **kwargs: object) -> dict[str, object]:
    return provenance.ProvenanceTransport().run(fetcher=fetcher, fixture_mode=True, **kwargs)


def test_happy_path_is_redacted_fixed_key_and_never_claims_ast_proof() -> None:
    fetcher = _happy_fetcher()
    result = _run_fixture(fetcher)
    assert result["status"] == "completed"
    assert provenance.validate_envelope(result)
    assert result["candidate_record"]["candidate_status"] == "eligible"
    assert result["result_envelope"]["provider_chain_status"] == "complete"
    assert result["result_envelope"]["signature_verification_status"] == "unavailable"
    assert result["result_envelope"]["offline_fixture_status"] == "not_run"
    assert result["result_envelope"]["ast_no_deps_service_only_status"] == "not_run"
    assert result["candidate_record"]["offline_fixture_contract"] == provenance.OFFLINE_FIXTURE_CONTRACT
    assert result["candidate_record"]["ast_proof_contract"] == provenance.AST_PROOF_CONTRACT
    assert result["candidate_record"]["decision_authority"] is None
    assert result["audit"]["request_count"] == 4
    assert result["audit"]["body_count"] == 4
    assert all(call[0] == "GET" and call[2] == provenance.REQUEST_TIMEOUT_SECONDS for call in fetcher.calls)
    assert all(call[3]["User-Agent"] == provenance.USER_AGENT for call in fetcher.calls)
    assert result["evidence_sha256"] == hashlib.sha256(json.dumps({key: value for key, value in result.items() if key != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("key", ["offline_fixture_contract", "ast_proof_contract", "version"])
def test_eligible_envelope_requires_all_contract_and_provenance_fields(key: str) -> None:
    result = _run_fixture(_happy_fetcher())
    tampered = json.loads(json.dumps(result))
    tampered["candidate_record"][key] = None
    tampered["evidence_sha256"] = provenance._canonical_digest(tampered)
    assert not provenance.validate_envelope(tampered)


@pytest.mark.parametrize("url", [
    "https://github.com/containers/podman-compose-evil",
    "https://github.com/other/podman-compose",
    "https://raw.githubusercontent.com/containers/podman-compose/main/podman_compose.py",
    "https://raw.githubusercontent.com/containers/podman-compose/abc/podman_compose.py",
    "https://pypi.org/pypi/podman-compose/json?x=1",
    "https://pypi.org:invalid/pypi/podman-compose/json",
    "https://evil.example/containers/podman-compose",
])
def test_exact_origin_and_path_rules_reject_siblings_mutable_refs_and_escapes(url: str) -> None:
    assert not provenance.allowed_url(url)


def test_redirect_is_validated_before_body_and_escape_stops() -> None:
    fetcher = FixtureFetcher({
        provenance.PYPI_JSON_URL: provenance.FetchResponse(302, {"Location": "https://evil.example/private"}, b"secret-body"),
    })
    result = _run_fixture(fetcher)
    assert result["status"] == "blocked"
    assert result["result_envelope"]["stop_reason"] == "redirect_boundary_violation"
    assert result["audit"]["body_count"] == 0
    assert "secret-body" not in json.dumps(result)


def test_oversize_and_mime_fail_closed_without_raw_body() -> None:
    large = b"x" * (provenance.ProvenanceLimits().max_body_bytes + 1)
    oversize = _run_fixture(FixtureFetcher({provenance.PYPI_JSON_URL: _response(large, "application/json")}))
    assert oversize["result_envelope"]["stop_reason"] == "body_too_large"
    wrong_mime = _run_fixture(FixtureFetcher({provenance.PYPI_JSON_URL: _response(_pypi_body(), "text/html")}))
    assert wrong_mime["result_envelope"]["stop_reason"] == "content_type_rejected"


def test_malformed_json_xml_and_source_stop_with_codes_only() -> None:
    bad_json = _run_fixture(FixtureFetcher({provenance.PYPI_JSON_URL: _response(b"{not json", "application/json")}))
    assert bad_json["result_envelope"]["stop_reason"] == "malformed_pypi_json"
    routes = _happy_fetcher().routes
    routes[provenance.GITHUB_ATOM_URL.format(version="1.2.3")] = _response(b"<feed", "application/atom+xml")
    bad_atom = _run_fixture(FixtureFetcher(routes))
    assert bad_atom["result_envelope"]["stop_reason"] == "malformed_tag_atom"
    routes = _happy_fetcher().routes
    routes[provenance.RAW_SOURCE_URL.format(commit=COMMIT)] = _response(b"\xff", "text/x-python")
    bad_source = _run_fixture(FixtureFetcher(routes))
    assert bad_source["result_envelope"]["stop_reason"] == "malformed_provider_source"


def test_first_atom_entry_is_authoritative_and_later_history_is_ignored() -> None:
    later = "c" * 40
    multi_entry_atom = (
        f"<feed><entry><id>tag:github.com,2008:Grit::Commit/{COMMIT}</id>"
        f"<link href=\"https://github.com/containers/podman-compose/commit/{COMMIT}\" /></entry>"
        f"<entry><id>tag:github.com,2008:Grit::Commit/{later}</id></entry></feed>"
    ).encode()
    routes = _happy_fetcher().routes
    routes[provenance.GITHUB_ATOM_URL.format(version="1.2.3")] = _response(multi_entry_atom, "application/atom+xml")
    assert _run_fixture(FixtureFetcher(routes))["status"] == "completed"


def test_ambiguous_or_missing_first_atom_entry_fails_closed() -> None:
    other = "c" * 40
    ambiguous = (
        f"<feed><entry><id>tag:github.com,2008:Grit::Commit/{COMMIT}</id>"
        f"<link href=\"https://github.com/containers/podman-compose/commit/{other}\" /></entry></feed>"
    ).encode()
    routes = _happy_fetcher().routes
    routes[provenance.GITHUB_ATOM_URL.format(version="1.2.3")] = _response(ambiguous, "application/atom+xml")
    assert _run_fixture(FixtureFetcher(routes))["result_envelope"]["stop_reason"] == "tag_commit_missing_or_ambiguous"
    routes = _happy_fetcher().routes
    routes[provenance.GITHUB_ATOM_URL.format(version="1.2.3")] = _response(b"<feed></feed>", "application/atom+xml")
    assert _run_fixture(FixtureFetcher(routes))["result_envelope"]["stop_reason"] == "tag_entry_missing"


def test_missing_digest_or_provider_chain_blocks_eligibility() -> None:
    missing_digest = _run_fixture(FixtureFetcher({provenance.PYPI_JSON_URL: _response(_pypi_body(digest="not-a-digest"), "application/json")}))
    assert missing_digest["candidate_record"]["candidate_status"] == "blocked"
    assert missing_digest["result_envelope"]["immutable_identity_status"] == "missing"
    routes = _happy_fetcher().routes
    routes[provenance.RAW_SOURCE_URL.format(commit=COMMIT)] = _response(b"def unrelated():\n    return 1\n", "text/x-python")
    incomplete_chain = _run_fixture(FixtureFetcher(routes))
    assert incomplete_chain["result_envelope"]["stop_reason"] == "provider_chain_incomplete"


def test_misleading_podman_helper_call_is_not_a_provider_execution_chain() -> None:
    routes = _happy_fetcher().routes
    routes[provenance.RAW_SOURCE_URL.format(commit=COMMIT)] = _response(
        b"def compose_up(args):\n    return get_podman_version()\n", "text/x-python",
    )
    result = _run_fixture(FixtureFetcher(routes))
    assert result["candidate_record"]["candidate_status"] == "blocked"
    assert result["result_envelope"]["stop_reason"] == "provider_chain_incomplete"


def test_podman_name_without_podman_attribute_is_not_a_provider_execution_chain() -> None:
    routes = _happy_fetcher().routes
    routes[provenance.RAW_SOURCE_URL.format(commit=COMMIT)] = _response(
        b"def compose_up(args):\n    return podman.executor.run(args)\n", "text/x-python",
    )
    result = _run_fixture(FixtureFetcher(routes))
    assert result["result_envelope"]["stop_reason"] == "provider_chain_incomplete"


def test_aggregate_cap_and_one_use_never_retry() -> None:
    limits = provenance.ProvenanceLimits(max_aggregate_bytes=16)
    transport = provenance.ProvenanceTransport(limits=limits)
    fetcher = _happy_fetcher()
    first = transport.run(fetcher=fetcher, fixture_mode=True)
    assert first["result_envelope"]["stop_reason"] == "aggregate_budget_exceeded"
    call_count = len(fetcher.calls)
    second = transport.run(fetcher=fetcher, fixture_mode=True)
    assert second["status"] == "not_executed"
    assert second["result_envelope"]["stop_reason"] == "attempt_already_consumed"
    assert len(fetcher.calls) == call_count


def test_default_disabled_and_fixture_requires_explicit_fixture_mode() -> None:
    fetcher = _happy_fetcher()
    disabled = provenance.ProvenanceTransport().run()
    assert disabled["status"] == "not_executed"
    assert disabled["result_envelope"]["stop_reason"] == "execution_disabled"
    bad_fixture = provenance.ProvenanceTransport().run(fetcher=fetcher)
    assert bad_fixture["result_envelope"]["stop_reason"] == "fixture_mode_required"
    assert not fetcher.calls


def test_wrong_subject_stops_before_any_fixture_request() -> None:
    fetcher = _happy_fetcher()
    result = provenance.ProvenanceTransport().run(
        fetcher=fetcher, fixture_mode=True, subject="some-other-project",
    )
    assert result["status"] == "not_executed"
    assert result["result_envelope"]["stop_reason"] == "subject_boundary_violation"
    assert not fetcher.calls


def test_future_grant_and_expiry_are_exact_and_no_invalid_grant_fetches() -> None:
    now = dt.datetime(2026, 7, 30, 10, 0, tzinfo=dt.timezone.utc)
    future = "2026-07-30T10:05:00+00:00"
    assert provenance.valid_execution_grant(provenance.FUTURE_GRANT_ID, future, now=now)
    assert not provenance.valid_execution_grant("other", future, now=now)
    assert not provenance.valid_execution_grant(provenance.FUTURE_GRANT_ID, "2026-07-30T09:59:59+00:00", now=now)
    result = provenance.ProvenanceTransport().run(execute=True, grant_id="other", expires_at=future, now=now)
    assert result["result_envelope"]["stop_reason"] == "invalid_or_expired_grant"
    assert result["audit"]["request_count"] == 0


def test_timeout_and_exception_text_are_redacted() -> None:
    def failing_fetcher(method: str, url: str, timeout: float, headers: dict[str, str]) -> provenance.FetchResponse:
        raise TimeoutError("token=top-secret https://private.example/path")

    result = provenance.ProvenanceTransport().run(fetcher=failing_fetcher, fixture_mode=True)
    rendered = json.dumps(result)
    assert result["result_envelope"]["stop_reason"] == "fetch_error"
    assert "top-secret" not in rendered
    assert "private.example" not in rendered


def test_help_and_offline_cli_validation_do_not_invoke_network(tmp_path: Path) -> None:
    script = Path(provenance.__file__)
    help_run = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, check=False)
    assert help_run.returncode == 0
    idle_run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    payload = json.loads(idle_run.stdout)
    assert idle_run.returncode == 1
    assert payload["status"] == "not_executed"
    assert provenance.validate_envelope(payload)
