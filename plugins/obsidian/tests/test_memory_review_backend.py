import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.memory_review import (
    REVIEW_QUEUE_FOLDER,
    MemoryReviewRequest,
    apply_memory_review_plan,
    build_memory_review_plan,
)


def test_memory_review_plan_flags_possible_duplicate_candidates():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo Memory.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo Memory\n\nGraph memory review context for the release checklist.")

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Demo Memory",
                    "content": "Graph memory review context for the release checklist.",
                    "source": "manual",
                },
                action="save_to_obsidian",
                target_folder="Memory Review",
            ),
        )

        assert plan.duplicate_candidates
        assert plan.duplicate_candidates[0].path == "Projects/Demo Memory.md"
        assert any("Possible duplicate vault notes found" in warning for warning in plan.warnings)


def test_memory_review_append_plan_keeps_duplicate_warning_visible():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo Memory.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo Memory\n\nGraph memory review context for the release checklist.")

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Demo Memory",
                    "content": "Graph memory review context for the release checklist.",
                    "source": "manual",
                },
                action="append_to_note",
                target_note="Projects/Demo Memory.md",
            ),
        )

        assert plan.files[0].mode == "append"
        assert plan.duplicate_candidates
        assert any("appending is better than creating another memory note" in warning for warning in plan.warnings)


def test_memory_review_append_plan_normalizes_source_alias_and_adds_anchor():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo Memory.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo Memory\n\nExisting context.")

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Queue Followup",
                    "content": "Keep the memory review workflow linked to Demo.",
                    "source": "email",
                    "source_ref": "Thread 42 / Release",
                    "risk": "high",
                },
                action="append_to_note",
                target_note="Projects/Demo Memory.md",
            ),
        )

        content = plan.files[0].content
        assert plan.files[0].mode == "append"
        assert "#source/mail" in plan.files[0].tags
        assert "## Memory Review " in content
        assert "<a id=\"memory-review-" in content
        assert "email" not in content
        assert "Quelle: mail (Thread 42 / Release)" in content
        assert "Risiko: high" in content


def test_memory_review_save_plan_normalizes_manual_source_aliases_in_frontmatter():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Human note",
                    "content": "Manually reviewed note source aliases should normalize.",
                    "source": "user",
                },
                action="save_to_obsidian",
                target_folder="Memory Review",
            ),
        )

        assert plan.files[0].frontmatter["source"] == "manual"
        assert "#source/manual" in plan.files[0].tags


def test_memory_review_queue_plan_uses_review_queue_folder_and_duplicate_warning():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo Memory.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo Memory\n\nGraph memory review context for the release checklist.")

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Demo Memory",
                    "content": "Graph memory review context for the release checklist.",
                    "source": "manual",
                },
                action="review_queue",
                target_folder="Ignored Folder",
            ),
        )

        assert plan.action == "review_queue"
        assert plan.target_folder == REVIEW_QUEUE_FOLDER
        assert plan.files[0].path.startswith(f"{REVIEW_QUEUE_FOLDER}/")
        assert plan.duplicate_candidates
        assert any("stays queued" in warning for warning in plan.warnings)


def test_memory_review_queue_apply_creates_queue_note_and_relationships():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as fh:
            fh.write("# Demo\n\n#project/demo\n\nExisting graph context.")

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Queue me",
                    "content": "Keep this note queued until the team decides how to store it.",
                    "source": "chat",
                },
                action="review_queue",
                project="Demo",
                tags=["#project/demo"],
                link_paths=["Projects/Demo.md"],
            ),
        )

        result = apply_memory_review_plan(tmpdir, plan)
        created_path = result["created_files"][0]
        created_abs = os.path.join(tmpdir, *created_path.split("/"))

        assert created_path.startswith(f"{REVIEW_QUEUE_FOLDER}/")
        assert os.path.exists(created_abs)
        with open(created_abs, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "Keep this note queued until the team decides how to store it." in content
        assert "[[Projects/Demo]]" in content
        assert result["relationships"][0]["target"] == "Projects/Demo.md"


def test_memory_review_queue_plan_reuses_existing_queue_note_for_same_source_ref():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Review Queue"), exist_ok=True)
        queue_path = os.path.join(tmpdir, "AI Memory", "Review Queue", "2026-06-16-release-check.md")
        with open(queue_path, "w", encoding="utf-8") as fh:
            fh.write(
                "---\n"
                "type: memory\n"
                "status: review\n"
                "source: chat\n"
                "created: 2026-06-16\n"
                "updated: 2026-06-16\n"
                "source_ref: thread-42\n"
                "---\n\n"
                "# Release check\n"
            )

        plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(
                candidate={
                    "title": "Release check",
                    "content": "Same source should update the queued review note instead of duplicating it.",
                    "source": "conversation",
                    "source_ref": "thread-42",
                },
                action="review_queue",
            ),
        )

        assert plan.action == "review_queue"
        assert plan.files[0].mode == "append"
        assert plan.files[0].path == "AI Memory/Review Queue/2026-06-16-release-check.md"
        assert "#source/chat" in plan.files[0].tags
        assert any("Existing queued review note matched this source" in warning for warning in plan.warnings)

        result = apply_memory_review_plan(tmpdir, plan)
        assert result["created_files"] == []
        assert result["updated_files"] == ["AI Memory/Review Queue/2026-06-16-release-check.md"]
        with open(queue_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "Same source should update the queued review note instead of duplicating it." in content
