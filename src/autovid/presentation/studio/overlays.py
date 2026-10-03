"""
Panels for the two scene-wide effects the studio draws on the stage:
the burned-in text overlays, and the scene's impact punch.

Both are separate from the scene/character inspector because they belong to
the scene rather than to any one character, and because a scene may carry
several overlays -- each with its own window, placement and animation -- so
a single form would have to be either ambiguous or a list of identical rows.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from autovid.domain.script import (
    MAX_IMPACT_INTENSITY,
    MAX_IMPACT_SHAKE_PX,
    TEXT_ANIMATIONS,
    TEXT_POSITIONS,
)

from .canvas import StageCanvas
from .store import TextOverlay


class OverlayPanel(QWidget):
    """The scene's captions: a list on the left, the selected one's fields."""

    changed = Signal()

    def __init__(self, canvas: StageCanvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self._suppress = False

        root = QVBoxLayout(self)

        # -- the list ---------------------------------------------------
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        root.addWidget(QLabel("Chu de them tren hinh"))
        root.addWidget(self.list)

        buttons = QHBoxLayout()
        add = QPushButton("+ Chu")
        add.clicked.connect(self.on_add)
        remove = QPushButton("- Chu")
        remove.clicked.connect(self.on_remove)
        up = QPushButton("Len")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Xuong")
        down.clicked.connect(lambda: self._move(1))
        for button in (add, remove, up, down):
            buttons.addWidget(button)
        holder = QWidget()
        holder.setLayout(buttons)
        root.addWidget(holder)

        # -- the fields -------------------------------------------------
        self.group = QGroupBox("Chu dang chon")
        form = QFormLayout(self.group)

        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Vi du: Bai 3 - Cuoc gap o cong lang")
        self.text_edit.editingFinished.connect(self._on_text)
        form.addRow("Noi dung", self.text_edit)

        self.position = QComboBox()
        self.position.addItems(TEXT_POSITIONS)
        self.position.currentTextChanged.connect(self._on_position)
        form.addRow("Vi tri", self.position)

        self.animation = QComboBox()
        self.animation.addItems(TEXT_ANIMATIONS)
        self.animation.currentTextChanged.connect(self._on_animation)
        form.addRow("Hien thi", self.animation)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 400)
        self.font_size.setSuffix(" px")
        self.font_size.valueChanged.connect(self._on_font_size)
        form.addRow("Co chu", self.font_size)

        colors = QHBoxLayout()
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._on_pick_color)
        colors.addWidget(self.color_button)
        self.stroke_button = QPushButton()
        self.stroke_button.clicked.connect(self._on_pick_stroke)
        colors.addWidget(self.stroke_button)
        self.stroke_width = QSpinBox()
        self.stroke_width.setRange(0, 30)
        self.stroke_width.valueChanged.connect(self._on_stroke_width)
        colors.addWidget(QLabel(" Vien"))
        colors.addWidget(self.stroke_width)
        colors_widget = QWidget()
        colors_widget.setLayout(colors)
        form.addRow("Mau / vien", colors_widget)

        timing = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, 600000)
        self.start.setSingleStep(100)
        self.start.setSuffix(" ms")
        self.start.valueChanged.connect(self._on_timing)
        self.end = QSpinBox()
        self.end.setRange(0, 600000)
        self.end.setSingleStep(100)
        self.end.setSuffix(" ms")
        self.end.valueChanged.connect(self._on_timing)
        timing.addWidget(QLabel("Tu"))
        timing.addWidget(self.start)
        timing.addWidget(QLabel("Den"))
        timing.addWidget(self.end)
        timing_widget = QWidget()
        timing_widget.setLayout(timing)
        form.addRow("Thoi gian", timing_widget)

        root.addWidget(self.group)

        hint = QLabel(
            "Chu duoc cat vao hinh cua scene nay. Thoi gian tinh tu dau scene; "
            "toi da 4000ms neu ban chua dat."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7a8296;")
        root.addWidget(hint)
        root.addStretch(1)

        self._colour = "#FFFFFF"
        self._stroke = "#000000"

    # -- populate ---------------------------------------------------------
    def reload(self) -> None:
        self._suppress = True
        scene = self.canvas.scene
        self.list.clear()
        if scene is not None:
            for overlay in scene.overlays:
                label = overlay.text or "(chua chong)"
                self.list.addItem(label)
            index = self.canvas.selected_overlay
            if index is not None and 0 <= index < self.list.count():
                self.list.setCurrentRow(index)

        overlay = self.canvas.selected_overlay_item()
        self.group.setEnabled(overlay is not None)
        if overlay is not None:
            self.text_edit.setText(overlay.text)
            self.position.setCurrentText(overlay.position)
            self.animation.setCurrentText(overlay.animation)
            self.font_size.setValue(overlay.font_size)
            self._colour = overlay.color
            self._stroke = overlay.stroke_color
            self._paint_colour_buttons()
            self.stroke_width.setValue(overlay.stroke_width)
            self.start.setValue(overlay.start_offset_ms)
            self.end.setValue(overlay.end_offset_ms)
        self._suppress = False

    def _paint_colour_buttons(self) -> None:
        self.color_button.setStyleSheet(
            f"background: {self._colour}; color: #ffffff;"
        )
        self.stroke_button.setStyleSheet(
            f"background: {self._stroke}; color: #ffffff;"
        )

    def _dirty(self) -> None:
        if self._suppress or self.canvas.project is None:
            return
        self.canvas.project.mark_dirty()
        self.canvas.update()
        self.changed.emit()

    def refresh_list(self) -> None:
        row = self.canvas.selected_overlay
        self.reload()
        if row is not None and 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    # -- list actions -----------------------------------------------------
    def on_add(self) -> None:
        scene = self.canvas.scene
        if scene is None:
            return
        scene.overlays.append(TextOverlay(text="Chu moi"))
        self.canvas.select_overlay(len(scene.overlays) - 1)
        self.refresh_list()
        self.list.setCurrentRow(len(scene.overlays) - 1)
        self._dirty()

    def on_remove(self) -> None:
        scene = self.canvas.scene
        index = self.canvas.selected_overlay
        if scene is None or index is None:
            return
        if not (0 <= index < len(scene.overlays)):
            return
        del scene.overlays[index]
        self.canvas.select_overlay(
            min(index, len(scene.overlays) - 1) if scene.overlays else None
        )
        self.refresh_list()
        self._dirty()

    def _move(self, delta: int) -> None:
        scene = self.canvas.scene
        index = self.canvas.selected_overlay
        if scene is None or index is None:
            return
        target = index + delta
        if not (0 <= target < len(scene.overlays)):
            return
        scene.overlays[index], scene.overlays[target] = (
            scene.overlays[target],
            scene.overlays[index],
        )
        self.canvas.select_overlay(target)
        self.refresh_list()
        self.list.setCurrentRow(target)
        self._dirty()

    def _on_row(self, row: int) -> None:
        if self._suppress:
            return
        self.canvas.select_overlay(row if row >= 0 else None)
        self.reload()

    # -- field handlers ---------------------------------------------------
    def _current(self) -> TextOverlay | None:
        return self.canvas.selected_overlay_item()

    def _on_text(self) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        overlay.text = self.text_edit.text()
        row = self.canvas.selected_overlay or 0
        if 0 <= row < self.list.count():
            self.list.item(row).setText(overlay.text or "(chua chong)")
        self._dirty()

    def _on_position(self, value: str) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        overlay.position = value
        self._dirty()

    def _on_animation(self, value: str) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        overlay.animation = value
        self._dirty()

    def _on_font_size(self, value: int) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        overlay.font_size = value
        self._dirty()

    def _on_stroke_width(self, value: int) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        overlay.stroke_width = value
        self._dirty()

    def _on_timing(self) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        start, end = self.start.value(), self.end.value()
        if end <= start:
            # The schema rejects end <= start; rather than writing a file
            # that will not validate, push the end out to a usable window.
            end = start + 500
            self.end.setValue(end)
        overlay.start_offset_ms = start
        overlay.end_offset_ms = end
        self._dirty()

    def _on_pick_color(self) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        chosen = QColorDialog.getColor(
            Qt.white, self, "Chon mau chu", QColorDialog.ShowAlphaChannel
        )
        if chosen.isValid():
            self._colour = chosen.name()
            overlay.color = self._colour
            self._paint_colour_buttons()
            self._dirty()

    def _on_pick_stroke(self) -> None:
        overlay = self._current()
        if overlay is None or self._suppress:
            return
        chosen = QColorDialog.getColor(
            Qt.black, self, "Chon mau vien", QColorDialog.ShowAlphaChannel
        )
        if chosen.isValid():
            self._stroke = chosen.name()
            overlay.stroke_color = self._stroke
            self._paint_colour_buttons()
            self._dirty()


class ImpactPanel(QWidget):
    """The scene's punch-in: a quick zoom spike with shake and flash."""

    changed = Signal()

    def __init__(self, canvas: StageCanvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self._suppress = False

        layout = QFormLayout(self)

        self.enabled = QCheckBox("Bat cu punch-in")
        self.enabled.toggled.connect(self._on_enabled)
        layout.addRow(self.enabled)

        self.anchor = QComboBox()
        self.anchor.addItem("Theo cau", "sentence")
        self.anchor.addItem("Theo thoi gian", "offset")
        self.anchor.currentIndexChanged.connect(self._on_anchor)
        layout.addRow("Neo theo", self.anchor)

        self.at_sentence = QSpinBox()
        self.at_sentence.setRange(1, 999)
        self.at_sentence.valueChanged.connect(self._on_sentence)
        layout.addRow("Cau thu", self.at_sentence)

        self.at_offset = QSpinBox()
        self.at_offset.setRange(0, 600000)
        self.at_offset.setSingleStep(100)
        self.at_offset.setSuffix(" ms")
        self.at_offset.valueChanged.connect(self._on_offset)
        layout.addRow("Sau dau", self.at_offset)

        self.intensity = QDoubleSpinBox()
        self.intensity.setRange(0.0, MAX_IMPACT_INTENSITY)
        self.intensity.setSingleStep(0.01)
        self.intensity.setDecimals(3)
        self.intensity.valueChanged.connect(self._on_intensity)
        layout.addRow("Cuong do zoom", self.intensity)

        self.shake = QSpinBox()
        self.shake.setRange(0, MAX_IMPACT_SHAKE_PX)
        self.shake.setSuffix(" px")
        self.shake.valueChanged.connect(self._on_shake)
        layout.addRow("Rung manh hinh", self.shake)

        self.duration = QSpinBox()
        self.duration.setRange(50, 5000)
        self.duration.setSingleStep(50)
        self.duration.setSuffix(" ms")
        self.duration.valueChanged.connect(self._on_duration)
        layout.addRow("Thoi luong", self.duration)

        self.flash = QComboBox()
        self.flash.addItems(["none", "white", "black"])
        self.flash.currentTextChanged.connect(self._on_flash)
        layout.addRow("Flash", self.flash)

        hint = QLabel(
            "Zoom tăng nhanh rồi từ từ về -- dung kéo thanh 'Xem giua chuyen dong' "
            "ben duoi khung hinh de xem diem dung cua no."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7a8296;")
        layout.addRow(hint)

    def reload(self) -> None:
        self._suppress = True
        scene = self.canvas.scene
        impact = scene.impact if scene is not None else None
        enabled = impact is not None and impact.enabled
        self.enabled.setChecked(enabled)
        if impact is not None:
            by_sentence = impact.at_sentence is not None
            self.anchor.setCurrentIndex(0 if by_sentence else 1)
            self.at_sentence.setValue(impact.at_sentence or 1)
            self.at_offset.setValue(impact.at_offset_ms)
            self.intensity.setValue(impact.intensity)
            self.shake.setValue(impact.shake_px)
            self.duration.setValue(impact.duration_ms)
            self.flash.setCurrentText(impact.flash or "none")
        self._update()
        self._suppress = False

    def _update(self) -> None:
        active = self.enabled.isChecked()
        for widget in (
            self.anchor,
            self.at_sentence,
            self.at_offset,
            self.intensity,
            self.shake,
            self.duration,
            self.flash,
        ):
            widget.setEnabled(active)
        by_sentence = self.anchor.currentData() == "sentence"
        self.at_sentence.setEnabled(active and by_sentence)
        self.at_offset.setEnabled(active and not by_sentence)

    def _dirty(self) -> None:
        if self._suppress or self.canvas.project is None:
            return
        self.canvas.project.mark_dirty()
        self.canvas.update()
        self.changed.emit()

    def _impact(self):
        scene = self.canvas.scene
        return scene.impact if scene is not None else None

    def _on_enabled(self, checked: bool) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.enabled = checked
        self._update()
        self._dirty()

    def _on_anchor(self, _index: int) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.at_sentence = (
            self.at_sentence.value()
            if self.anchor.currentData() == "sentence"
            else None
        )
        self._update()
        self._dirty()

    def _on_sentence(self, value: int) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.at_sentence = value
        self._dirty()

    def _on_offset(self, value: int) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.at_offset_ms = value
        impact.at_sentence = None
        self._dirty()

    def _on_intensity(self, value: float) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.intensity = value
        self._dirty()

    def _on_shake(self, value: int) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.shake_px = value
        self._dirty()

    def _on_duration(self, value: int) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.duration_ms = value
        self._dirty()

    def _on_flash(self, value: str) -> None:
        impact = self._impact()
        if impact is None or self._suppress:
            return
        impact.flash = value
        self._dirty()