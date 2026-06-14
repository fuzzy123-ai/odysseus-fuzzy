import importlib.machinery
import importlib.util
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_API = ROOT / "integrations" / "codex" / "scripts" / "odysseus_api.py"
CLAUDE_API = ROOT / "integrations" / "claude" / "skills" / "odysseus" / "scripts" / "odysseus_api.py"


class ReconfigurableStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.reconfigure_calls = []

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)


class FakeBuffer:
    def __init__(self):
        self.writes = []
        self.flushed = False

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        self.flushed = True


class FakeStdout:
    def __init__(self):
        self.buffer = FakeBuffer()


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return "äöü ↔".encode("utf-8")


def _load_module(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_integration_api_scripts_configure_utf8_stdio(monkeypatch):
    stdout = ReconfigurableStream()
    stderr = ReconfigurableStream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    for path, name in ((CODEX_API, "codex_odysseus_api"), (CLAUDE_API, "claude_odysseus_api")):
        module = _load_module(path, name)
        module._configure_utf8_stdio()

    expected = {"encoding": "utf-8", "errors": "replace"}
    assert stdout.reconfigure_calls == [expected, expected]
    assert stderr.reconfigure_calls == [expected, expected]


def test_claude_integration_api_writes_response_bytes_to_stdout_buffer(monkeypatch):
    module = _load_module(CLAUDE_API, "claude_odysseus_api_response")
    stdout = FakeStdout()

    monkeypatch.setattr(module.sys, "argv", ["odysseus_api.py", "capabilities"])
    monkeypatch.setattr(module.sys, "stdout", stdout)
    monkeypatch.setenv("ODYSSEUS_URL", "http://odysseus.local")
    monkeypatch.setenv("ODYSSEUS_API_TOKEN", "token")
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

    assert module.main() == 0
    assert stdout.buffer.writes == ["äöü ↔".encode("utf-8"), b"\n"]
    assert stdout.buffer.flushed is True
