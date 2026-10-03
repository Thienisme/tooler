"""
The inspector: every field of a scene and a character, as plain widgets.

Written next to the canvas rather than as a property dialog so that a change
is visible on the stage the instant it is made.  Each control writes into
the store object immediately and asks the canvas to repaint; nothing is
buffered until "apply", because a form that hides a pending edit is worse
than no form at all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .canvas import (
    ENTER_CHOICES,
    EXIT_CHOICES,
    FRAME_STYLE_CHOICES,
    IDLE_CHOICES,
    MOTION_CHOICES,
    TRANSITION_CHOICES,
    StageCanvas,
)
from .store import Placement, Project, Scene, StoryFrame


def _spin(minimum: float, maximum: float, step: float, decimals: int = 2):
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    return box


class Inspector(QScrollArea):
    """
    The right-hand panel: the scene's look, then the selected character's.

    `changed` is forwarded from the canvas, so a drag on the stage refreshes
    the numbers here and an edit here refreshes the stage -- one direction
    of truth, two views.
    """

    changed = Signal()

    def __init__(self, canvas: StageCanvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.setWidgetResizable(True)
        self.setMinimumWidth(300)

        body = QWidget()
        self._layout = QVBoxLayout(body)
        self._layout.setAlignment(Qt.AlignTop)
        self.setWidget(body)

        self.scene_group = QGroupBox("Scene")
        self._scene_form = QFormLayout(self.scene_group)

        # Background
        self.background_label = QLabel("-")
        self.background_label.setWordWrap(True)
        self.background_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._scene_form.addRow("Anh nen", self.background_label)

        # Ken Burns
        self.motion_enabled = QCheckBox("Bat chuyen dong cham")
        self.motion_enabled.toggled.connect(self._on_motion_enabled)
        self._scene_form.addRow(self.motion_enabled)

        self.motion_type = QComboBox()
        self.motion_type.addItems(MOTION_CHOICES)
        self.motion_type.currentTextChanged.connect(self._on_motion_type)
        self._scene_form.addRow("Kieu", self.motion_type)

        scales = QHBoxLayout()
        self.motion_start = _spin(0.5, 3.0, 0.01)
        self.motion_start.valueChanged.connect(self._on_motion_scale)
        self.motion_end = _spin(0.5, 3.0, 0.01)
        self.motion_end.valueChanged.connect(self._on_motion_scale)
        scales.addWidget(self.motion_start)
        scales.addWidget(QLabel("->"))
        scales.addWidget(self.motion_end)
        scales_widget = QWidget()
        scales_widget.setLayout(scales)
        self._scene_form.addRow("Thu phong", scales_widget)

        # Transition
        self.transition_type = QComboBox()
        self.transition_type.addItems(TRANSITION_CHOICES)
        self.transition_type.currentTextChanged.connect(self._on_transition)
        self._scene_form.addRow("Chuyen canh", self.transition_type)

        self.transition_duration = _spin(0.0, 5.0, 0.05)
        self.transition_duration.valueChanged.connect(self._on_transition_duration)
        self._scene_form.addRow("Thoi luong (s)", self.transition_duration)

        # Scene-level frame opt-out
        self.frame_override = QComboBox()
        self.frame_override.addItem("Theo mac dinh", None)
        self.frame_override.addItem("Co khung", True)
        self.frame_override.addItem("Khong khung", False)
        self.frame_override.currentIndexChanged.connect(self._on_frame_override)
        self._scene_form.addRow("Khung hinh", self.frame_override)

        self._layout.addWidget(self.scene_group)

        # -- character ---------------------------------------------------
        self.character_group = QGroupBox("Nhan vat")
        self._character_form = QFormLayout(self.character_group)

        self.character_label = QLabel("-")
        self.character_label.setWordWrap(True)
        self._character_form.addRow("Anh", self.character_label)

        position = QHBoxLayout()
        self.position_x = _spin(0.0, 1.0, 0.005, 3)
        self.position_x.valueChanged.connect(self._on_position)
        self.position_y = _spin(0.0, 1.0, 0.005, 3)
        self.position_y.valueChanged.connect(self._on_position)
        position.addWidget(QLabel("x"))
        position.addWidget(self.position_x)
        position.addWidget(QLabel("y"))
        position.addWidget(self.position_y)
        position_widget = QWidget()
        position_widget.setLayout(position)
        self._character_form.addRow("Vi tri", position_widget)

        self.size = _spin(0.04, 1.5, 0.01)
        self.size.valueChanged.connect(self._on_size)
        self._character_form.addRow("Chieu cao", self.size)

        self.flip = QCheckBox("Quay nguoc")
        self.flip.toggled.connect(self._on_flip)
        self._character_form.addRow(self.flip)

        self.enter_type = QComboBox()
        self.enter_type.addItems(ENTER_CHOICES)
        self.enter_type.currentTextChanged.connect(self._on_enter)
        self._character_form.addRow("Dien vao", self.enter_type)

        self.enter_duration = QSpinBox()
        self.enter_duration.setRange(0, 5000)
        self.enter_duration.setSingleStep(50)
        self.enter_duration.setSuffix(" ms")
        self.enter_duration.valueChanged.connect(self._on_enter_duration)
        self._character_form.addRow("Thoi luong", self.enter_duration)

        self.enter_from = QComboBox()
        self.enter_from.addItems(
            ["left", "right", "top", "bottom", "center"]
        )
        self.enter_from.currentTextChanged.connect(self._on_enter_from)
        self._character_form.addRow("Huong", self.enter_from)

        self.idle_type = QComboBox()
        self.idle_type.addItems(IDLE_CHOICES)
        self.idle_type.currentTextChanged.connect(self._on_idle)
        self._character_form.addRow("Dung im", self.idle_type)

        self.idle_amplitude = QSpinBox()
        self.idle_amplitude.setRange(0, 120)
        self.idle_amplitude.setSuffix(" px")
        self.idle_amplitude.valueChanged.connect(self._on_idle_amplitude)
        self._character_form.addRow("Bien do", self.idle_amplitude)

        self.idle_period = _spin(0.1, 10.0, 0.1)
        self.idle_period.valueChanged.connect(self._on_idle_period)
        self._character_form.addRow("Chu ky (s)", self.idle_period)

        self.exit_type = QComboBox()
        self.exit_type.addItems(EXIT_CHOICES)
        self.exit_type.currentTextChanged.connect(self._on_exit)
        self._character_form.addRow("Ra khoi", self.exit_type)

        self.exit_duration = QSpinBox()
        self.exit_duration.setRange(0, 5000)
        self.exit_duration.setSingleStep(50)
        self.exit_duration.setSuffix(" ms")
        self.exit_duration.valueChanged.connect(self._on_exit_duration)
        self._character_form.addRow("Thoi luong", self.exit_duration)

        self.exit_to = QComboBox()
        self.exit_to.addItems(["left", "right", "top", "bottom", "center"])
        self.exit_to.currentTextChanged.connect(self._on_exit_to)
        self._character_form.addRow("Huong", self.exit_to)

        # -- timing ------------------------------------------------------
        timing = QHBoxLayout()
        self.at_sentence = QSpinBox()
        self.at_sentence.setRange(0, 999)
        self.at_sentence.setSpecialValueText("ngay")
        self.at_sentence.valueChanged.connect(self._on_timing)
        self.for_sentences = QSpinBox()
        self.for_sentences.setRange(0, 999)
        self.for_sentences.setSpecialValueText("het")
        self.for_sentences.valueChanged.connect(self._on_timing)
        timing.addWidget(QLabel("Tu cau"))
        timing.addWidget(self.at_sentence)
        timing.addWidget(QLabel("So cau"))
        timing.addWidget(self.for_sentences)
        timing_widget = QWidget()
        timing_widget.setLayout(timing)
        self._character_form.addRow("Thoi diem", timing_widget)

        remove = QPushButton("Go nhan vat khoi scene")
        remove.clicked.connect(self._on_remove)
        self._character_form.addRow(remove)

        self._layout.addWidget(self.character_group)
        self._layout.addStretch(1)

        self._suppress = False

    # -- populate ---------------------------------------------------------
    def reload(self) -> None:
        """Repopulate every control from the store, without echoing back."""
        self._suppress = True
        scene = self.canvas.scene
        placement = self.canvas.current()

        has_scene = scene is not None
        self.scene_group.setEnabled(has_scene)
        if scene is not None:
            self.background_label.setText(
                scene.image_file or "chua chon"
            )
            self.motion_enabled.setChecked(scene.motion.enabled)
            self.motion_type.setCurrentText(scene.motion.type)
            self.motion_start.setValue(scene.motion.start_scale)
            self.motion_end.setValue(scene.motion.end_scale)
            self.transition_type.setCurrentText(scene.transition.type)
            self.transition_duration.setValue(scene.transition.duration)
            index = self.frame_override.findData(scene.story_frame_enabled)
            self.frame_override.setCurrentIndex(index if index >= 0 else 0)
            self._update_motion_enabled(scene.motion)

        has_character = placement is not None
        self.character_group.setEnabled(has_character)
        self.character_group.setVisible(has_scene)
        if placement is not None:
            self.character_label.setText(placement.image_file)
            self.position_x.setValue(placement.x)
            self.position_y.setValue(placement.y)
            self.size.setValue(placement.height)
            self.flip.setChecked(placement.flip)
            self.enter_type.setCurrentText(placement.enter_type)
            self.enter_duration.setValue(placement.enter_duration_ms)
            self.enter_from.setCurrentText(placement.enter_from)
            self.idle_type.setCurrentText(placement.idle_type)
            self.idle_amplitude.setValue(placement.idle_amplitude_px)
            self.idle_period.setValue(placement.idle_period_s)
            self.exit_type.setCurrentText(placement.exit_type)
            self.exit_duration.setValue(placement.exit_duration_ms)
            self.exit_to.setCurrentText(placement.exit_to)
            self.at_sentence.setValue(placement.at_sentence or 0)
            self.for_sentences.setValue(placement.for_sentences or 0)
            self._update_enter_fields(placement.enter_type)
            self._update_exit_fields(placement.exit_type)

        self._suppress = False

    def _update_motion_enabled(self, motion) -> None:
        active = motion.enabled and motion.type != "none"
        self.motion_type.setEnabled(active)
        self.motion_start.setEnabled(active)
        self.motion_end.setEnabled(active)

    def _update_enter_fields(self, kind: str) -> None:
        directional = kind in ("slide_in", "fly_in")
        self.enter_from.setEnabled(directional)
        self.enter_duration.setEnabled(kind != "none")

    def _update_exit_fields(self, kind: str) -> None:
        self.exit_to.setEnabled(kind in ("slide_out", "fly_out"))
        self.exit_duration.setEnabled(kind != "none")

    # -- handlers ---------------------------------------------------------
    def _dirty(self) -> None:
        if self._suppress:
            return
        if self.canvas.project is not None:
            self.canvas.project.mark_dirty()
        self.canvas.update()
        self.changed.emit()

    def _on_motion_enabled(self, checked: bool) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.motion.enabled = checked
        self._update_motion_enabled(scene.motion)
        self._dirty()

    def _on_motion_type(self, kind: str) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.motion.type = kind
        if kind == "none":
            scene.motion.enabled = False
        else:
            scene.motion.enabled = True
        self._update_motion_enabled(scene.motion)
        self._dirty()

    def _on_motion_scale(self) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.motion.start_scale = self.motion_start.value()
        scene.motion.end_scale = self.motion_end.value()
        self._dirty()

    def _on_transition(self, kind: str) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.transition.type = kind
        self._dirty()

    def _on_transition_duration(self, value: float) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.transition.duration = value
        self._dirty()

    def _on_frame_override(self, index: int) -> None:
        scene = self.canvas.scene
        if scene is None or self._suppress:
            return
        scene.story_frame_enabled = self.frame_override.itemData(index)
        self._dirty()

    def _on_position(self) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.x = self.position_x.value()
        placement.y = self.position_y.value()
        self._dirty()

    def _on_size(self, value: float) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.height = value
        self._dirty()

    def _on_flip(self, checked: bool) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.flip = checked
        self._dirty()

    def _on_enter(self, kind: str) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.enter_type = kind
        self._update_enter_fields(kind)
        self._dirty()

    def _on_enter_duration(self, value: int) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.enter_duration_ms = value
        self._dirty()

    def _on_enter_from(self, value: str) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.enter_from = value
        self._dirty()

    def _on_idle(self, kind: str) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.idle_type = kind
        self._dirty()

    def _on_idle_amplitude(self, value: int) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.idle_amplitude_px = value
        self._dirty()

    def _on_idle_period(self, value: float) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.idle_period_s = value
        self._dirty()

    def _on_exit(self, kind: str) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.exit_type = kind
        self._update_exit_fields(kind)
        self._dirty()

    def _on_exit_duration(self, value: int) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.exit_duration_ms = value
        self._dirty()

    def _on_exit_to(self, value: str) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.exit_to = value
        self._dirty()

    def _on_timing(self) -> None:
        placement = self.canvas.current()
        if placement is None or self._suppress:
            return
        placement.at_sentence = self.at_sentence.value() or None
        placement.for_sentences = self.for_sentences.value() or None
        self._dirty()

    def _on_remove(self) -> None:
        scene = self.canvas.scene
        index = self.canvas.selected
        if scene is None or index is None or not (0 <= index < len(scene.characters)):
            return
        del scene.characters[index]
        self.canvas.select(None)
        self._dirty()


class FramePanel(QWidget):
    """The project-wide storytelling frame, shared by every scene."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self._suppress = False

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.enabled = QCheckBox("Dung khung cho video")
        self.enabled.toggled.connect(self._on_enabled)
        form.addRow(self.enabled)

        self.style = QComboBox()
        self.style.addItems(FRAME_STYLE_CHOICES)
        self.style.currentTextChanged.connect(self._on_style)
        form.addRow("Kieu khung", self.style)

        geometry = QFormLayout()
        for name, low, high in (
            ("x", 0.0, 1.0),
            ("y", 0.0, 1.0),
            ("width", 0.1, 1.0),
            ("height", 0.1, 1.0),
        ):
            box = _spin(low, high, 0.005, 3)
            box.valueChanged.connect(self._on_geometry)
            setattr(self, f"frame_{name}", box)
            geometry.addRow(name, box)
        geometry_widget = QWidget()
        geometry_widget.setLayout(geometry)
        form.addRow("Khung hinh", geometry_widget)

        self.show_narrator = QCheckBox("Hien nguoi dan")
        self.show_narrator.toggled.connect(self._on_narrator)
        form.addRow(self.show_narrator)

        self.host_x = _spin(0.0, 1.0, 0.005, 3)
        self.host_x.valueChanged.connect(self._on_host)
        form.addRow("Nguoi dan x", self.host_x)

        self.host_height = _spin(0.05, 1.0, 0.01)
        self.host_height.valueChanged.connect(self._on_host)
        form.addRow("Nguoi dan cao", self.host_height)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #e0a34a;")
        form.addRow(self.warning)

        layout.addLayout(form)

    def reload(self, project: Project | None) -> None:
        self.project = project
        self._suppress = True
        enabled = project is not None and project.story_frame.enabled
        self.enabled.setChecked(enabled)
        if project is not None:
            frame = project.story_frame
            self.style.setCurrentText(frame.style)
            self.frame_x.setValue(frame.x)
            self.frame_y.setValue(frame.y)
            self.frame_width.setValue(frame.width)
            self.frame_height.setValue(frame.height)
            self.show_narrator.setChecked(frame.show_narrator)
            self.host_x.setValue(frame.host_x)
            self.host_height.setValue(frame.host_height)
        self._update_enabled()
        self._suppress = False

    def _update_enabled(self) -> None:
        active = self.enabled.isChecked()
        for widget in (
            self.style,
            self.frame_x,
            self.frame_y,
            self.frame_width,
            self.frame_height,
            self.show_narrator,
            self.host_x,
            self.host_height,
        ):
            widget.setEnabled(active)
        if not active:
            self.warning.setText("")
        elif self.project is not None and not self._narrator_fits():
            self.warning.setText(
                "Khung che het choang ben phai: thu nho width hoac giam host_height "
                "de nguoi dan khong bi che."
            )
        else:
            self.warning.setText("")

    def _narrator_fits(self) -> bool:
        """
        Whether the narrator still has a clear strip beside the panel.

        The host is wider than it is tall by its sprite's aspect ratio,
        which the panel does not know; 0.48 is a fair stand-in for the
        drawn hosts in this repo, and the warning only has to catch the
        gross case of a panel that reaches the frame edge.
        """
        if self.project is None:
            return True
        frame = self.project.story_frame
        host_width = frame.host_height * 0.48
        left = frame.host_x - host_width / 2
        return left >= frame.right_edge and frame.host_x + host_width / 2 <= 1.0

    def _dirty(self) -> None:
        if self._suppress or self.project is None:
            return
        self.project.mark_dirty()
        self.changed.emit()

    def _on_enabled(self, checked: bool) -> None:
        if self.project is None or self._suppress:
            return
        self.project.story_frame.enabled = checked
        self._update_enabled()
        self._dirty()

    def _on_style(self, style: str) -> None:
        if self.project is None or self._suppress:
            return
        self.project.story_frame.style = style
        self._update_enabled()
        self._dirty()

    def _on_geometry(self) -> None:
        if self.project is None or self._suppress:
            return
        frame = self.project.story_frame
        frame.x = self.frame_x.value()
        frame.y = self.frame_y.value()
        frame.width = self.frame_width.value()
        frame.height = self.frame_height.value()
        self._update_enabled()
        self._dirty()

    def _on_narrator(self, checked: bool) -> None:
        if self.project is None or self._suppress:
            return
        self.project.story_frame.show_narrator = checked
        self._update_enabled()
        self._dirty()

    def _on_host(self) -> None:
        if self.project is None or self._suppress:
            return
        frame = self.project.story_frame
        frame.host_x = self.host_x.value()
        frame.host_height = self.host_height.value()
        self._update_enabled()
        self._dirty()