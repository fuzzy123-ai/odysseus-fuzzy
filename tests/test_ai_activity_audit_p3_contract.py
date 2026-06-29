from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_repo_file(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_domain_modules_label_ai_activity_surfaces():
    expectations = {
        ("routes", "calendar_routes.py"): [
            'surface="calendar"',
            'prompt_type="calendar_quick_parse"',
        ],
        ("routes", "document_routes.py"): [
            'surface="document"',
            'prompt_type="document_ai_tidy"',
            'surface="vision"',
            'prompt_type="document_pdf_vision_fill"',
        ],
        ("routes", "memory_routes.py"): [
            'surface="memory"',
            'prompt_type="memory_chat_extract"',
            'prompt_type="memory_file_extract"',
            'doc_id="uploaded_file"',
        ],
        ("routes", "email_pollers.py"): [
            'surface="email"',
            'prompt_type="email_background_reply"',
            'prompt_type="email_calendar_extract"',
            'prompt_type="email_urgency_classify"',
            "_audit_email_correlation(message_id)",
        ],
        ("routes", "email_routes.py"): [
            'prompt_type="email_writing_style_extract"',
            'prompt_type="email_ai_reply"',
        ],
        ("routes", "preset_routes.py"): [
            'surface="preset"',
            'prompt_type="preset_expand_character_prompt"',
        ],
        ("routes", "task_routes.py"): [
            'surface="task"',
            'prompt_type="task_name_generate"',
            'prompt_type="task_parse_draft"',
        ],
        ("routes", "history_routes.py"): [
            'surface="history"',
            'prompt_type="history_manual_compact"',
        ],
        ("routes", "session_routes.py"): [
            'surface="session"',
            'prompt_type="session_manual_compact"',
        ],
        ("routes", "note_routes.py"): [
            'surface="notes"',
            'prompt_type="note_reminder_synthesis"',
        ],
        ("routes", "skills_routes.py"): [
            'surface="skills"',
            'prompt_type="skill_run_evaluation"',
            'prompt_type="skill_necessity_audit"',
            'prompt_type="skill_retrieval_precision_audit"',
            'prompt_type="skill_markdown_improve"',
        ],
        ("src", "ai_interaction.py"): [
            'surface="model_pipeline"',
            'prompt_type="model_pipeline_step"',
        ],
        ("src", "builtin_actions.py"): [
            'prompt_type="memory_consolidate"',
            'prompt_type="calendar_classify_events"',
            'prompt_type="email_signature_extract"',
            'prompt_type="email_urgency_classify"',
        ],
        ("src", "research_handler.py"): [
            'surface="research"',
            'prompt_type="research_query_synthesis"',
            'prompt_type="research_plan_generate"',
            'prompt_type="research_endpoint_probe"',
        ],
        ("src", "deep_research.py"): [
            'surface="research"',
            'prompt_type="deep_research_llm"',
        ],
        ("src", "context_compactor.py"): [
            'surface="context_compactor"',
            'prompt_type="context_auto_compact"',
        ],
        ("src", "agent_loop.py"): [
            'surface="agent"',
            'prompt_type="agent_verifier"',
            'prompt_type="agent_grace_synthesis"',
        ],
        ("src", "agent_tools", "model_interaction_tools.py"): [
            'surface="agent_tool"',
            'prompt_type="ask_model_tool"',
            'prompt_type="consult_teacher_tool"',
        ],
        ("src", "agent_tools", "session_tools.py"): [
            'surface="agent_tool"',
            'prompt_type="message_session_tool"',
        ],
    }

    for path_parts, needles in expectations.items():
        src = read_repo_file(*path_parts)
        for needle in needles:
            assert needle in src


def test_legacy_vault_replaces_obsolete_audit_surface_name():
    files = [
        ("plugins", "obsidian", "backend", "model_router.py"),
        ("plugins", "obsidian", "backend", "routes.py"),
        ("plugins", "obsidian", "plugin.py"),
    ]

    for parts in files:
        src = read_repo_file(*parts)
        assert 'surface="legacy_vault"' in src
        assert "legacy_vault_" in src
        assert 'surface="obsidian"' not in src
        assert 'prompt_type="obsidian_' not in src
