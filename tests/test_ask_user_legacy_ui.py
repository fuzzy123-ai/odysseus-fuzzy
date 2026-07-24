from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ask_user_replay_is_read_only_and_inline() -> None:
    renderer = _read("static/js/chatRenderer.js")

    assert "export function renderAskUserCard(data, options = {})" in renderer
    assert "const mount = options.mount || chatBox;" in renderer
    assert "const interactive = options.interactive !== false" in renderer
    assert "card.dataset.askUserInteractive = interactive ? 'true' : 'false';" in renderer
    assert (
        "renderAskUserCard(ev.ask_user, { interactive: false, mount: threadWrap, scroll: false });"
        in renderer
    )


def test_ask_user_live_branch_uses_single_renderer() -> None:
    chat = _read("static/js/chat.js")
    branch_start = chat.index("} else if (json.type === 'ask_user') {")
    branch_end = chat.index("} else if (json.type === 'plan_update') {", branch_start)
    branch = chat[branch_start:branch_end]

    assert "chatRenderer.renderAskUserCard(json.data || {});" in branch
    assert "const _aq = json.data || {};" not in branch
    assert "document.createElement('div')" not in branch


def test_ask_user_read_only_styles_are_present() -> None:
    styles = _read("static/style.css")

    assert ".ask-user-card.read-only" in styles
    assert ".ask-user-status" in styles
    assert ".agent-tool-screenshot" in styles
