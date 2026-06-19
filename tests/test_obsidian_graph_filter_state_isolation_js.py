import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "plugins" / "obsidian" / "frontend" / "main.js"
_HAS_NODE = shutil.which("node") is not None


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"function {name}\(", source)
    assert match, f"{name} not found in main.js"
    start = match.start()
    depth = 0
    brace = None
    for index in range(source.index("(", match.end() - 1), len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                brace = source.index("{", index)
                break
    assert brace is not None, f"{name} signature did not close"
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} body did not close")


def _run_graph_state_probe():
    source = _SRC.read_text(encoding="utf-8")
    names = (
        "createDefaultGraphFilterState",
        "normalizeGraphFilterState",
        "createGraphViewState",
        "graphStorageScope",
        "graphFilterScopeKey",
    )
    functions = "\n\n".join(_extract_function(source, name) for name in names)
    js = f"""
    const source = {json.dumps(source)};
    const OBSIDIAN_GRAPH_FILTERS_KEY_PREFIX = 'odysseus.obsidian.graphFilters';
    function graphFocusPath() {{ return 'Project A/Focus.md'; }}
    {functions}
    let graphViewState = createGraphViewState();

    const first = createDefaultGraphFilterState();
    const second = createDefaultGraphFilterState();
    first.nodes.markdown = false;
    first.edges.wiki_link = false;
    first.tags.push('alpha');

    const scopedA = graphFilterScopeKey(graphStorageScope({{ lensMode: 'overview', focusPath: 'Project A/Focus.md' }}));
    const scopedB = graphFilterScopeKey(graphStorageScope({{ lensMode: 'overview', focusPath: 'Project B/Focus.md' }}));
    const scopedLens = graphFilterScopeKey(graphStorageScope({{ lensMode: 'review_queue', focusPath: 'Project A/Focus.md' }}));
    const normalized = normalizeGraphFilterState({{
      nodes: {{ markdown: false }},
      edges: {{ shared_tag: false }},
      tags: ['one'],
    }});

    console.log(JSON.stringify({{
      freshNestedState: second.nodes.markdown === true && second.edges.wiki_link === true && second.tags.length === 0,
      scopedKeysDiffer: scopedA !== scopedB && scopedA !== scopedLens,
      scopedA,
      scopedB,
      normalizedKeepsDefaults: normalized.nodes.folder === true && normalized.edges.wiki_link === true,
      normalizedCopiesTags: normalized.tags.length === 1 && normalized.tags !== first.tags,
      renderTokenStartsAtZero: graphViewState.renderToken === 0,
      noGlobalSingletons: !source.includes('let graphFilterState = {{') && !source.includes(\"let graphLensMode = 'overview';\"),
    }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_graph_filter_helpers_isolate_scoped_state_and_defaults():
    result = _run_graph_state_probe()

    assert result["freshNestedState"] is True
    assert result["scopedKeysDiffer"] is True
    assert result["scopedA"].startswith("odysseus.obsidian.graphFilters:overview:")
    assert result["scopedB"].startswith("odysseus.obsidian.graphFilters:overview:")
    assert result["normalizedKeepsDefaults"] is True
    assert result["normalizedCopiesTags"] is True
    assert result["renderTokenStartsAtZero"] is True
    assert result["noGlobalSingletons"] is True
