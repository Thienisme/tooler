"""
autovid — explainer-video production pipeline (entry point).

Usage:
    python autovid.py validate <workspace>/script.json
    python autovid.py validate <workspace>/script.json --strict

See `python autovid.py --help` for the full stage list.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) in sys.path:
    sys.path.remove(str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR))

from autovid.presentation.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
