import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_UTILS = (_REPO / "static" / "js" / "calendar" / "utils.js").resolve().as_uri()
_CALENDAR_JS = _REPO / "static" / "js" / "calendar.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_cssurlescape_doubles_backslashes_before_quotes():
    js = textwrap.dedent(
        f"""
        const {{ _cssUrlEscape }} = await import('{_UTILS}');
        console.log(JSON.stringify({{
          backslash: _cssUrlEscape('a\\\\b'),
          trailing: _cssUrlEscape('img\\\\'),
          quote: _cssUrlEscape("a'b"),
          dquote: _cssUrlEscape('a"b'),
        }}));
        """
    )
    out = json.loads(_run(js))
    assert out["backslash"] == r"a\\b"
    assert out["trailing"] == r"img\\"
    assert out["quote"] == r"a\'b"
    assert out["dquote"] == "a%22b"


def test_calbgcss_escapes_quote_breakout():
    js = textwrap.dedent(
        f"""
        const {{ _calBgCss }} = await import('{_UTILS}');
        console.log(JSON.stringify(_calBgCss("bg:a'); X{{}}//", 'var(--accent)')));
        """
    )
    css = json.loads(_run(js))
    assert r"\'" in css
    assert "url('a\\'); X{}//')" in css


def test_every_calendar_url_interpolation_is_escaped():
    src = _CALENDAR_JS.read_text(encoding="utf-8")
    interps = re.findall(r"url\('\$\{([^}]*)\}'\)", src)
    assert interps
    assert not [expr for expr in interps if "_cssUrlEscape(" not in expr]
