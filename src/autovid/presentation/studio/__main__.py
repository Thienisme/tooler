"""
Launch the studio.

    python -m autovid.presentation.studio [script.json]

Opens the given project, or the demo project if no path is given, or an
empty project list if there is nothing to open.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .window import StudioWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    script_path = Path(argv[0]) if argv else None

    app = QApplication.instance() or QApplication(sys.argv)
    window = StudioWindow(script_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())