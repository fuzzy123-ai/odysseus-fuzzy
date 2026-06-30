from types import SimpleNamespace

from routes.model_endpoint_helpers import _resolve_probe_key


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


def test_resolve_probe_key_returns_runtime_key_and_passes_owner():
    ep = SimpleNamespace(id="ep-1", owner="alice")
    calls = []

    def resolve_runtime(endpoint, *, owner=None):
        calls.append((endpoint, owner))
        return "https://models.example/v1", "secret-key"

    result = _resolve_probe_key(
        ep,
        resolve_endpoint_runtime_func=resolve_runtime,
        logger=_Logger(),
    )

    assert result == "secret-key"
    assert calls == [(ep, "alice")]


def test_resolve_probe_key_warns_and_returns_none_on_failure():
    ep = SimpleNamespace(id="ep-2", owner="alice")
    logger = _Logger()

    def resolve_runtime(endpoint, *, owner=None):
        raise RuntimeError("missing runtime config")

    result = _resolve_probe_key(
        ep,
        resolve_endpoint_runtime_func=resolve_runtime,
        logger=logger,
    )

    assert result is None
    assert len(logger.warnings) == 1
    message, args = logger.warnings[0]
    assert message == "Probe key resolution failed for %s: %s"
    assert args[0] == "ep-2"
    assert isinstance(args[1], RuntimeError)
    assert str(args[1]) == "missing runtime config"
