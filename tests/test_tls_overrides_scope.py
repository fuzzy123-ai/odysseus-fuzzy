"""Scope tests for src/tls_overrides.

#722 / PR #769 added an opt-in extra CA bundle (LLM_CA_BUNDLE) for
private-CA LLM providers. The whole point is that the override stays
SCOPED — it must extend trust for the intended outbound LLM provider
requests only, and never:

  - touch arbitrary URL fetching (web_fetch, document downloads, generic
    httpx.get from any other module),
  - touch browser-facing TLS (anything our app serves over HTTPS),
  - weaken httpx's process-wide defaults,
  - silently disable certificate verification.

These tests prove that. They enumerate both the importer and direct-invoker
sets of `llm_verify()` in the source tree and assert they match their
allowlists; they verify the override module itself never reaches for the
well-known "skip TLS verification" knobs; and they pin the safe default
(verify=True) when LLM_CA_BUNDLE is unset.

If a future change imports or directly invokes `llm_verify()` from a non-LLM
HTTP path, the relevant test fails and the contributor either has to justify
the new integration (and add it to the matching allowlist with a comment) or
revert. That keeps the security-sensitive helper hard to misuse even when a
trusted caller passes it into a narrow helper as a dependency.
"""

from __future__ import annotations

import ast
import os
import re
import tokenize
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]


# Modules that legitimately import the helper. The model route passes it only
# into model-endpoint probe helpers; the other two invoke it directly.
ALLOWED_IMPORTERS = frozenset({
    "routes/model_routes.py",       # configured LLM endpoint probes
    "src/llm_kimi_code.py",         # host/path-gated Kimi Code /models probe
    "src/llm_runtime_state.py",     # shared AsyncClient for llm_core only
})


# The actual ``llm_verify()`` invocations. ``model_routes`` deliberately is
# absent: it injects the helper into narrow probe helpers instead of calling it
# itself. Keeping this separate makes both boundaries explicit after refactors.
ALLOWED_DIRECT_INVOKERS = frozenset({
    "src/llm_kimi_code.py",
    "src/llm_runtime_state.py",
})


@lru_cache(maxsize=1)
def _production_module_trees() -> tuple[tuple[str, ast.AST], ...]:
    """Parse production Python modules that can consume the TLS helper."""
    trees: list[tuple[str, ast.AST]] = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith("tests/"):
            continue
        if rel == "src/tls_overrides.py":  # definition site, not a caller
            continue
        if rel.startswith(".claude/") or "/.claude/" in rel:
            continue
        with tokenize.open(path) as source:
            body = source.read()
        trees.append((rel, ast.parse(body, filename=rel)))
    return tuple(trees)


def _dotted_name(node: ast.AST) -> str | None:
    """Return a dotted expression name, if *node* is only names/attributes."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _llm_verify_usage() -> tuple[set[str], set[str]]:
    """Return exact importer and direct-invoker modules for llm_verify."""
    importers: set[str] = set()
    invokers: set[str] = set()
    for rel, tree in _production_module_trees():
        direct_names: set[str] = set()
        module_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "src.tls_overrides":
                    for alias in node.names:
                        if alias.name == "llm_verify":
                            importers.add(rel)
                            direct_names.add(alias.asname or alias.name)
                elif node.module == "src":
                    for alias in node.names:
                        if alias.name == "tls_overrides":
                            importers.add(rel)
                            module_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src.tls_overrides":
                        importers.add(rel)
                        module_names.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_name(node.func)
            if dotted in direct_names or any(
                dotted == f"{module_name}.llm_verify"
                for module_name in module_names
            ):
                invokers.add(rel)
    return importers, invokers


def _modules_referencing(symbol: str) -> set[str]:
    """Return production modules with an executable reference to *symbol*."""
    hits: set[str] = set()
    for rel, tree in _production_module_trees():
        if any(
            (isinstance(node, ast.Name) and node.id == symbol)
            or (isinstance(node, ast.Attribute) and node.attr == symbol)
            for node in ast.walk(tree)
        ):
            hits.add(rel)
    return hits


def test_llm_verify_imported_only_by_allowlisted_modules():
    """Only reviewed LLM integration modules may import llm_verify.

    The model routes inject it into their probe helpers while runtime state
    and the Kimi helper call it directly. If a future PR threads it into
    web_fetch, search providers, embeddings, gallery downloads, webhook
    delivery, or any other arbitrary-URL caller, that's a scope expansion
    and a security review. Any new importer requires a written security
    justification.
    """
    importers, _ = _llm_verify_usage()
    unexpected = importers - ALLOWED_IMPORTERS
    missing = ALLOWED_IMPORTERS - importers
    assert not unexpected, (
        f"llm_verify imported from unexpected file(s): {sorted(unexpected)}. "
        f"Expected scope: {sorted(ALLOWED_IMPORTERS)}. If the new importer is "
        "an LLM provider entry point, add it with a comment; otherwise do not "
        "thread the extra CA bundle into it."
    )
    assert not missing, (
        f"llm_verify no longer imported from {sorted(missing)} — the "
        "extra CA bundle integration regressed or the allowlist is stale."
    )


def test_llm_verify_directly_invoked_only_by_allowlisted_modules():
    """Direct calls stay restricted even when imports are injected onward."""
    _, invokers = _llm_verify_usage()
    unexpected = invokers - ALLOWED_DIRECT_INVOKERS
    missing = ALLOWED_DIRECT_INVOKERS - invokers
    assert not unexpected, (
        f"llm_verify() called from unexpected file(s): {sorted(unexpected)}. "
        f"Expected direct scope: {sorted(ALLOWED_DIRECT_INVOKERS)}."
    )
    assert not missing, (
        f"llm_verify() no longer called from {sorted(missing)}; the extra "
        "CA bundle integration regressed or the allowlist is stale."
    )


def test_model_routes_only_injects_llm_verify_into_model_probe_helpers():
    """The allowed route importer cannot pass extended trust to generic I/O."""
    model_routes = dict(_production_module_trees())["routes/model_routes.py"]
    parents = {
        child: parent
        for parent in ast.walk(model_routes)
        for child in ast.iter_child_nodes(parent)
    }
    injected_into: set[str] = set()
    for node in ast.walk(model_routes):
        if not (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == "llm_verify"
        ):
            continue
        keyword = parents.get(node)
        call = parents.get(keyword)
        assert isinstance(keyword, ast.keyword) and keyword.arg == "llm_verify_func"
        assert isinstance(call, ast.Call)
        injected_into.add(_dotted_name(call.func) or "")

    assert injected_into == {
        "_ping_endpoint_impl",
        "_probe_endpoint_impl",
        "_probe_single_model_impl",
    }


def test_shared_llm_client_factory_has_only_llm_core_production_consumer():
    """The extended-trust shared client cannot become a generic transport."""
    consumers = _modules_referencing("get_shared_http_client")
    consumers.discard("src/llm_runtime_state.py")  # definition site
    assert consumers == {"src/llm_core.py"}, (
        "get_shared_http_client must remain private to llm_core; "
        f"found production consumers: {sorted(consumers)}"
    )


def test_kimi_probe_uses_marker_only_after_validated_kimi_coding_gate(monkeypatch):
    """Kimi's extra-CA probe is host/path gated and never probes lookalikes."""
    from src import llm_kimi_code, tls_overrides

    marker = object()
    calls = []

    def fake_get(url, *, headers, timeout, verify):
        calls.append({"url": url, "headers": headers, "timeout": timeout, "verify": verify})
        return SimpleNamespace(status_code=200, content=b"{}")

    monkeypatch.setattr(tls_overrides, "llm_verify", lambda: marker)
    monkeypatch.setattr(llm_kimi_code.httpx, "get", fake_get)
    llm_kimi_code._kimi_code_ua_cache.clear()
    try:
        llm_kimi_code.apply_kimi_code_headers(
            {"Authorization": "Bearer placeholder"},
            "https://gateway.kimi.com/coding/v1/chat/completions",
        )
        assert len(calls) == 1
        parsed = urlparse(calls[0]["url"])
        assert parsed.scheme == "https"
        assert parsed.hostname == "gateway.kimi.com"
        assert parsed.path == "/coding/v1/models"
        assert calls[0]["verify"] is marker
        assert calls[0]["timeout"] == 8

        for rejected_url in (
            "https://kimi.com.example/coding/v1/chat/completions",
            "https://api.kimi.com/v1/chat/completions",
        ):
            llm_kimi_code.apply_kimi_code_headers({}, rejected_url)
        assert len(calls) == 1
    finally:
        llm_kimi_code._kimi_code_ua_cache.clear()


def test_tls_overrides_does_not_weaken_global_tls():
    """src/tls_overrides must never reach for a TLS-weakening knob.

    Several common ways to silently weaken TLS in Python:
      - ssl._create_default_https_context = ssl._create_unverified_context
      - ssl._create_unverified_context (used as a default)
      - urllib3.disable_warnings(...)
      - httpx.AsyncClient(verify=False) (anywhere — must stay verify=True
        or an SSLContext)
      - requests.packages.urllib3.disable_warnings(...)

    The override module must only EXTEND trust by loading an additional
    bundle into an ssl.SSLContext built on top of the system default. It
    must never silently disable verification.
    """
    body = (REPO / "src" / "tls_overrides.py").read_text(encoding="utf-8")
    forbidden = [
        r"_create_default_https_context\s*=",
        r"_create_unverified_context",
        r"disable_warnings",
        r"verify\s*=\s*False",
    ]
    for pat in forbidden:
        assert not re.search(pat, body), (
            f"src/tls_overrides.py contains forbidden pattern {pat!r}. "
            "The extra CA bundle must only ADD trust, never weaken it."
        )


def test_llm_verify_default_is_true_when_env_unset():
    """When LLM_CA_BUNDLE is unset, llm_verify() must return True so httpx
    falls through to its built-in trust store. This is the safe default —
    operators have to opt in to get any change at all."""
    os.environ.pop("LLM_CA_BUNDLE", None)
    import importlib

    import src.tls_overrides as mod
    importlib.reload(mod)
    assert mod.llm_verify() is True, (
        f"Default llm_verify() must be True (httpx built-in trust store); "
        f"got {mod.llm_verify()!r}. An accidental non-True default would "
        "turn an opt-in extension into a process-wide change."
    )


def test_llm_verify_falls_back_to_true_for_missing_bundle_file():
    """Pointing LLM_CA_BUNDLE at a non-existent path must NOT raise and
    must fall back to verify=True (system trust). A misconfigured env var
    on a deploy box should never produce a silently TLS-disabled process."""
    os.environ["LLM_CA_BUNDLE"] = "/nonexistent/path/extra-roots.pem"
    try:
        import importlib

        import src.tls_overrides as mod
        importlib.reload(mod)
        assert mod.llm_verify() is True
    finally:
        os.environ.pop("LLM_CA_BUNDLE", None)
