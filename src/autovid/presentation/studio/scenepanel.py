"""
ScenePanel: read-only summary of every scene, so the author can see the
narration, captions, host and punch-in all in one place while they arrange
the stage.  One compact table with one row per scene and one column per
field -- picture behind the scene, spoken text, captions, style, impact,
characters and the storytelling frame.

Clicking a row is how the author picks a scene from here, so the panel says
which Scene it stands for through `scene_picked`.  What it will not do is
edit: no cell writes back, so the canvas and the inspector can never
disagree with what this table says.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from autovid.presentation.studio.store import Scene

# (table column) -> (display name, the key `scene_summary` fills in)
# The table is read-only: nothing is written back through it, only through the
# scene itself, so the canvas and the inspector can never disagree with it.
COLUMNS: list[tuple[str, str]] = [
    ("image", "Hinh"),
    ("text", "Noi dung thoai"),
    ("subtitles", "Chu de them"),
    ("style", "Style"),
    ("impact", "Punch-up"),
    ("characters", "Nhan vat"),
    ("frame", "Khung hinh"),
]

NO_BACKGROUND = "(chua chon nen)"
NO_SUBTITLES = "(chua them chu de)"
NO_CHARACTERS = "(khong co)"

_HEADER_LABELS = [name for _, name in COLUMNS]


def _basename(path: str) -> str:
    """The file name, which is all a table row has room to say."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _frame_label(scene: Scene, frame: object | None) -> str:
    """
    What the frame column says for one scene.

    The frame itself is a project-wide setting; a scene only carries the
    opt-out, so both are needed to answer "does this scene draw the panel,
    and who is in it".  None means nothing is known yet.
    """
    if frame is None:
        return "?"
    drawn = bool(getattr(frame, "enabled", False)) and (
        scene.story_frame_enabled is not False
    )
    if not drawn:
        return "khong"
    host = getattr(frame, "use", None) or getattr(frame, "image_file", None)
    return f"on / {_basename(str(host))}" if host else "on"


def scene_summary(scene: Scene, frame: object | None = None) -> dict[str, str]:
    """One row of text for every column, ready to paint into the table."""
    return {
        "image": _basename(scene.image_file) if scene.image_file else NO_BACKGROUND,
        "text": (scene.text or "").strip() or "(chua co loi thoai)",
        "subtitles": " ".join(
            overlay.text for overlay in scene.overlays
        ).strip() or NO_SUBTITLES,
        "style": scene.motion.type if scene.motion.enabled else "khong",
        "impact": (
            f"cau {scene.impact.at_sentence} / {scene.impact.duration_ms}ms "
            f"/ {scene.impact.intensity}"
            if scene.impact.enabled
            else ""
        ),
        "characters": ", ".join(
            f"{index + 1}.{_basename(character.image_file)}"
            for index, character in enumerate(scene.characters)
        ) or NO_CHARACTERS,
        "frame": _frame_label(scene, frame),
    }


class ScenePanel(QTableWidget):
    """
    A read-only summary of every scene in the current project.

    `scene_row(scene)` turns a Scene into a row index; `selected_scene()`
    turns the active row back into a Scene, or None when nothing is picked.
    `scene_picked` says out loud when the *author* clicked a row, so the
    window can open that scene.
    """

    scene_picked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSortingEnabled(False)
        self.setWordWrap(False)

        self.setHorizontalHeaderLabels(_HEADER_LABELS)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)

        self.setMinimumWidth(480)
        self.setMinimumHeight(180)
        self.setMaximumHeight(460)

        # Row index -> the Scene it describes, so a click can hand the
        # scene itself back instead of a copy of its text.
        self._rows: list[Scene] = []
        # `cellClicked` fires for a real click only -- not for the repaints and
        # programmatic highlights this table does all day, so the window is
        # never told to switch scenes when nothing was asked of it.
        self.cellClicked.connect(self._on_cell_clicked)

    # -- population -------------------------------------------------------
    def set_project(self, project: object | None) -> None:
        """Paint the current project; `project` carries `scenes` and `name`."""
        self._rows = list(getattr(project, "scenes", []) or [])
        self._paint(getattr(project, "story_frame", None))
        self._update_title(project)

    def set_project_scenes(self, scenes: list[Scene], frame: object | None = None) -> None:
        """Rebuild the table from a list of Scene objects."""
        self._rows = list(scenes)
        self._paint(frame)

    def _paint(self, frame: object | None = None) -> None:
        """Fill the cells.  Selection is restored by the caller afterwards."""
        selected = self.currentRow()
        self.clear()
        # `clear()` takes the header labels with it, so they are put back --
        # otherwise the columns lose their names on the first repaint.
        self.setHorizontalHeaderLabels(_HEADER_LABELS)
        self.setRowCount(len(self._rows))
        for row, scene in enumerate(self._rows):
            summary = scene_summary(scene, frame)
            for column, (key, _) in enumerate(COLUMNS):
                self.setItem(row, column, QTableWidgetItem(summary[key]))
        self.resizeRowsToContents()
        if 0 <= selected < len(self._rows):
            self.selectRow(selected)

    # -- lookups ----------------------------------------------------------
    def scene_row(self, scene: Scene | None) -> int:
        """The row that describes `scene`, or -1 when it is not shown."""
        if scene is None:
            return -1
        for row, candidate in enumerate(self._rows):
            if candidate is scene:
                return row
        return -1

    def selected_scene(self) -> Scene | None:
        """The Scene the author picked, or None when nothing is picked."""
        row = self.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # -- interaction ------------------------------------------------------
    def select_scene(self, scene: Scene | None) -> None:
        """Highlight `scene`, or clear the selection when there is none."""
        row = self.scene_row(scene)
        if row < 0:
            # `clearSelection()` leaves the current row standing, which would
            # keep `selected_scene()` reporting a scene nobody is looking at.
            self.clearSelection()
            self.setCurrentCell(-1, -1)
            return
        self.selectRow(row)
        self.scrollToItem(self.item(row, 0), QAbstractItemView.EnsureVisible)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        """The author clicked a cell: name the Scene that row stands for."""
        self.scene_picked.emit(self.scene_at(row))

    def scene_at(self, row: int) -> Scene | None:
        """The Scene shown on `row`, or None when that row is not there."""
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def set_project_dirty(self, project: object | None) -> None:
        """Repaint after an edit, keeping the same scene highlighted."""
        if project is None:
            return
        selected = self.selected_scene()
        self.set_project_scenes(
            getattr(project, "scenes", []), getattr(project, "story_frame", None)
        )
        self.select_scene(selected)

    # -- helpers ----------------------------------------------------------
    def _update_title(self, project: object | None) -> None:
        if project is None:
            self.setWindowTitle("autovid - Scene Panel")
            return
        name = getattr(project, "name", "Project")
        self.setWindowTitle(f"autovid - {name} - Scene Summary")