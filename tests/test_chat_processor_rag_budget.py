from src.chat_processor import build_budgeted_rag_context, rag_context_budget_units
from src.prompt_security import untrusted_context_message
from src.token_budget import count_text_tokens


def test_rag_context_budget_units_scales_with_request_budget():
    assert rag_context_budget_units(None) == 1200
    assert rag_context_budget_units(1000) == 256
    assert rag_context_budget_units(20000) == 3600
    assert rag_context_budget_units(100000) == 4000


def test_build_budgeted_rag_context_preserves_source_spans_and_caps_content():
    context, sources, truncated = build_budgeted_rag_context(
        [
            {
                "document": "alpha " * 300,
                "similarity": 0.91,
                "metadata": {
                    "filename": "notes.md",
                    "splitter_version": "rag_structured_v1",
                    "char_start": 10,
                    "char_end": 200,
                },
            },
            {
                "document": "beta " * 300,
                "similarity": 0.8,
                "metadata": {"filename": "more.md"},
            },
        ],
        budget_units=180,
    )

    assert context.startswith("Relevant documents:")
    injected = untrusted_context_message("retrieved documents", context)
    assert count_text_tokens(injected["content"]) <= 180
    assert "[Truncated]" in context
    assert truncated >= 1
    assert sources == [
        {
            "filename": "notes.md",
            "snippet": sources[0]["snippet"],
            "similarity": 0.91,
            "splitter_version": "rag_structured_v1",
            "char_start": 10,
            "char_end": 200,
            "budget_units_est": sources[0]["budget_units_est"],
            "truncated": True,
        }
    ]


def test_build_budgeted_rag_context_returns_empty_when_budget_too_small():
    context, sources, truncated = build_budgeted_rag_context(
        [{"document": "alpha", "similarity": 1.0, "metadata": {"filename": "a.md"}}],
        budget_units=1,
    )

    assert context == ""
    assert sources == []
    assert truncated == 1


def test_build_budgeted_rag_context_routes_model_hint_and_caps_unicode():
    model_hint = "unregistered/model"
    context, sources, truncated = build_budgeted_rag_context(
        [
            {
                "document": "Grüße Ω code() " * 40,
                "similarity": 0.9,
                "metadata": {"filename": "synthetic.md"},
            }
        ],
        budget_units=600,
        model_hint=model_hint,
    )

    assert context.startswith("Relevant documents:")
    injected = untrusted_context_message("retrieved documents", context)
    assert count_text_tokens(injected["content"], model_hint=model_hint) <= 600
    assert sources[0]["truncated"] is True
    assert truncated == 1
