"""
The studio window: projects on the left, the stage in the middle, the
inspector on the right, and the render action at the bottom.

Layout follows the way the work is actually done -- pick a project, pick a
scene, arrange it, check it -- so the three panes read left to right as
that sequence.  Nothing here knows how video is rendered; it writes a valid
script.json and hands it to the pipeline, which keeps the studio honest
about what it controls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QDrag, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .assets import Asset, AssetLibrary
from .canvas import MIME_BACKGROUND, MIME_CHARACTER, MIME_NARRATOR, StageCanvas
from .inspector import FramePanel, Inspector
from .overlays import ImpactPanel, OverlayPanel
from .scenepanel import ScenePanel
from .store import Placement, Project, ProjectStore, Scene
from .walk import WalkPanel

# The preview's frame budget.  33ms is about 30fps, which is smooth enough to
# read the entrances and slow enough to leave the UI responsive while it runs.
PLAY_INTERVAL_MS = 33

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _repo_root() -> Path:
    """The repository root, whichever prefix autovid was installed under."""
    return Path(__file__).resolve().parents[4]


class RenderThread(QThread):
    """Runs the pipeline off the UI thread so the window stays responsive."""

    output = Signal(str)
    finished_ok = Signal(int)

    def __init__(self, script_path: Path, stage: str, parent=None) -> None:
        super().__init__(parent)
        self.script_path = script_path
        self.stage = stage
        self._log: list[str] = []

    def run(self) -> None:  # noqa: D102 (Qt entry point)
        command = [
            sys.executable,
            str(_repo_root() / "autovid.py"),
            self.stage,
            str(self.script_path),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(_repo_root()),
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if line:
                self._log.append(line)
                self.output.emit(line)
        code = process.wait()
        self.finished_ok.emit(code)

    def log(self) -> str:
        return "\n".join(self._log)


class AssetList(QListWidget):
    """
    One library list whose rows drag onto the stage as the stage's own mime.

    A stock `QListWidget` hands the drop handler Qt's internal model blob,
    which the canvas does not read, so a drag from the library did nothing.
    This one builds the mime itself, from the row's `UserRole` payload, and
    overrides `startDrag` so it hands that same mime to the `QDrag`.
    """

    def __init__(self, mime_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mime_type = mime_type
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def row_mime(self, item: QListWidgetItem) -> QMimeData:
        """The mime for one row: our format, carrying its payload as text."""
        mime = QMimeData()
        mime.setData(
            self.mime_type,
            str(item.data(Qt.UserRole) or "").encode("utf-8"),
        )
        return mime

    def startDrag(self, actions) -> None:  # noqa: N802 (Qt naming)
        item = self.currentItem()
        if item is None:
            # Nothing picked: return rather than raising on a missing item.
            return
        drag = QDrag(self)
        drag.setMimeData(self.row_mime(item))
        drag.setPixmap(self.viewport().grab())
        drag.exec(actions)


class StudioWindow(QMainWindow):
    """The video design studio."""

    def __init__(self, script_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("autovid studio - thiet ke video giai thich")
        self.resize(1500, 900)

        self.projects: list[Project] = []
        self.current_project: Project | None = None
        self.library: AssetLibrary | None = None
        self._render_thread: RenderThread | None = None

        # The live preview clock: a timer that advances the canvas through the
        # scene's estimated timeline so the author can watch a change without
        # rendering.  `_play_progress` is where the clock stands, which is not
        # always `canvas.progress` -- scrubbing by hand moves the canvas too.
        self._play_progress = 0.0
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(PLAY_INTERVAL_MS)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._build_ui()
        self._wire()

        if script_path is not None:
            self.open_project(Path(script_path))
        else:
            default = _repo_root() / "projects" / "demo_story_inside" / "script.json"
            if default.exists():
                self.open_project(default)
            else:
                self._refresh_projects()

    # -- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        self.setCentralWidget(central)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_centre())
        splitter.addWidget(self._build_right())
        splitter.setSizes([300, 720, 380])

        outer.addWidget(self._build_bar())

    # -- the live preview ----------------------------------------------------
    def _on_scrub_changed(self, value: int) -> None:
        """A hand on the scrubber: the canvas follows, the clock stands."""
        self.canvas.set_progress(value / 100.0)

    def toggle_play(self) -> None:
        """Play, or stop if it is already playing."""
        if self._play_timer.isActive():
            self.pause_play()
        else:
            self.start_play()

    def start_play(self) -> None:
        """
        Run the scene on its estimated clock.

        A finished scene starts over rather than sitting at the end, so
        pressing Play twice always shows the whole thing again.
        """
        if self.canvas.scene is None:
            return
        if self.canvas.progress >= 1.0:
            self.canvas.set_progress(0.0)
            self._sync_scrubber()
        self._play_progress = self.canvas.progress
        self._play_timer.start()
        self.play_button.setText("Dung")

    def pause_play(self) -> None:
        """Stop the clock, leaving the stage wherever it got to."""
        self._play_timer.stop()
        self.play_button.setText("Chay")

    def _preview_span(self) -> float:
        """How long the current scene runs, in estimated seconds."""
        timeline = self.canvas.timeline()
        return timeline.span if timeline is not None else 0.0

    def _on_play_tick(self) -> None:
        """
        One frame of the preview.

        The clock is wall time, not a fixed step, so the preview takes as
        long as the scene should -- a slow speaker reads slowly, however
        fast the timer fires.
        """
        if not self._play_timer.isActive():
            return
        span = self._preview_span()
        if span <= 0.0:
            self.pause_play()
            return
        step = (PLAY_INTERVAL_MS / 1000.0) / span
        self._play_progress = min(1.0, self._play_progress + step)
        self.canvas.set_progress(self._play_progress)
        self._sync_scrubber()
        if self._play_progress >= 1.0:
            # Stopped at the end, not left spinning past it.
            self.pause_play()

    def _sync_scrubber(self) -> None:
        """Show where the canvas is, without the slider echoing back."""
        self.scrub.blockSignals(True)
        self.scrub.setValue(int(round(self.canvas.progress * 100)))
        self.scrub.blockSignals(False)

    # -- construction -----------------------------------------------------
    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # Projects
        projects_box = QGroupBox("Du an")
        projects_layout = QVBoxLayout(projects_box)
        self.project_list = QListWidget()
        self.project_list.setSelectionMode(QAbstractItemView.SingleSelection)
        projects_layout.addWidget(self.project_list)

        buttons = QHBoxLayout()
        for label, slot in (
            ("Mo", self.on_open_project),
            ("Moi", self.on_new_project),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons_widget = QWidget()
        buttons_widget.setLayout(buttons)
        projects_layout.addWidget(buttons_widget)
        layout.addWidget(projects_box)

        # Library
        library_box = QGroupBox("Thu vien anh")
        library_layout = QVBoxLayout(library_box)

        self.background_list = AssetList(MIME_BACKGROUND)
        self.background_list.itemDoubleClicked.connect(self._on_background_picked)
        library_layout.addWidget(QLabel("Anh nen (keo vao khung)"))
        library_layout.addWidget(self.background_list)

        self.character_list = AssetList(MIME_CHARACTER)
        self.character_list.itemDoubleClicked.connect(self._on_character_picked)
        library_layout.addWidget(QLabel("Nhan vat (keo vao khung)"))
        library_layout.addWidget(self.character_list)

        # The frame's storyteller: registry keys first, then any picture
        # dropped in `assets/narrators/`.  Double-click is not wired here --
        # a narrator is picked, not inserted into the cast -- so the list is
        # dragged onto the stage like the others.
        self.narrator_list = AssetList(MIME_NARRATOR)
        self.narrator_list.itemDoubleClicked.connect(self._on_narrator_picked)
        library_layout.addWidget(QLabel("Nguoi ke (keo vao khung)"))
        library_layout.addWidget(self.narrator_list)

        refresh = QPushButton("Lam moi thu vien")
        refresh.clicked.connect(self._refresh_library)
        library_layout.addWidget(refresh)
        layout.addWidget(library_box, 1)
        return panel

    def _build_centre(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # The stage gets every pixel left over: it is the thing being
        # arranged, so it must not share space with the lists below.
        self.canvas = StageCanvas(AssetLibrary(Path.cwd(), _repo_root()))
        layout.addWidget(self.canvas, 1)

        # The live preview: a Play button and a scrubber over the estimated
        # clock.  The scrubber is always usable -- it drives entrances,
        # walks, the punch-in and captions, not just Ken Burns, so a scene
        # that stands still still has something to scrub through.
        self.play_button = QPushButton("Chay")
        self.play_button.clicked.connect(self.toggle_play)
        layout.addWidget(self.play_button)

        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, 100)
        self.scrub.setEnabled(True)
        self.scrub.setToolTip(
            "Keo de dung o mot moc bat ky trong scene"
        )
        self.scrub.valueChanged.connect(self._on_scrub_changed)
        scrub = QHBoxLayout()
        scrub.addWidget(QLabel("Xem giua chuyen dong"))
        scrub.addWidget(self.scrub, 1)
        layout.addLayout(scrub)

        scene_buttons = QHBoxLayout()
        for label, slot in (
            ("+ Scene", self.on_add_scene),
            ("Xoa scene", self.on_delete_scene),
            ("Len", self.on_move_scene_up),
            ("Xuong", self.on_move_scene_down),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            scene_buttons.addWidget(button)
        scene_widget = QWidget()
        scene_widget.setLayout(scene_buttons)
        layout.addWidget(scene_widget)

        self.scene_list = QListWidget()
        self.scene_list.setMaximumHeight(170)
        layout.addWidget(self.scene_list)

        # The whole script at a glance: every scene's narration, captions,
        # style, punch-in, cast and frame in one table.  Read-only, so it can
        # never disagree with the stage.
        self.scene_panel = ScenePanel()
        layout.addWidget(self.scene_panel, 1)
        return panel

    def _build_right(self) -> QWidget:
        self.tabs = QTabWidget()

        self.inspector = Inspector(self.canvas)
        self.tabs.addTab(self.inspector, "Scene / Nhan vat")

        frame_panel = QWidget()
        frame_layout = QVBoxLayout(frame_panel)
        self.frame_panel = FramePanel()
        frame_layout.addWidget(self.frame_panel)
        self.tabs.addTab(frame_panel, "Khung hinh")

        self.overlay_panel = OverlayPanel(self.canvas)
        self.tabs.addTab(self.overlay_panel, "Chu de them")

        self.walk_panel = WalkPanel(self.canvas)
        self.tabs.addTab(self.walk_panel, "Di chuyen")

        self.impact_panel = ImpactPanel(self.canvas)
        self.tabs.addTab(self.impact_panel, "Punch-in")

        video_tab = QWidget()
        video_layout = QFormLayout(video_tab)
        self.title_field = QLineEdit()
        self.author_field = QLineEdit()
        self.resolution_box = QComboBox()
        for text in ("1920x1080", "1280x720", "1080x1920", "720x1280", "1080x1080"):
            self.resolution_box.addItem(text)
        self.fps_box = QComboBox()
        for value in (24, 25, 30, 60):
            self.fps_box.addItem(str(value))
        video_layout.addRow("Tieu de", self.title_field)
        video_layout.addRow("Tac gia", self.author_field)
        video_layout.addRow("Khung hinh", self.resolution_box)
        video_layout.addRow("Khung hinh/giay", self.fps_box)
        self.tabs.addTab(video_tab, "Video")

        self.tabs.addTab(self._build_log_tab(), "Nhat ky")
        return self.tabs

    def _build_log_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.log_view = QLabel("")
        self.log_view.setWordWrap(True)
        self.log_view.setAlignment(Qt.AlignTop)
        self.log_view.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.log_view.setStyleSheet(
            "font-family: Menlo, monospace; font-size: 11px;"
        )
        scroll = QWidget()
        scroll_layout = QVBoxLayout(scroll)
        scroll_layout.addWidget(self.log_view)
        layout.addWidget(scroll)
        return widget

    def _build_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)

        self.save_button = QPushButton("Luu script.json")
        self.save_button.clicked.connect(self.on_save)
        layout.addWidget(self.save_button)

        self.status = QLabel("")
        layout.addWidget(self.status, 1)

        self.render_button = QPushButton("Render video")
        self.render_button.clicked.connect(self.on_render)
        layout.addWidget(self.render_button)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        return bar

    def _wire(self) -> None:
        self.project_list.currentRowChanged.connect(self._on_project_selected)
        self.scene_list.currentRowChanged.connect(self._on_scene_selected)
        self.canvas.changed.connect(self._on_canvas_changed)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.overlay_changed.connect(self._on_overlay_selected)
        self.inspector.changed.connect(self._on_inspector_changed)
        self.frame_panel.changed.connect(self._on_frame_changed)
        self.overlay_panel.changed.connect(self._on_panel_changed)
        self.impact_panel.changed.connect(self._on_panel_changed)
        self.walk_panel.changed.connect(self._on_panel_changed)
        self.scene_panel.scene_picked.connect(self._on_scene_panel_picked)
        self.title_field.editingFinished.connect(self._on_metadata_changed)
        self.author_field.editingFinished.connect(self._on_metadata_changed)
        self.resolution_box.currentTextChanged.connect(self._on_metadata_changed)
        self.fps_box.currentTextChanged.connect(self._on_metadata_changed)

        save = QAction("Luu", self)
        save.setShortcut(QKeySequence.StandardKey.Save)
        save.triggered.connect(self.on_save)
        self.addAction(save)

        select_all = QAction("Chon het nhan vat", self)
        select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all.triggered.connect(self.on_select_all_characters)
        self.addAction(select_all)

    # -- projects ---------------------------------------------------------
    def _refresh_projects(self) -> None:
        """List every script.json under projects/."""
        self.project_list.clear()
        self.projects = []
        for path in sorted((_repo_root() / "projects").glob("*/script.json")):
            project = ProjectStore.open(path)
            self.projects.append(project)
            item = QListWidgetItem(f"{project.name}  ({len(project.scenes)} scene)")
            item.setData(Qt.UserRole, path)
            self.project_list.addItem(item)

    def open_project(self, path: Path) -> None:
        project = ProjectStore.open(Path(path))
        self.current_project = project
        self.library = AssetLibrary(project.path.parent, _repo_root())
        self.canvas.library = self.library
        self.canvas.set_project(project)

        # Keep the project list pointing at what is open.
        if not self.project_list.count():
            self._refresh_projects()
        for row in range(self.project_list.count()):
            item = self.project_list.item(row)
            if Path(item.data(Qt.UserRole)) == Path(path):
                self.project_list.blockSignals(True)
                self.project_list.setCurrentRow(row)
                self.project_list.blockSignals(False)
                break

        self.title_field.setText(project.title)
        self.author_field.setText(project.author)
        self.resolution_box.setCurrentText(
            f"{project.resolution[0]}x{project.resolution[1]}"
        )
        self.fps_box.setCurrentText(str(project.fps))

        self._refresh_library()
        self.frame_panel.reload(project)
        self._refresh_scenes()
        self._update_title()

    def on_open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Mo script.json", str(_repo_root() / "projects"), "JSON (*.json)"
        )
        if path:
            self.open_project(Path(path))

    def on_new_project(self) -> None:
        name, ok = QFileDialog.getSaveFileName(
            self, "Project moi", str(_repo_root() / "projects"), "script.json"
        )
        if not ok or not name:
            return
        path = Path(name)
        if path.suffix != ".json":
            path = path.with_suffix(".json")
        project = ProjectStore.blank(path)
        self.current_project = project
        self.library = AssetLibrary(path.parent, _repo_root())
        self.canvas.library = self.library
        self.canvas.set_project(project)
        self._refresh_projects()
        self.title_field.setText(project.title)
        self.author_field.setText(project.author)
        self._refresh_library()
        self.frame_panel.reload(project)
        self._refresh_scenes()
        self._update_title()

    def _on_project_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.project_list.item(row)
        self.open_project(Path(item.data(Qt.UserRole)))

    # -- library ----------------------------------------------------------
    def _refresh_library(self) -> None:
        if self.library is None:
            return
        self.library.invalidate()
        self.canvas.clear_cache()
        self.background_list.clear()
        for asset in self.library.backgrounds:
            item = QListWidgetItem(f"{asset.label}")
            item.setData(Qt.UserRole, asset.relative)
            self._attach_icon(item, asset)
            self.background_list.addItem(item)

        self.character_list.clear()
        for asset in self.library.characters:
            item = QListWidgetItem(f"{asset.label}")
            item.setData(Qt.UserRole, asset.relative)
            self._attach_icon(item, asset)
            self.character_list.addItem(item)

        self.narrator_list.clear()
        for narrator in self.library.narrators:
            item = QListWidgetItem(narrator.label)
            # The row carries the registry key when the registry describes
            # this storyteller, and the picture's own path when it is just a
            # file in `assets/narrators/`; the canvas decides which slot to
            # write from that payload.
            item.setData(Qt.UserRole, narrator.key if narrator.use else narrator.image_file)
            icon_path = self.library.resolve(narrator.image_file)
            if icon_path is not None:
                pixmap = QPixmap(str(icon_path))
                if not pixmap.isNull():
                    item.setIcon(
                        QIcon(
                            pixmap.scaled(
                                48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
                            )
                        )
                    )
            self.narrator_list.addItem(item)

        if self.library.is_empty():
            self.status.setText(
                "Chua co anh nao trong project. Bo anh vao "
                "images/backgrounds/ hoac assets/characters/ roi bam 'Lam moi'."
            )

    def _attach_icon(self, item: QListWidgetItem, asset: Asset) -> None:
        """A small thumbnail, so the author recognises the picture."""
        path = self.library.resolve(asset.relative) if self.library else None
        if path is None:
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        pixmap = pixmap.scaled(
            48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        item.setIcon(QIcon(pixmap))

    def _on_background_picked(self, item: QListWidgetItem) -> None:
        scene = self.canvas.scene
        if scene is None:
            return
        scene.image_file = item.data(Qt.UserRole)
        self.canvas._pixmaps.pop(scene.image_file, None)
        self._on_canvas_changed()

    def _on_character_picked(self, item: QListWidgetItem) -> None:
        scene = self.canvas.scene
        if scene is None:
            return
        scene.characters.append(
            Placement(image_file=item.data(Qt.UserRole), x=0.5, y=0.86, height=0.3)
        )
        self.canvas.select(len(scene.characters) - 1)
        self._on_canvas_changed()

    # -- scenes -----------------------------------------------------------
    def _refresh_scenes(self) -> None:
        # Keep the author where they were.  This runs after every edit, and
        # jumping back to scene 1 threw away the scene they were working on
        # every time they touched the frame.
        keep = self.scene_list.currentRow()
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        project = self.current_project
        if project is not None:
            for scene in project.scenes:
                framed = "co khung" if self._scene_framed(scene) else "khung thuong"
                preview = (scene.image_file or "chua chon nen").split("/")[-1]
                self.scene_list.addItem(
                    f"Scene {scene.id}  [{framed}]  {len(scene.characters)} nhan vat  {preview}"
                )
        self.scene_list.blockSignals(False)
        self.scene_panel.set_project(project)
        if project is not None and project.scenes:
            self.scene_list.setCurrentRow(min(max(keep, 0), len(project.scenes) - 1))

    def _scene_framed(self, scene: Scene) -> bool:
        if self.current_project is None:
            return False
        frame = self.current_project.story_frame
        return frame.enabled and scene.story_frame_enabled is not False

    def on_select_all_characters(self) -> None:
        """Select the whole cast so one drag moves everyone."""
        if self.canvas.scene is None:
            return
        self.canvas.select_all()
        self.inspector.reload()
        self._update_selection_hint()

    def _on_scene_selected(self, row: int) -> None:
        project = self.current_project
        # A new scene is a new clock: stop the old one before switching.
        self.pause_play()
        if project is None or row < 0 or row >= len(project.scenes):
            self.canvas.set_scene(None)
            self.inspector.reload()
            self.overlay_panel.reload()
            self.impact_panel.reload()
            self.scene_panel.select_scene(None)
            self.scrub.setEnabled(False)
            return
        self.canvas.set_scene(project.scenes[row])
        self.inspector.reload()
        self.overlay_panel.reload()
        self.impact_panel.reload()
        self.walk_panel.reload()
        self.scene_panel.select_scene(project.scenes[row])
        self._build_scrubber()

    def _build_scrubber(self) -> None:
        """
        Put the scrubber where the stage is, and keep it usable.

        It used to be gated on Ken Burns being on.  The clock now runs
        entrances, walks, the punch-in and captions too, so it works for
        every scene -- a still scene just has nothing moving.
        """
        self.scrub.setEnabled(self.canvas.scene is not None)
        self._sync_scrubber()

    def on_add_scene(self) -> None:
        project = self.current_project
        if project is None:
            return
        template = project.scenes[-1] if project.scenes else None
        scene = Scene(
            id=project.next_scene_id(),
            text="",
            image_file=template.image_file if template else "",
            story_frame_enabled=None,
        )
        project.scenes.append(scene)
        project.mark_dirty()
        self._refresh_scenes()
        self.scene_list.setCurrentRow(len(project.scenes) - 1)
        self._update_title()
        self.scene_panel.set_project_dirty(project)

    def can_delete_scene(self) -> bool:
        """
        Whether the current scene may go.

        A video needs at least one scene, so the last one is kept.  The
        rule is a method of its own so it can be tested without a modal
        dialog in the way.
        """
        return self.current_project is not None and len(self.current_project.scenes) > 1

    def on_delete_scene(self) -> None:
        project = self.current_project
        row = self.scene_list.currentRow()
        if project is None or row < 0:
            return
        if not self.can_delete_scene():
            QMessageBox.information(
                self, "Khong xoa", "Mot video can it nhat mot scene."
            )
            return
        del project.scenes[row]
        project.renumber()
        project.mark_dirty()
        self._refresh_scenes()
        self.scene_list.setCurrentRow(min(row, len(project.scenes) - 1))
        self._update_title()
        self.scene_panel.set_project_dirty(project)

    def on_move_scene_up(self) -> None:
        self._reorder(-1)

    def on_move_scene_down(self) -> None:
        self._reorder(1)

    def _reorder(self, delta: int) -> None:
        project = self.current_project
        row = self.scene_list.currentRow()
        if project is None or row < 0:
            return
        target = row + delta
        if not (0 <= target < len(project.scenes)):
            return
        project.scenes[row], project.scenes[target] = (
            project.scenes[target],
            project.scenes[row],
        )
        project.renumber()
        project.mark_dirty()
        self._refresh_scenes()
        self.scene_list.setCurrentRow(target)
        self._update_title()
        self.scene_panel.set_project_dirty(project)

    # -- reactions --------------------------------------------------------
    def _on_canvas_changed(self) -> None:
        """A drag on the stage: refresh the numbers, not the whole form."""
        self.inspector.reload()
        self._update_title()
        self._refresh_scene_row()
        self.scene_panel.set_project_dirty(self.current_project)

    def _refresh_scene_row(self) -> None:
        scene = self.canvas.scene
        row = self.scene_list.currentRow()
        if scene is None or row < 0:
            return
        framed = "co khung" if self._scene_framed(scene) else "khung thuong"
        preview = (scene.image_file or "chua chon nen").split("/")[-1]
        self.scene_list.item(row).setText(
            f"Scene {scene.id}  [{framed}]  {len(scene.characters)} nhan vat  {preview}"
        )

    def _on_selection_changed(self, index: int) -> None:
        self.inspector.reload()
        self._update_selection_hint()

    def _on_overlay_selected(self, index: int) -> None:
        self.overlay_panel.reload()

    def _on_panel_changed(self) -> None:
        self._update_title()
        self._refresh_scene_row()
        self.scene_panel.set_project_dirty(self.current_project)

    def _update_selection_hint(self) -> None:
        """Tell the author how many are selected and how to move them."""
        count = len(self.canvas.also_selected) + (1 if self.canvas.selected is not None else 0)
        if count > 1:
            self.status.setText(
                f"Dang chon {count} nhan vat - keo de chuyen ca nhom, "
                "keo goc duoi de doi cao, Shift+click de them/bot"
            )

    def _on_inspector_changed(self) -> None:
        self._update_title()
        self._refresh_scene_row()

    def _on_frame_changed(self) -> None:
        self.canvas.update()
        self._refresh_scenes()
        self._update_title()
        self.scene_panel.set_project_dirty(self.current_project)

    def _on_metadata_changed(self) -> None:
        project = self.current_project
        if project is None:
            return
        text = self.resolution_box.currentText()
        if "x" in text:
            left, _, right = text.partition("x")
            try:
                project.resolution = (int(left), int(right))
            except ValueError:
                pass
        try:
            project.fps = int(self.fps_box.currentText())
        except ValueError:
            pass
        project.title = self.title_field.text()
        project.author = self.author_field.text()
        project.mark_dirty()
        self.canvas.update()
        self._update_title()

    def _update_title(self) -> None:
        project = self.current_project
        if project is None:
            self.setWindowTitle("autovid studio")
            self.status.setText("")
            return
        mark = " *" if project.dirty else ""
        self.setWindowTitle(f"autovid studio - {project.name}{mark}")
        count = len(project.scenes)
        self.status.setText(
            f"{project.name}: {count} scene | luu de ghi vao {project.path.name}"
        )

    # -- saving and rendering ---------------------------------------------
    def on_save(self) -> None:
        project = self.current_project
        if project is None:
            return
        # Pull the fields still being typed before serialising.
        self._on_metadata_changed()
        try:
            path = ProjectStore.save(project)
        except OSError as error:
            QMessageBox.critical(self, "Khong luu duoc", str(error))
            return
        self.log_view.setText(f"Da luu {path}")
        self._update_title()
        self.status.setText(f"Da luu {path}")

    def on_render(self) -> None:
        project = self.current_project
        if project is None:
            return
        self.on_save()
        self.progress.setVisible(True)
        self.render_button.setEnabled(False)
        self.log_view.setText("Dang render...")
        self._render_thread = RenderThread(project.path, "assembly", self)
        self._render_thread.output.connect(self.log_view.setText)
        self._render_thread.finished_ok.connect(self._on_render_done)
        self._render_thread.start()

    def _on_render_done(self, code: int) -> None:
        self.progress.setVisible(False)
        self.render_button.setEnabled(True)
        if code == 0 and self._render_thread is not None:
            output = self._render_thread.script_path.parent / "output"
            self.log_view.setText(
                f"Xong. Video o {output}\n{self._render_thread.log()[-2000:]}"
            )
            self.status.setText(f"Render xong - xem {output}")
        else:
            self.log_view.setText(
                f"Render that bai (ma loi {code})\n"
                + (self._render_thread.log()[-3000:] if self._render_thread else "")
            )

    def _on_scene_panel_picked(self, scene: Scene | None) -> None:
        """
        The author clicked a row of the summary table: open that scene.

        The selection is moved through the scene list rather than set on the
        canvas, so there is one path into a scene and the table, the list and
        the stage cannot end up disagreeing about what is open.
        """
        if scene is None or self.current_project is None:
            return
        row = self.scene_panel.scene_row(scene)
        if row < 0 or row >= self.scene_list.count():
            return
        if self.scene_list.currentRow() == row:
            return
        self.scene_list.setCurrentRow(row)

    def _on_narrator_picked(self, item: QListWidgetItem) -> None:
        """
        The storyteller row the author picked.

        The canvas owns which slot gets written -- `use` for a registry key,
        `image_file` for a bare picture -- so the window only reports back
        what the canvas decided, and says where it came from.
        """
        payload = item.data(Qt.UserRole)
        name = self.canvas.choose_narrator_payload(payload)
        source = "assets/narrators/" if "/" in str(payload) else "registry"
        self.status.setText(f"Nguoi ke: {name} ({source})")
        # The canvas switched the frame on; the panel's checkbox has to
        # follow, or it would still read "off" while a host is standing there.
        self.frame_panel.reload(self.current_project)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # A timer outliving the window would keep the clock running.
        if self._play_timer.isActive():
            self._play_timer.stop()
        if self.current_project is not None and self.current_project.dirty:
            answer = QMessageBox.question(
                self,
                "Chua luu",
                f"{self.current_project.name} co thay doi chua luu. Luu lai?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Save:
                self.on_save()
            elif answer == QMessageBox.Cancel:
                event.ignore()
                return
        event.accept()