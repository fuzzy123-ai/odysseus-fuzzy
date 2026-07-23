from __future__ import annotations

import ast
import importlib.abc
import os
import subprocess
import sys
from importlib import util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RAG_MANAGER_PATH = ROOT / "src" / "rag_manager.py"
INDEX_DOCUMENTS_PATH = ROOT / "scripts" / "index_documents.py"


def _run_from_repository_root(code: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def test_rag_manager_has_one_canonical_top_level_vector_import() -> None:
    source = RAG_MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RAG_MANAGER_PATH))
    vector_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "VectorRAG" for alias in node.names)
    ]

    assert len(vector_imports) == 1
    assert vector_imports[0] in tree.body
    assert vector_imports[0].module == "src.rag_vector"
    assert vector_imports[0].level == 0
    assert [(alias.name, alias.asname) for alias in vector_imports[0].names] == [("VectorRAG", None)]
    assert not any(
        isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ImportError"
        for node in ast.walk(tree)
    )
    assert "sys.path" not in source


def test_internal_rag_vector_import_error_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = ImportError("internal rag_vector dependency failed")
    executed_modules: list[str] = []

    class FailingRagVectorLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module) -> None:
            executed_modules.append(module.__name__)
            module.__dict__["_synthetic_internal_import_error"] = marker
            module_body = compile(
                "raise _synthetic_internal_import_error",
                "<synthetic src.rag_vector module body>",
                "exec",
            )
            exec(module_body, module.__dict__)

    loader = FailingRagVectorLoader()

    class RagVectorFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "src.rag_vector":
                return util.spec_from_loader(fullname, loader)
            return None

    # Force this import through a module loader whose module body raises the
    # marker. This proves a transitive/internal failure is not mistaken for a
    # missing top-level path and retried through a legacy import branch.
    monkeypatch.delitem(sys.modules, "src.rag_vector", raising=False)
    monkeypatch.setattr(sys, "meta_path", [RagVectorFinder(), *sys.meta_path])
    spec = util.spec_from_file_location("_sar_rag_manager_import_probe", RAG_MANAGER_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)

    with pytest.raises(ImportError) as raised:
        spec.loader.exec_module(module)

    assert raised.value is marker
    assert executed_modules == ["src.rag_vector"]


def test_repository_root_services_and_script_module_imports_succeed() -> None:
    result = _run_from_repository_root(
        "import src.rag_manager; import services.docs; import scripts.index_documents"
    )

    assert result.returncode == 0, result.stderr


def test_supported_index_documents_entrypoint_imports_the_canonical_manager(tmp_path: Path) -> None:
    missing_directory = tmp_path / "intentionally-missing"
    code = f"""
import runpy
import src.constants
import src.rag_manager

class ProbeRAGManager:
    created = 0

    def __init__(self):
        type(self).created += 1

src.constants.PERSONAL_DIR = {str(missing_directory)!r}
src.rag_manager.RAGManager = ProbeRAGManager
runpy.run_path({str(INDEX_DOCUMENTS_PATH)!r}, run_name="__main__")
assert ProbeRAGManager.created == 1
"""
    result = _run_from_repository_root(code)

    assert result.returncode == 0, result.stderr
