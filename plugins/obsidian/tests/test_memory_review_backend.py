import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.memory_review import MemoryReviewRequest, build_memory_review_plan


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
