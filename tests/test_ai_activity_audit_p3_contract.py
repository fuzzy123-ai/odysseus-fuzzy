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
