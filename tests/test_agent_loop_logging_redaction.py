from pathlib import Path


def test_agent_loop_does_not_log_agent_content_previews():
    source = Path("src/agent_loop.py").read_text(encoding="utf-8")

    assert "[agent-intent] latest=%r" not in source
    assert "retrieval_query=%r" not in source
    assert "resp_preview" not in source
    assert "Preview: {resp_preview}" not in source
