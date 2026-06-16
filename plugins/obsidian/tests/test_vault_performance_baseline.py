import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.performance_fixtures import (
    LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS,
    LARGE_VAULT_RC_NOTE_COUNT,
    LARGE_VAULT_RC_WORST_THRESHOLD_MS,
    create_large_vault_fixture,
    profile_graph_build,
    profile_graph_build_baseline,
)


def test_large_vault_fixture_produces_retrievable_graph_baseline():
    with tempfile.TemporaryDirectory() as tmpdir:
        fixture = create_large_vault_fixture(tmpdir, note_count=48)
        profile = profile_graph_build(tmpdir)

        assert fixture["note_count"] == 48
        assert profile["nodes"] >= 48
        assert profile["edges"] >= 48
        assert profile["elapsed_ms"] >= 0


def test_large_vault_graph_build_meets_rc_thresholds():
    with tempfile.TemporaryDirectory() as tmpdir:
        fixture = create_large_vault_fixture(tmpdir, note_count=LARGE_VAULT_RC_NOTE_COUNT)
        baseline = profile_graph_build_baseline(tmpdir, runs=4)

        assert fixture["note_count"] == LARGE_VAULT_RC_NOTE_COUNT
        assert baseline["nodes"] >= LARGE_VAULT_RC_NOTE_COUNT
        assert baseline["edges"] >= LARGE_VAULT_RC_NOTE_COUNT
        assert baseline["median_ms"] <= LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS
        assert baseline["max_ms"] <= LARGE_VAULT_RC_WORST_THRESHOLD_MS
