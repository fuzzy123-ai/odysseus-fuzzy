from pathlib import Path


ROADMAP = Path(__file__).resolve().parents[1] / "docs" / "plans" / "anti-hallucination-evidence-roadmap.md"


def test_anti_hallucination_roadmap_covers_runtime_self_knowledge_points():
    text = ROADMAP.read_text(encoding="utf-8")

    required_terms = [
        "Runtime-Snapshot",
        "Live Tool Registry",
        "runtime_tool_status",
        "/api/system/runtime-tools",
        "Capability Probe",
        "Evidence Ledger",
        "Recent Changes",
        "Run-State-Modell",
        "Ask-User Policy",
        "Tool-Failure-Transparenz",
        "memory_is_not_authoritative",
        "Context-Efficiency-Roadmap",
        "DNS-Rebinding",
        "PinnedPublicHttpTransport",
    ]

    for term in required_terms:
        assert term in text


def test_anti_hallucination_roadmap_marks_runtime_capabilities_as_non_memory_truth():
    text = ROADMAP.read_text(encoding="utf-8")

    assert "Nicht als Memory speichern" in text
    assert "aktuelle Tool-Liste" in text
    assert "aktuelle Systemversion" in text
    assert "Memory ist dafuer nicht autoritativ" in text
