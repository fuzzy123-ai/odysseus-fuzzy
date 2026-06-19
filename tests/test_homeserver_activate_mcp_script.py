from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "homeserver" / "activate-mcp-server.sh"


def test_mcp_activation_script_targets_latest_published_commit():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'ODYSSEUS_MCP_EXPECTED_COMMIT="${ODYSSEUS_MCP_EXPECTED_COMMIT:-3e164879}"' in text
    assert "c85e7bcd" not in text


def test_mcp_activation_script_keeps_safe_deploy_guards():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "git merge --ff-only FETCH_HEAD" in text
    assert "ops/homeserver/pre-update-snapshot.sh" in text
    assert "does not match expected" in text
    assert "ODYSSEUS_INTERNAL_TOKEN" in text
