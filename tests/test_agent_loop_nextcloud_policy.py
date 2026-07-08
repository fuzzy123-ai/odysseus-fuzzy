from src.agent_loop_prompts import _assemble_prompt


def test_agent_prompt_forbids_nextcloud_credentials_in_chat():
    prompt = _assemble_prompt(set(), compact=True)

    assert "Nextcloud/WebDAV Zugangsdaten are server-side runtime configuration only" in prompt
    assert "Never ask for Nextcloud URL" in prompt
    assert "chat/Telegram" in prompt
    assert "server-side config or operator gate is missing" in prompt


def test_agent_prompt_scopes_assumption_preface_to_projects_and_roadmaps():
    compact_prompt = _assemble_prompt(set(), compact=True)
    full_prompt = _assemble_prompt(set(), compact=False)

    for prompt in (compact_prompt, full_prompt):
        assert "Scope/assumption preface" in prompt
        assert "create a new project" in prompt
        assert "plan a new roadmap" in prompt
        assert "execute/start a roadmap" in prompt
        assert '"Need to know:"' in prompt
        assert '"Assumptions:"' in prompt
        assert "proceed if it is non-blocking" in prompt
        assert "ask one concise question and stop" in prompt
        assert "trivial notes, todos, reminders, calendar items" in prompt
