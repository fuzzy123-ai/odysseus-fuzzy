from __future__ import annotations

import asyncio

import pytest

from src.security_incident_egress import (
    DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    MAX_TRACE_BODY_BYTES,
    PUBLIC_EGRESS_ENDPOINT,
    PublicEgressDiscoveryError,
    PublicEgressRefreshController,
    discover_public_egress_snapshot,
    discovery_enabled_from_disable_value,
)


class _Response:
    def __init__(self, body: bytes, *, status: int = 200, url: str = PUBLIC_EGRESS_ENDPOINT):
        self._body = body
        self.status = status
        self._url = url
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, limit):
        self.read_limit = limit
        return self._body[:limit]


class _Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", ("8.8.8.8",)),
        ("2606:4700:4700::1111", ("2606:4700:4700::1111",)),
    ],
)
def test_fixed_https_trace_contract_returns_only_validated_public_snapshot(address, expected):
    response = _Response(f"fl=1\nip={address}\nts=1\n".encode("ascii"))
    opener = _Opener(response)
    snapshot = discover_public_egress_snapshot(opener=opener, clock=lambda: 100.0)

    request, timeout = opener.calls[0]
    assert request.full_url == PUBLIC_EGRESS_ENDPOINT
    assert request.get_method() == "GET"
    assert timeout == DEFAULT_DISCOVERY_TIMEOUT_SECONDS
    assert response.read_limit == MAX_TRACE_BODY_BYTES + 1
    assert snapshot.addresses == expected
    assert snapshot.source == "cloudflare_trace"
    assert snapshot.is_fresh(now=100.0)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"fl=1\n",
        b"ip=8.8.8.8\nip=1.1.1.1\n",
        b"ip=10.0.0.1\n",
        b"ip=127.0.0.1\n",
        b"ip=2001:db8::1\n",
        b"ip=::ffff:8.8.8.8\n",
        b"ip=2606:4700:4700:0:0:0:0:1111\n",
        b"ip=8.8.8.8 \n",
        b"ip=host.example\n",
        b"ip=fe80::1%eth0\n",
        b"ip=\xff\n",
        b"x" * (MAX_TRACE_BODY_BYTES + 1),
    ],
)
def test_trace_body_rejects_private_noncanonical_multiple_malformed_or_oversize(body):
    with pytest.raises(PublicEgressDiscoveryError) as caught:
        discover_public_egress_snapshot(opener=_Opener(_Response(body)), clock=lambda: 100.0)
    assert str(caught.value) == "public egress discovery unavailable"


@pytest.mark.parametrize(
    "response",
    [
        _Response(b"ip=8.8.8.8\n", status=204),
        _Response(b"ip=8.8.8.8\n", status=301),
        _Response(b"ip=8.8.8.8\n", url="https://www.cloudflare.com/cdn-cgi/trace"),
        _Response(b"ip=8.8.8.8\n", url="http://cloudflare.com/cdn-cgi/trace"),
        _Response(b"ip=8.8.8.8\n", url="https://example.com/cdn-cgi/trace"),
    ],
)
def test_http_redirect_tls_and_alternate_host_responses_fail_closed(response):
    with pytest.raises(PublicEgressDiscoveryError):
        discover_public_egress_snapshot(opener=_Opener(response), clock=lambda: 100.0)


def test_provider_errors_never_expose_exception_or_body_data():
    marker = "198.51.100.77 provider-secret"
    with pytest.raises(PublicEgressDiscoveryError) as caught:
        discover_public_egress_snapshot(
            opener=_Opener(RuntimeError(marker)),
            clock=lambda: 100.0,
        )
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_discovery_disable_switch_is_default_on_and_invalid_is_fail_open():
    assert discovery_enabled_from_disable_value(None) is True
    assert discovery_enabled_from_disable_value("") is True
    assert discovery_enabled_from_disable_value("false") is True
    assert discovery_enabled_from_disable_value("true") is False
    for value in ("TRUE", "1", "yes", " true ", "invalid", object()):
        assert discovery_enabled_from_disable_value(value) is False


def test_refresh_replaces_atomically_retains_only_fresh_and_failure_is_fail_open():
    clock = [100.0]
    opener = _Opener(
        _Response(b"ip=8.8.8.8\n"),
        RuntimeError("provider detail must not escape"),
        RuntimeError("provider detail must not escape"),
    )
    controller = PublicEgressRefreshController(
        opener=opener,
        clock=lambda: clock[0],
        refresh_interval_seconds=10,
        ttl_seconds=60,
    )
    first = controller.refresh_now()
    assert first is not None and first.addresses == ("8.8.8.8",)
    clock[0] = 150.0
    assert controller.refresh_now() is first
    clock[0] = 161.0
    assert controller.refresh_now() is None
    assert controller.current_snapshot() is None


def test_refresh_clock_uncertainty_is_fail_open_and_never_publishes_unvalidated_state():
    def broken_clock():
        raise RuntimeError("clock detail")

    controller = PublicEgressRefreshController(
        opener=_Opener(_Response(b"ip=8.8.8.8\n")),
        clock=broken_clock,
        refresh_interval_seconds=10,
        ttl_seconds=60,
    )
    assert controller.refresh_now() is None
    assert controller.current_snapshot() is None


@pytest.mark.asyncio
async def test_periodic_refresh_is_nonblocking_cancel_safe_and_clears_published_state():
    entered_sleep = asyncio.Event()
    release_sleep = asyncio.Event()
    published = []

    async def sleeper(_seconds):
        entered_sleep.set()
        await release_sleep.wait()

    controller = PublicEgressRefreshController(
        opener=_Opener(_Response(b"ip=8.8.8.8\n")),
        clock=lambda: 100.0,
        sleeper=sleeper,
        refresh_interval_seconds=10,
        ttl_seconds=60,
    )
    task = asyncio.create_task(controller.run(published.append))
    await asyncio.wait_for(entered_sleep.wait(), timeout=1)
    assert published[-1].addresses == ("8.8.8.8",)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert published[-1] is None


@pytest.mark.asyncio
async def test_periodic_failure_publishes_none_without_crashing_or_retrying_early():
    entered_sleep = asyncio.Event()
    published = []

    async def sleeper(_seconds):
        entered_sleep.set()
        await asyncio.Event().wait()

    opener = _Opener(RuntimeError("raw provider failure"))
    controller = PublicEgressRefreshController(
        opener=opener,
        clock=lambda: 100.0,
        sleeper=sleeper,
        refresh_interval_seconds=10,
        ttl_seconds=60,
    )
    task = asyncio.create_task(controller.run(published.append))
    await asyncio.wait_for(entered_sleep.wait(), timeout=1)
    assert published == [None]
    assert len(opener.calls) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert published[-1] is None
