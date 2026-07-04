from src.agent_loop_prompts import _assemble_prompt


def test_agent_prompt_forbids_nextcloud_credentials_in_chat():
    prompt = _assemble_prompt(set(), compact=True)

    assert "Nextcloud/WebDAV Zugangsdaten are server-side runtime configuration only" in prompt
    assert "Never ask for Nextcloud URL" in prompt
    assert "chat/Telegram" in prompt
    assert "server-side config or operator gate is missing" in prompt
