from pathlib import Path
import runpy


SCRIPT = (
    Path(__file__).parents[1]
    / "ops"
    / "homeserver"
    / "patch-security-reporter-auto-update-context.py"
)


def _module():
    return runpy.run_path(str(SCRIPT), run_name="security_reporter_patcher")


def _fixture(module: dict) -> str:
    return "\n".join(
        [
            module["CONSTANT_ANCHOR"],
            "def run(argv, *, timeout=30):\n    raise NotImplementedError\n",
            module["FUNCTION_ANCHOR"],
            "    return None\n",
            "def watch():",
            module["ALERTS_ANCHOR"],
            "        item for item in []",
            "    ]",
            module["AUDIT_ANCHOR"],
            module["STATUS_ANCHOR"],
        ]
    )


def test_patch_is_complete_and_idempotent():
    module = _module()
    patched = module["patch_source"](_fixture(module))

    assert module["MARKER"] in patched
    assert "def verified_auto_update_active()" in patched
    assert "🔄 Debian-Wartungsmeldung" in patched
    assert "Sicherheitsmonitor aktiv; keine Gegenmaßnahme erforderlich." in patched
    assert module["patch_source"](patched) == patched


def test_patch_fails_closed_when_expected_source_anchor_is_missing():
    module = _module()
    source = _fixture(module).replace(module["STATUS_ANCHOR"], "")

    try:
        module["patch_source"](source)
    except RuntimeError as exc:
        assert "expected exactly one patch anchor" in str(exc)
    else:
        raise AssertionError("missing source anchor should reject the patch")
