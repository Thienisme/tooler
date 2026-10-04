"""
The walk panel: where a character goes during the scene, and when.

A walk is a list of stops, so it is edited as a list rather than as a set
of fields -- an author scripting a conversation needs to see "stand here,
walk there on line 3, walk back on line 5" as three rows they can reorder,
not three sets of numbers that overwrite each other.

The canvas draws the path as a dotted line with the stops marked, so the
plan is visible before anything is rendered.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .canvas import StageCanvas
from .store import Move

EASE_LABELS = {
    "in_out": "Muot va cham chan",
    "out": "Cham dan, dap cham",
    "linear": "Deu deu",
}

# Where a character usually ends up, as a frame fraction.  The stops sit
# inside the frame rather than hard against it, because a cut-out flush to
# the edge reads as cropped rather than as standing there.
QUICK_SPOTS: tuple[tuple[str, float], ...] = (
    ("Trai", 0.18),
    ("Giua", 0.5),
    ("Phai", 0.82),
    ("Hon trai", 0.34),
    ("Hon phai", 0.66),
    ("Sat trai", 0.08),
    ("Sat phai", 0.92),
)


def _pace(speed: float) -> str:
    """The pace the way an author says it: 0.5 -> "0.5", 0.25 -> "0.25".

    Rounded to two decimals so it matches what the spin box stores, then
    trimmed of trailing zeros so a plain "1.5" is not padded to "1.50" and
    a stored 0.25 is never re-rounded to a different number.
    """
    return f"{float(speed):.2f}".rstrip("0").rstrip(".")


class WalkPanel(QWidget):
    """The selected character's route across the scene."""

    changed = Signal()

    def __init__(self, canvas: StageCanvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self._suppress = False

        root = QVBoxLayout(self)

        intro = QLabel(
            "Cho nhan vat di chuyen trong scene: them cac diem dung, va no se noi "
            "diem nay sang diem sau bang mot duong bo cong. Chinh toc do va do "
            "ngang cho tung buoc."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #7a8296;")
        root.addWidget(intro)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        root.addWidget(self.list)

        buttons = QHBoxLayout()
        for label, slot in (
            ("+ Diem", self.on_add),
            ("- Diem", self.on_remove),
            ("Len", lambda: self._move(-1)),
            ("Xuong", lambda: self._move(1)),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        holder = QWidget()
        holder.setLayout(buttons)
        root.addWidget(holder)

        self.group = QGroupBox("Diem dang chon")
        form = QFormLayout(self.group)

        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, 1000)
        self.x_spin.setValue(500)
        self.x_spin.valueChanged.connect(self._on_x)
        form.addRow("Vi tri x (‰)", self.x_spin)

        self.anchor = QComboBox()
        self.anchor.addItem("Theo cau", "sentence")
        self.anchor.addItem("Theo thoi gian", "offset")
        self.anchor.currentIndexChanged.connect(self._on_anchor)
        form.addRow("Bat dau luc", self.anchor)

        self.sentence = QSpinBox()
        self.sentence.setRange(1, 999)
        self.sentence.valueChanged.connect(self._on_sentence)
        form.addRow("Cau thu", self.sentence)

        self.offset = QSpinBox()
        self.offset.setRange(0, 600000)
        self.offset.setSingleStep(200)
        self.offset.setSuffix(" ms")
        self.offset.valueChanged.connect(self._on_offset)
        form.addRow("Sau dau", self.offset)

        self.duration = QSpinBox()
        self.duration.setRange(0, 10000)
        self.duration.setSingleStep(100)
        self.duration.setSuffix(" ms")
        self.duration.valueChanged.connect(self._on_duration)
        form.addRow("Di hanh (ms)", self.duration)

        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.2, 3.0)
        self.speed.setSingleStep(0.1)
        self.speed.setDecimals(2)
        self.speed.setSuffix(" x")
        self.speed.setToolTip(
            "Nhanh cham cua buoc di. 0.5x = di cham, 1x = binh thuong, 2x = chay"
        )
        self.speed.valueChanged.connect(self._on_speed)
        form.addRow("Toc do", self.speed)

        self.travel_hint = QLabel("")
        self.travel_hint.setStyleSheet("color: #7a8296;")
        form.addRow("", self.travel_hint)

        self.sway = QSpinBox()
        self.sway.setRange(0, 70)
        self.sway.setSingleStep(1)
        self.sway.setSuffix(" do")
        self.sway.setToolTip(
            "Do nganh nguoc khi di. 0 = nhan vat di thang, 8-15 = di binh thuong"
        )
        self.sway.valueChanged.connect(self._on_sway)
        form.addRow("Nga nguoc", self.sway)

        self.ease = QComboBox()
        self.ease.addItems(list(EASE_LABELS))
        self.ease.currentTextChanged.connect(self._on_ease)
        form.addRow("Cham muot", self.ease)

        self.flip = QCheckBox("Quay nguoc khi den noi")
        self.flip.toggled.connect(self._on_flip)
        form.addRow(self.flip)

        root.addWidget(self.group)

        # Typing a per-mille number for where someone should stand is the
        # part of this panel that felt worst, so the common places are one
        # click each.  These set the *selected* stop, which is the same
        # thing the x field edits.
        spots = QGroupBox("Den noi nhanh")
        grid = QGridLayout(spots)
        for column, (label, value) in enumerate(QUICK_SPOTS):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, x=value: self._set_x(x)
            )
            grid.addWidget(button, column // 3, column % 3)
        root.addWidget(spots)
        self.spots = spots
        self.spots = spots

        hint = QLabel(
            "Nhân vật đi theo sàn nó đang đứng. Muốn đổi cả sàn thì thêm trường "
            "\"y\" trong script.json."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7a8296;")
        root.addWidget(hint)
        root.addStretch(1)

    # -- populate ---------------------------------------------------------
    def reload(self) -> None:
        self._suppress = True
        placement = self.canvas.current()
        self.list.clear()
        if placement is not None:
            for index, move in enumerate(placement.moves, start=1):
                when = (
                    f"cau {move.at_sentence}"
                    if move.at_sentence is not None
                    else f"{move.at_offset_ms} ms"
                )
                # The pace is shown, not just the authored duration: "0.6x"
                # is what the author set, but 1500ms is what the character
                # actually spends getting there.  The label is trimmed to
                # whole decimals rather than fixed to one, so a stored 0.25
                # does not read back as "0.2x".
                pace = "" if move.speed == 1.0 else f", {_pace(move.speed)}x"
                self.list.addItem(
                    f"{index}. x={move.x:.2f}  ({when}, "
                    f"{move.travel_ms}ms{pace})"
                )

        has_moves = placement is not None
        chosen = has_moves and self.canvas.selected_move is not None
        self.group.setEnabled(chosen)
        self.group.setVisible(has_moves)
        self.spots.setEnabled(chosen)
        self.spots.setVisible(has_moves)
        if has_moves:
            index = self.canvas.selected_move
            if index is not None and 0 <= index < self.list.count():
                self.list.setCurrentRow(index)
            move = self.canvas.current_move()
            if move is not None:
                self.x_spin.setValue(int(round(move.x * 1000)))
                by_sentence = move.at_sentence is not None
                self.anchor.setCurrentIndex(0 if by_sentence else 1)
                self.sentence.setValue(move.at_sentence or 1)
                self.offset.setValue(move.at_offset_ms or 0)
                self.duration.setValue(move.duration_ms)
                self.speed.setValue(move.speed)
                self.sway.setValue(int(round(move.sway_deg)))
                self._update_travel_hint(move)
                label = next(
                    (text for key, text in EASE_LABELS.items() if key == move.ease),
                    "Muot va cham chan",
                )
                self.ease.setCurrentText(label)
                self.flip.setChecked(bool(move.flip))
                self._update_anchor()
        self._suppress = False

    def _update_anchor(self) -> None:
        by_sentence = self.anchor.currentData() == "sentence"
        self.sentence.setEnabled(by_sentence)
        self.offset.setEnabled(not by_sentence)

    def _update_travel_hint(self, move: Move | None = None) -> None:
        """
        Show how long the walk really takes, at the speed actually set.

        `duration_ms` alone is the authored number; with a speed in play it
        is no longer what the viewer sees, and the author needs the real one
        to judge whether the character is about to sprint across the stage.
        """
        move = move if move is not None else self.canvas.current_move()
        if move is None:
            self.travel_hint.setText("")
            return
        real = move.travel_ms
        if abs(move.speed - 1.0) > 1e-6:
            self.travel_hint.setText(f"thuc te: {real} ms")
        else:
            self.travel_hint.setText("")

    def _set_x(self, value: float) -> None:
        """A quick-spot button, routed through the same edit as the x field."""
        self.x_spin.setValue(int(round(value * 1000)))
        # setValue only fires valueChanged when the number actually changes,
        # so a spot matching the current x would otherwise do nothing --
        # which is correct, but the canvas still needs redrawing.
        self._on_x(int(round(value * 1000)))

    # -- actions ----------------------------------------------------------
    def on_add(self) -> None:
        placement = self.canvas.current()
        if placement is None:
            return
        # The new stop starts where the previous one ended, so adding one
        # never teleports the character.
        start_x = (
            placement.moves[-1].x if placement.moves else placement.x
        )
        move = Move(
            x=round(min(0.95, max(0.05, start_x + 0.15)), 4),
            at_sentence=(placement.at_sentence or 1) + len(placement.moves),
            duration_ms=900,
            # A short hop across the stage at a slow, leaning pace: the
            # default a new stop used to get read as a character being
            # yanked, because 700ms of eased slide covers a lot of frame.
            speed=0.8,
            sway_deg=10.0,
        )
        placement.moves.append(move)
        self.canvas.select_move(len(placement.moves) - 1)
        self.refresh_list()
        self.list.setCurrentRow(len(placement.moves) - 1)
        self._dirty()

    def _on_speed(self, value: float) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.speed = round(value, 2)
        self._update_travel_hint(move)
        # The list shows the real duration, so a pace change has to reach it
        # as well as the canvas.
        self.refresh_list()
        self._dirty()

    def _on_sway(self, value: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.sway_deg = float(value)
        self._dirty()

    def on_remove(self) -> None:
        placement = self.canvas.current()
        index = self.canvas.selected_move
        if placement is None or index is None:
            return
        if not (0 <= index < len(placement.moves)):
            return
        del placement.moves[index]
        self.canvas.select_move(
            min(index, len(placement.moves) - 1) if placement.moves else None
        )
        self.refresh_list()
        self._dirty()

    def _move(self, delta: int) -> None:
        placement = self.canvas.current()
        index = self.canvas.selected_move
        if placement is None or index is None:
            return
        target = index + delta
        if not (0 <= target < len(placement.moves)):
            return
        placement.moves[index], placement.moves[target] = (
            placement.moves[target],
            placement.moves[index],
        )
        self.canvas.select_move(target)
        self.refresh_list()
        self.list.setCurrentRow(target)
        self._dirty()

    def refresh_list(self) -> None:
        row = self.canvas.selected_move
        self.reload()
        if row is not None and 0 <= row < self.list.count():
            self.list.setCurrentRow(row)

    def _dirty(self) -> None:
        if self._suppress or self.canvas.project is None:
            return
        self.canvas.project.mark_dirty()
        self.canvas.update()
        self.changed.emit()

    def _on_row(self, row: int) -> None:
        if self._suppress:
            return
        self.canvas.select_move(row if row >= 0 else None)
        self.reload()

    # -- field handlers ---------------------------------------------------
    def _on_x(self, value: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.x = round(value / 1000.0, 4)
        self._dirty()

    def _on_anchor(self, _index: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        if self.anchor.currentData() == "sentence":
            move.at_sentence = self.sentence.value()
            move.at_offset_ms = None
        else:
            move.at_sentence = None
            move.at_offset_ms = self.offset.value()
        self._update_anchor()
        self._dirty()

    def _on_sentence(self, value: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.at_sentence = value
        self._dirty()

    def _on_offset(self, value: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.at_offset_ms = value
        self._dirty()

    def _on_duration(self, value: int) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.duration_ms = value
        self._dirty()

    def _on_ease(self, label: str) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        for key, text in EASE_LABELS.items():
            if text == label:
                move.ease = key
                break
        self._dirty()

    def _on_flip(self, checked: bool) -> None:
        move = self.canvas.current_move()
        if move is None or self._suppress:
            return
        move.flip = checked
        self._dirty()