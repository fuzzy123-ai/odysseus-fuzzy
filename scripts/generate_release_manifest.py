"""CLI wrapper for the revision-bound Odysseus release manifest."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.release_manifest import main


if __name__ == "__main__":
    raise SystemExit(main())
