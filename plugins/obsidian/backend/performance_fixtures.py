import os
import time
from typing import Any, Dict

from .vault_model import graph_payload

LARGE_VAULT_RC_NOTE_COUNT = 120
LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS = 700.0
LARGE_VAULT_RC_WORST_THRESHOLD_MS = 1200.0


def create_large_vault_fixture(vault_dir: str, note_count: int = 120) -> Dict[str, Any]:
    """Create deterministic markdown notes for graph/index performance checks."""
    os.makedirs(vault_dir, exist_ok=True)
    folders = ["Projects", "Architecture", "Tests", "Risks"]
    for folder in folders:
        os.makedirs(os.path.join(vault_dir, folder), exist_ok=True)

    paths = []
    for index in range(note_count):
        folder = folders[index % len(folders)]
        path = f"{folder}/Note-{index:03d}.md"
        previous_link = f"[[Note-{index - 1:03d}]]" if index else ""
        next_link = f"[[Note-{(index + 1) % note_count:03d}]]"
        tag = ["#planning", "#architecture", "#test", "#risk"][index % 4]
        body = (
            f"# Note {index:03d}\n\n"
            f"{tag} #large-vault\n\n"
            f"{previous_link}\n{next_link}\n\n"
            f"This note mentions Note {(index + 2) % note_count:03d} for filename relationship coverage.\n"
        )
        with open(os.path.join(vault_dir, path), "w", encoding="utf-8") as fh:
            fh.write(body)
        paths.append(path)

    return {"note_count": note_count, "paths": paths}


def profile_graph_build(vault_dir: str) -> Dict[str, Any]:
    started = time.perf_counter()
    payload = graph_payload(vault_dir)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    graph = payload["graph"]
    return {
        "elapsed_ms": elapsed_ms,
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
    }


def profile_graph_build_baseline(vault_dir: str, runs: int = 4) -> Dict[str, Any]:
    """Return warm-run timings for a deterministic RC-sized large vault fixture."""
    # Warm cache/filesystem/path imports once before measuring repeated runs.
    warmup = profile_graph_build(vault_dir)
    samples = [profile_graph_build(vault_dir)["elapsed_ms"] for _ in range(max(runs, 1))]
    ordered = sorted(samples)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else round((ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2, 2)
    max_sample = max(samples)
    median_headroom = round(LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS - median, 2)
    worst_headroom = round(LARGE_VAULT_RC_WORST_THRESHOLD_MS - max_sample, 2)
    return {
        "warmup_elapsed_ms": warmup["elapsed_ms"],
        "runs": len(samples),
        "samples_ms": samples,
        "median_ms": median,
        "max_ms": max_sample,
        "min_ms": min(samples),
        "nodes": warmup["nodes"],
        "edges": warmup["edges"],
        "thresholds_ms": {
            "median": LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS,
            "worst": LARGE_VAULT_RC_WORST_THRESHOLD_MS,
        },
        "headroom_ms": {
            "median": median_headroom,
            "worst": worst_headroom,
        },
        "within_threshold": median_headroom >= 0 and worst_headroom >= 0,
    }
