"""Node-driven regression coverage for body-portaled dropdown z-order."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "js" / "toolWindowZOrder.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_portal_z_clears_dock_chip_floor_when_no_modal_is_open():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topPortalZ }} from '{HELPER.as_uri()}';
            const root = {{ querySelectorAll() {{ return []; }} }};
            console.log(JSON.stringify({{ z: topPortalZ({{ root, getStyle: () => ({{}}) }}) }}));
            """
        )
    )

    assert values == {"z": 10031}


def test_portal_z_sits_above_a_modal_whose_counter_has_climbed_past_10001():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topPortalZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const modal = {{ id: 'memory-modal', classList: cls(), style: {{ zIndex: '99999' }} }};
            const root = {{ querySelectorAll() {{ return [modal]; }} }};
            console.log(JSON.stringify({{ z: topPortalZ({{ root, getStyle: (el) => el.style }}) }}));
            """
        )
    )

    assert values == {"z": 100000}


def test_portal_z_uses_chip_floor_when_the_open_modal_sits_below_it():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topPortalZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const modal = {{ id: 'cookbook-modal', classList: cls(), style: {{ zIndex: '5000' }} }};
            const root = {{ querySelectorAll() {{ return [modal]; }} }};
            console.log(JSON.stringify({{ z: topPortalZ({{ root, getStyle: (el) => el.style }}) }}));
            """
        )
    )

    assert values == {"z": 10031}
