#!/usr/bin/env python3
"""
Start the video design studio.

    python3 studio.py                        open the demo project
    python3 studio.py projects/demo/script.json

`src` is put in front of sys.path because the project root holds an
`autovid.py` that would otherwise shadow the package of the same name.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC_DIR))

from autovid.presentation.studio.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())