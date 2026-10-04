"""
The stage canvas: the picture the author actually arranges things on.

Drawn with QPainter rather than assembled from widgets, because the layout
has to match the video frame's proportions exactly -- a sprite placed here
has to land in the same place in the render, and only a real painter
transform guarantees that.

Interaction follows the tools people already know from slide and layout
editors: drag to move, grab a handle to resize, double-click to remove,
drop an asset from the library to add it.  Every gesture writes straight
back into the store as a fraction of the frame, so what is dragged is what
gets saved.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from autovid.domain.script import (
    CHARACTER_ENTER_TYPES,
    CHARACTER_EXIT_TYPES,
    CHARACTER_IDLE_TYPES,
    FRAME_STYLES,
    KEN_BURNS_TYPES,
    TRANSITION_TYPES,
)
from autovid.infrastructure.image.fonts import resolve_font, wrap_lines
from autovid.infrastructure.video.filters import (
    STORY_FRAME_BACKDROP_RGB,
    story_inset_for_style,
)
from autovid.infrastructure.video.text import bubble_inset, tail_direction


class _QtAdvance:
    """
    Lets Qt text metrics stand in for a PIL font when wrapping.

    `wrap_lines` asks its font how wide a string is; PIL spells that
    `getlength` and Qt spells it `horizontalAdvance`.  Rather than write the
    wrapping twice -- once for the render and once for the preview, and hope
    they agree -- the preview measures through this shim.
    """

    def __init__(self, metrics: QFontMetrics) -> None:
        self._metrics = metrics

    def getlength(self, text: str) -> float:
        return float(self._metrics.horizontalAdvance(text))


def _revealed_lines(lines: list[str], revealed: str) -> list[str]:
    """
    The wrapped lines as they stand mid-typewriter.

    Whole lines first, with the cut inside one line -- the same rule the
    renderer's typewriter uses, so the preview reveals in the same places.
    """
    budget = len(revealed)
    out: list[str] = []
    for line in lines:
        if budget <= 0:
            break
        if len(line) <= budget:
            out.append(line)
            budget -= len(line)
            continue
        out.append(line[:budget])
        budget = 0
    return out or [""]

from .assets import AssetLibrary
from .preview import SceneTimeline
from .store import Placement, Project, Scene, StoryFrame

# Mime payload for a library row being dragged onto the stage.
MIME_BACKGROUND = "application/x-autovid-background"
MIME_CHARACTER = "application/x-autovid-character"
# The storyteller lists carry a *registry key*, not a path: the artwork,
# the mouth flap and the poses all ride in from `characters.json`, so a
# path would silently drop them.
MIME_NARRATOR = "application/x-autovid-narrator"

HANDLE = 8  # px, the resize grip in either lower corner
MIN_SPRITE_FRACTION = 0.04


def _pil_to_qpixmap(image: Image.Image, max_size: int = 2048) -> QPixmap:
    """A cached Pillow image as a Qt pixmap, downscaled to bound memory."""
    if max(image.size) > max_size:
        scale = max_size / max(image.size)
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )
    data = image.convert("RGBA").tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


# Where a text overlay sits, as a fraction of the frame's size.  The same
# nine spots the schema's TEXT_POSITIONS names, so a caption dragged into
# the preview lands in the place the render will use.
OVERLAY_ANCHORS: dict[str, tuple[float, float]] = {
    "top": (0.5, 0.06),
    "top_left": (0.06, 0.06),
    "top_right": (0.94, 0.06),
    "center": (0.5, 0.5),
    "bottom": (0.5, 0.94),
    "bottom_left": (0.06, 0.94),
    "bottom_right": (0.94, 0.94),
}


class StageCanvas(QWidget):
    """
    The editable stage for one scene.

    Emits `changed` whenever a gesture edits the scene, so the window can
    refresh the property panel and mark the project dirty.  The canvas holds
    no state of its own beyond the drag in progress: `scene` and `frame`
    are the store's objects and are edited in place, which keeps the panel
    and the canvas from ever disagreeing about what is on the stage.
    """

    changed = Signal()
    selection_changed = Signal(int)
    overlay_changed = Signal(int)
    move_changed = Signal(int)

    MIMES = (MIME_BACKGROUND, MIME_CHARACTER, MIME_NARRATOR)

    def __init__(
        self,
        library: AssetLibrary,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.library = library

        self.project: Project | None = None
        self.scene: Scene | None = None
        self.frame: StoryFrame = StoryFrame()
        self.resolution: tuple[int, int] = (1920, 1080)

        # `selected` is what the inspector edits; `also_selected` are the
        # rest of the current selection, so a cast can be nudged together
        # the way a group in any layout tool moves.
        self.selected: int | None = None
        self.also_selected: set[int] = set()
        self.progress: float = 0.0  # where in the scene's Ken Burns we are
        self.selected_overlay: int | None = None
        self.selected_move: int | None = None

        self._pixmaps: dict[str, QPixmap] = {}
        # Font file -> the family name Qt gave it.  Registering a face is
        # global and not free, so it happens once per file per session.
        self._font_families: dict[str, str] = {}
        self._drag_index: int | None = None
        self._drag_group: list[Placement] = []
        self._resizing: bool = False
        self._grab_offset: tuple[float, float] = (0.0, 0.0)
        self._last_pos: QPoint | None = None
        self._drop_target: QRectF | None = None
        self._drag_overlay: int | None = None

        self.setAcceptDrops(True)
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- geometry ---------------------------------------------------------
    def _stage_rect(self) -> QRectF:
        """The video frame drawn inside the widget, letterboxed to ratio."""
        frame_width, frame_height = self.resolution
        ratio = frame_width / frame_height
        widget_ratio = self.width() / max(1, self.height())
        if widget_ratio > ratio:
            height = self.height()
            width = int(round(height * ratio))
        else:
            width = self.width()
            height = int(round(width / ratio))
        return QRectF(
            (self.width() - width) / 2, (self.height() - height) / 2, width, height
        )

    def _to_px(self, fraction_x: float, fraction_y: float) -> QPointF:
        stage = self._stage_rect()
        return stage.topLeft() + QPointF(stage.width() * fraction_x, stage.height() * fraction_y)

    def _to_fraction(self, point: QPointF) -> tuple[float, float]:
        stage = self._stage_rect()
        if stage.width() <= 0 or stage.height() <= 0:
            return (0.0, 0.0)
        return (
            (point.x() - stage.left()) / stage.width(),
            (point.y() - stage.top()) / stage.height(),
        )

    def sprite_rect(self, placement: Placement) -> QRectF:
        """Where a character stands, in widget pixels, at the scrubbed time."""
        centre = self._to_px(self._walked_x(placement), placement.y)
        sprite = self._pixmap_for(placement.image_file)
        aspect = (sprite.width() / sprite.height()) if sprite.height() else 1.0
        height = placement.height * self._stage_rect().height()
        width = height * aspect
        return QRectF(
            centre.x() - width / 2, centre.y() - height, width, height
        )

    def _walked_x(self, placement: Placement) -> float:
        """
        Where the sprite stands at the scrubbed time, its walk included.

        Drawing, hit-testing and dragging all go through here, so the sprite
        the author clicks is the sprite they see.  While a drag is in flight
        the sprite follows the cursor instead: applying the walk to a
        position being edited would fight the mouse.
        """
        timeline = self.timeline()
        if timeline is None:
            return placement.x
        if any(placement is dragged for dragged in self._drag_group):
            return placement.x
        return timeline.walk_position(placement, self.progress * timeline.span)[0]

    def _pixmap_for(self, relative: str) -> QPixmap:
        if relative in self._pixmaps:
            return self._pixmaps[relative]
        path = self.library.resolve(relative)
        pixmap = QPixmap()
        if path is not None:
            try:
                with Image.open(path) as raw:
                    pixmap = _pil_to_qpixmap(raw)
            except (OSError, ValueError):
                pixmap = QPixmap()
        self._pixmaps[relative] = pixmap
        return pixmap

    def clear_cache(self) -> None:
        self._pixmaps.clear()

    # -- model ------------------------------------------------------------
    def set_project(self, project: Project | None) -> None:
        self.project = project
        if project is not None:
            self.resolution = project.resolution
            self.frame = project.story_frame
        self.clear_cache()
        self.update()

    def set_scene(self, scene: Scene | None) -> None:
        self.scene = scene
        self.selected = None
        self.also_selected = set()
        self.selected_overlay = None
        self.selected_move = None
        self.progress = self._design_progress(scene)
        self.update()

    # -- the preview clock ------------------------------------------------
    def preview_speed(self) -> float:
        """The project's TTS speed, so the preview estimates the right length."""
        config = self.project.preserved.get("tts_config") if self.project else None
        if isinstance(config, dict):
            try:
                return max(0.1, float(config.get("speed") or 1.0))
            except (TypeError, ValueError):
                return 1.0
        return 1.0

    def timeline(self) -> SceneTimeline | None:
        """The estimated clock for the scene on the stage, if there is one."""
        if self.scene is None:
            return None
        return SceneTimeline(self.scene, self.preview_speed())

    def _design_progress(self, scene: Scene | None) -> float:
        """
        Where the scrub parks when a scene is loaded: just after everyone
        has arrived, so a dragged-in character is on screen rather than
        hanging off its own entrance animation.
        """
        if scene is None:
            return 0.0
        timeline = SceneTimeline(scene, self.preview_speed())
        if timeline.span <= 0:
            return 0.0
        return max(0.0, min(1.0, timeline.design_seconds(scene.characters) / timeline.span))

    def select_move(self, index: int | None) -> None:
        self.selected_move = index
        self.move_changed.emit(index if index is not None else -1)
        self.update()

    def current_move(self):
        """The walk stop the walk panel is editing, if any."""
        placement = self.current()
        if placement is None or self.selected_move is None:
            return None
        if 0 <= self.selected_move < len(placement.moves):
            return placement.moves[self.selected_move]
        return None

    def set_progress(self, progress: float) -> None:
        """Scrub the Ken Burns so the author can see the move, not guess."""
        self.progress = max(0.0, min(1.0, progress))
        self.update()

    def frame_active(self) -> bool:
        """Whether this scene draws the panel: script default minus opt-out."""
        if self.scene is None or not self.frame.enabled:
            return False
        return self.scene.story_frame_enabled is not False

    def select(self, index: int | None) -> None:
        """Select one character on its own."""
        self.selected = index
        self.also_selected = set()
        self.selection_changed.emit(index if index is not None else -1)
        self.update()

    def toggle_selection(self, index: int) -> None:
        """Add or remove one character from the current selection."""
        if index == self.selected:
            self.selected = None
            self.also_selected = set()
        else:
            if self.selected is None:
                self.selected = index
            self.also_selected.add(index)
        self.selection_changed.emit(self.selected if self.selected is not None else -1)
        self.update()

    def select_all(self) -> None:
        """Select the whole cast, so one drag can move everyone."""
        if self.scene is None or not self.scene.characters:
            self.select(None)
            return
        self.selected = 0
        self.also_selected = set(range(1, len(self.scene.characters)))
        self.selection_changed.emit(0)
        self.update()

    def is_selected(self, index: int) -> bool:
        return index == self.selected or index in self.also_selected

    def current(self) -> Placement | None:
        if self.scene is None or self.selected is None:
            return None
        if 0 <= self.selected < len(self.scene.characters):
            return self.scene.characters[self.selected]
        return None

    def selected_overlay_item(self):
        """The text overlay the overlay panel is editing, if any."""
        if self.scene is None or self.selected_overlay is None:
            return None
        if 0 <= self.selected_overlay < len(self.scene.overlays):
            return self.scene.overlays[self.selected_overlay]
        return None

    def select_overlay(self, index: int | None) -> None:
        self.selected_overlay = index
        self.overlay_changed.emit(index if index is not None else -1)
        self.update()

    # -- impact -----------------------------------------------------------
    def impact_scale(self) -> tuple[float, float, QColor | None]:
        """
        The scene's punch-in at the scrubbed position: (scale, shake_px, flash).

        The spike is quick in and slow out, which is what makes a punch read
        as a hit rather than a slow zoom: it peaks a third of the way in.
        """
        scene = self.scene
        if scene is None or not scene.impact.enabled:
            return (1.0, 0.0, None)
        timeline = self.timeline()
        if timeline is None:
            return (1.0, 0.0, None)

        duration = max(0.05, scene.impact.duration_ms / 1000.0)
        # Time runs from the punch-in's own beat -- the sentence it waits
        # for, or its offset -- so the preview shows the hit where the
        # render will put it, not always at the top of the scene.
        seconds = self.progress * timeline.span - timeline.impact_offset()
        if seconds < 0.0 or seconds > duration:
            return (1.0, 0.0, None)

        peak = min(1.0, max(0.0, seconds / (duration * 0.34)))
        settle = min(1.0, max(0.0, (seconds - duration * 0.34) / (duration * 0.66)))
        # Out quickly, back slowly.
        amount = peak * (1.0 - settle**1.6)
        scale = 1.0 + scene.impact.intensity * amount

        shake = scene.impact.shake_px * amount
        flash = None
        if scene.impact.flash in ("white", "black") and amount > 0.05:
            value = int(255 * min(0.45, max(0.0, amount)))
            flash = (
                QColor(value, value, value)
                if scene.impact.flash == "white"
                else QColor(0, 0, 0, value)
            )
        return (scale, shake, flash)

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#14161c"))

        stage = self._stage_rect()
        if self.scene is None:
            painter.setPen(QColor("#5a6072"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Chon mot scene de sua")
            return

        scale, shake, flash = self.impact_scale()

        painter.save()
        painter.setClipRect(stage)
        # The punch zooms and shakes the whole stage, characters included,
        # so it is applied as a transform on the painter rather than inside
        # the artwork pass.
        painter.translate(stage.center())
        if shake > 0.5:
            wobble = math.sin(self.progress * 40.0) * shake * (
                stage.height() / 1080.0
            )
            painter.translate(wobble, wobble * 0.6)
        if abs(scale - 1.0) > 1e-3:
            painter.scale(scale, scale)
        painter.translate(-stage.center().x(), -stage.center().y())
        self._paint_stage(painter, stage)
        painter.restore()

        if flash is not None:
            painter.fillRect(stage, flash)

        painter.setPen(QPen(QColor("#39405a"), 1))
        painter.drawRect(stage)

    def _paint_stage(self, painter: QPainter, stage: QRectF) -> None:
        scene = self.scene
        assert scene is not None
        framed = self.frame_active()

        if framed:
            backdrop = QColor(*STORY_FRAME_BACKDROP_RGB)
            painter.fillRect(stage, backdrop)
            panel = self._pixmap_for(
                f"assets/frames/story_frame_{self.frame.style}.png"
            )
            panel_rect = QRectF(
                stage.left() + self.frame.x * stage.width(),
                stage.top() + self.frame.y * stage.height(),
                self.frame.width * stage.width(),
                self.frame.height * stage.height(),
            )
            if not panel.isNull():
                painter.drawPixmap(panel_rect, panel, QRectF(panel.rect()))
            window = self._art_window(stage, panel_rect)
            if window is not None:
                self._paint_artwork(painter, window, scene)
                # Characters live inside the panel's window, clipped to it.
                painter.save()
                painter.setClipRect(window)
                self._paint_characters(painter, stage)
                painter.restore()
            if self.frame.show_narrator:
                self._paint_narrator(painter, stage)
        else:
            self._paint_artwork(painter, stage, scene)
            self._paint_characters(painter, stage)

        # The route is an authoring aid, not part of the finished picture,
        # so it is drawn over the stage but under the captions.
        self._paint_walk(painter, stage)
        self._paint_overlays(painter, stage)

    def _paint_walk(self, painter: QPainter, stage: QRectF) -> None:
        """
        Draw the selected character's route: a dotted line from where it
        stands through every stop in order, each stop marked.

        The plan is easier to judge drawn than read from a list of numbers,
        and the stops sit at the character's feet so a row in the panel and
        a mark on the stage refer to the same place.
        """
        placement = self.current()
        if placement is None or not placement.moves:
            return

        sprite = self._pixmap_for(placement.image_file)
        height = max(8.0, placement.height * stage.height())
        width = (
            height * (sprite.width() / sprite.height())
            if sprite.height()
            else height * 0.5
        )

        def foot_at(x: float) -> QPointF:
            return QPointF(
                stage.left() + x * stage.width(),
                stage.top() + placement.y * stage.height(),
            )

        points = [foot_at(placement.x)]
        points.extend(foot_at(move.x) for move in placement.moves)

        painter.save()
        pen = QPen(QColor("#4ea3ff"), 2, Qt.DashLine)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)

        for index, move in enumerate(placement.moves):
            centre = foot_at(move.x)
            radius = 9.0
            chosen = index == self.selected_move
            painter.setBrush(
                QBrush(QColor("#ffc94d") if chosen else QColor("#4ea3ff"))
            )
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(centre, radius, radius)
            if chosen:
                # The stop being edited also shows where the sprite will be
                # standing at that moment, so the author sees the size they
                # are about to get rather than just a dot on the floor.
                rect = QRectF(
                    centre.x() - width / 2, centre.y() - height, width, height
                )
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#ffc94d"), 2, Qt.DashLine))
                painter.drawRect(rect)
        painter.restore()

    def _art_window(self, stage: QRectF, panel: QRectF) -> QRectF | None:
        """
        The picture window inside the panel.

        Same arithmetic as the assembler, so a sprite dropped inside this
        rectangle lands inside the frame in the finished video too.
        """
        inset = story_inset_for_style(self.frame.style)
        if isinstance(inset, dict):
            top = panel.height() * float(inset.get("t", 0.0))
            bottom = panel.height() * float(inset.get("b", 0.0))
            left = panel.width() * float(inset.get("l", 0.0))
            right = panel.width() * float(inset.get("r", 0.0))
        else:
            mat = min(panel.width(), panel.height()) * float(inset)
            top = bottom = left = right = mat
        window = panel.adjusted(
            int(left), int(top), -int(right), -int(bottom)
        )
        if window.width() <= 2 or window.height() <= 2:
            return None
        return window

    def _paint_artwork(self, painter: QPainter, target: QRectF, scene: Scene) -> None:
        background = self._pixmap_for(scene.image_file)
        if background.isNull():
            painter.fillRect(target, QColor("#232733"))
            painter.setPen(QColor("#6c7488"))
            painter.drawText(
                target,
                Qt.AlignCenter,
                "Chua chon anh nen\n(keo mot anh tu ben trai vao)",
            )
            return

        # Ken Burns: the scale the scene's move reaches at `progress`.
        scale = scene.motion.scale_at(self.progress)
        painter.save()
        painter.setClipRect(target)
        source = QRectF(background.rect())
        drawn_w = target.width() * scale
        drawn_h = target.height() * scale
        left = target.left() - (drawn_w - target.width()) / 2
        top = target.top() - (drawn_h - target.height()) / 2
        # A pan shifts the window instead of resizing it.
        if scene.motion.type.startswith("pan_"):
            if scene.motion.type == "pan_right":
                left -= drawn_w - target.width()
            elif scene.motion.type == "pan_left":
                left += drawn_w - target.width()
            elif scene.motion.type == "pan_down":
                top -= drawn_h - target.height()
            else:
                top += drawn_h - target.height()
        painter.drawPixmap(QRectF(left, top, drawn_w, drawn_h), background, source)
        painter.restore()

    def _paint_characters(self, painter: QPainter, stage: QRectF) -> None:
        scene = self.scene
        assert scene is not None
        timeline = self.timeline()
        assert timeline is not None  # a scene on the stage always has a clock
        seconds = timeline.seconds(self.progress)

        for index, placement in enumerate(scene.characters):
            sprite = self._pixmap_for(placement.image_file)
            rect = self.sprite_rect(placement)
            size = (rect.width() / stage.width(), rect.height() / stage.height())
            state = timeline.actor_state(placement, seconds, size)
            if state is None:
                # Waiting for its sentence, or already gone: either way this
                # character is not part of the frame being previewed.
                continue
            if sprite.isNull():
                # A missing sprite still shows its footprint, so the author
                # can see and fix the placement instead of hunting blind.
                painter.setPen(QPen(QColor("#e2564d"), 1, Qt.DashLine))
                painter.setBrush(QBrush(QColor(226, 86, 77, 40)))
                painter.drawRect(rect)
                painter.setPen(QColor("#ffb4ae"))
                painter.drawText(rect, Qt.AlignCenter, "thieu anh")
                continue

            # Entrance/exit travel moves the box; the turn (walk flip or a
            # spin) and the size ride inside it, exactly as the render
            # composes sprite travel, rotation and scale.
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, state.opacity)))
            painter.translate(
                rect.center().x() + state.dx * stage.width(),
                rect.center().y() + state.dy * stage.height(),
            )
            if state.rotation:
                painter.rotate(state.rotation)
            scale = state.scale
            painter.scale(-scale if state.flip else scale, scale)
            painter.drawPixmap(
                QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height()),
                sprite,
                QRectF(sprite.rect()),
            )
            painter.restore()

            if index == self.selected or index in self.also_selected:
                painter.setPen(QPen(QColor("#4ea3ff"), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
                if index == self.selected:
                    painter.setBrush(QBrush(QColor("#4ea3ff")))
                    for corner in self._handles(rect):
                        painter.drawRect(corner.adjusted(-3, -3, 3, 3))

    def _paint_caption_frame(
        self,
        painter: QPainter,
        box: QRectF,
        *,
        kind: str,
        padding: float,
        tail: str,
        fill: str,
        stroke: str,
        width: float,
    ) -> None:
        """
        Draw a caption's box in the preview.

        The same three shapes the renderer draws with PIL -- a rounded box
        with a tail, a cloud of puffs, a starburst -- so that choosing
        `thought` in the panel shows the cloud, not a rectangle that only
        turns into one in the finished video.
        """
        outer = box.adjusted(-padding, -padding, padding, padding)
        pen = QPen(QColor(stroke), width)
        brush = QBrush(QColor(fill))
        painter.setPen(pen)
        painter.setBrush(brush)

        if kind == "speech":
            painter.drawRoundedRect(outer, padding * 1.5, padding * 1.5)
            self._paint_caption_tail(painter, outer, tail, padding, fill, stroke)
            return

        if kind == "thought":
            centre_x = outer.center().x()
            centre_y = outer.center().y()
            radius_x = outer.width() / 2 + padding * 0.4
            radius_y = outer.height() / 2 + padding * 0.4
            for dx, dy, scale in (
                (-0.30, 0.22, 1.05),
                (0.30, 0.20, 1.00),
                (-0.28, -0.20, 0.92),
                (0.30, -0.22, 0.96),
                (0.00, 0.28, 0.88),
            ):
                puff = padding * 1.1 * scale
                painter.drawEllipse(
                    QPointF(
                        centre_x + outer.width() * dx,
                        centre_y + outer.height() * dy,
                    ),
                    puff,
                    puff,
                )
            x = outer.left() + outer.width() * 0.34
            y = outer.bottom() if tail == "down" else outer.top()
            step = padding * 0.95
            direction = 1.0 if tail == "down" else -1.0
            for index, scale in enumerate((0.55, 0.40, 0.26)):
                puff = padding * 1.1 * scale
                painter.drawEllipse(
                    QPointF(x, y + step * (index + 1) * direction), puff, puff
                )
            return

        if kind == "shout":
            centre = outer.center()
            spikes = 20
            half_x = outer.width() / 2
            half_y = outer.height() / 2
            path_points: list[QPointF] = []
            for index in range(spikes * 2):
                angle = math.pi * index / spikes - math.pi / 2
                spike = index % 2 == 0
                radius_x = half_x + padding * 0.55 if spike else half_x * 0.84
                radius_y = half_y + padding * 0.55 if spike else half_y * 0.84
                path_points.append(
                    QPointF(
                        centre.x() + math.cos(angle) * radius_x,
                        centre.y() + math.sin(angle) * radius_y,
                    )
                )
            painter.setBrush(brush)
            painter.drawPolygon(QPolygonF(path_points))

    def _paint_caption_tail(
        self,
        painter: QPainter,
        outer: QRectF,
        tail: str,
        padding: float,
        fill: str,
        stroke: str,
    ) -> None:
        """The point that says who is talking, sitting off to one side."""
        size = padding * 1.3
        y = outer.bottom() if tail == "down" else outer.top()
        direction = 1.0 if tail == "down" else -1.0
        x = outer.left() + outer.width() * 0.32
        spread = size * 0.55
        triangle = QPolygonF(
            [
                QPointF(x - spread, y - size * 0.18 * direction),
                QPointF(x + spread, y - size * 0.18 * direction),
                QPointF(x + size * 0.35, y + size * direction),
            ]
        )
        painter.setBrush(QBrush(QColor(fill)))
        painter.setPen(QPen(QColor(stroke), 1))
        painter.drawPolygon(triangle)

    def caption_font(self, reference: str, size: int) -> QFont:
        """
        The caption's own face, loaded from the file the render will open.

        The preview used to draw every caption in the system font, bold, so a
        caption picked for its handwriting looked exactly like one picked for
        its weight -- and a `Bangers` caption was judged on a face nobody
        would ever see.  `QFontDatabase.addApplicationFont` is the only way
        Qt draws a TTF that is not installed system-wide, which is our
        situation: the faces ship with the project.

        Returns the system default when the file cannot be read, which is
        what the preview has always done for a caption with no font.
        """
        fallback = QFont()
        fallback.setPixelSize(int(round(size)))

        resolution = resolve_font(reference, self.library.workspace)
        if resolution.path is None or not resolution.path.is_file():
            return fallback

        family = self._font_families.get(str(resolution.path))
        if family is None:
            font_id = QFontDatabase.addApplicationFont(str(resolution.path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if font_id < 0 or not families:
                return fallback
            family = families[0]
            self._font_families[str(resolution.path)] = family

        font = QFont(family)
        font.setPixelSize(int(round(size)))
        return font

    def overlay_layout(
        self, overlay, stage: QRectF, state
    ) -> tuple[QFont, list[str], QRectF, float, float] | None:
        """
        Where a caption sits at this moment, and in what face.

        Returns the font, the wrapped lines, the block's box, the box's
        padding and the line height -- or None when the caption has nothing
        to say yet.  Painting and hit-testing both go through here, so what
        the mouse can grab is exactly what the eye is looking at.

        `font_size` in the script is pixels on a 1080-line frame, so it is
        scaled by the preview's own height -- otherwise a caption authored
        at 72px would look enormous in a 540px preview.
        """
        anchor = OVERLAY_ANCHORS.get(overlay.position, OVERLAY_ANCHORS["bottom"])
        size = max(8.0, overlay.font_size * (stage.height() / 1080.0))
        font = self.caption_font(overlay.font, int(round(size)))
        metrics = QFontMetrics(font)

        if overlay.image_file:
            return self._image_layout(overlay, stage)

        # The bubble's padding is reserved from the caption's own area, so
        # the box grows outwards and still lands inside the frame.
        padding = bubble_inset(overlay.font_size, overlay.frame) * (
            stage.height() / 1080.0
        )
        # Shrink to fit rather than letting a long caption run off the
        # frame, which is what the assembler does with the same rule.
        available = max(stage.width() * 0.9 - 2 * padding, size)
        text_width = metrics.horizontalAdvance(overlay.text)
        if text_width > available and text_width > 0:
            font.setPixelSize(max(8, int(round(size * available / text_width))))
            metrics = QFontMetrics(font)

        # Wrapped by the same rule the renderer wraps by, so the preview
        # breaks a caption in the same places the video will.
        lines = wrap_lines(
            overlay.text,
            _QtAdvance(metrics),
            available,
            stroke_width=overlay.stroke_width,
        )
        # Mid-typewriter the caption is shorter than its final self, but
        # it is laid out as if complete: otherwise the words crawl
        # outwards from the centre on every keystroke.
        if state.text.strip() and state.text.strip() != overlay.text.strip():
            lines = _revealed_lines(lines, state.text)

        line_height = metrics.height()
        block_width = max(
            (metrics.horizontalAdvance(line) for line in lines), default=1
        )
        block_height = line_height * len(lines)

        if overlay.x is not None and overlay.y is not None:
            # A caption the author placed by hand is centred on its
            # coordinates, exactly as the renderer centres it.
            left = stage.left() + overlay.x * stage.width() - block_width / 2
            top = stage.top() + overlay.y * stage.height() - block_height / 2
        else:
            point = QPointF(
                stage.left() + (anchor[0] + state.dx) * stage.width(),
                stage.top() + anchor[1] * stage.height(),
            )
            left = {
                "top_left": point.x() + padding,
                "bottom_left": point.x() + padding,
            }.get(overlay.position, point.x() - block_width / 2)
            if overlay.position.endswith("_right"):
                left = point.x() - block_width - padding
            top = {
                "top": point.y() + padding,
                "top_left": point.y() + padding,
                "top_right": point.y() + padding,
            }.get(overlay.position, point.y() - block_height - padding)

        return font, lines, QRectF(left, top, block_width, block_height), padding, line_height

    def _image_layout(self, overlay, stage: QRectF) -> tuple | None:
        """
        Where a picture overlay lands, in the same terms as a caption's box.

        The height is the author's share of the frame and the width follows
        from the file's own shape, exactly as the render works it out -- a
        picture squashed into a fixed box is not a caption, it is a mistake.
        """
        pixmap = self._pixmap_for(overlay.image_file)
        if pixmap.isNull():
            return None
        height = max(8.0, overlay.image_height * stage.height())
        width = height * (pixmap.width() / max(1, pixmap.height()))
        left = {
            "top_left": stage.left() + stage.width() * 0.05,
            "bottom_left": stage.left() + stage.width() * 0.05,
        }.get(overlay.position, stage.left() + (stage.width() - width) / 2)
        if overlay.position.endswith("_right"):
            left = stage.right() - stage.width() * 0.05 - width
        top = {
            "top": stage.top() + stage.height() * 0.05,
            "top_left": stage.top() + stage.height() * 0.05,
            "top_right": stage.top() + stage.height() * 0.05,
        }.get(overlay.position, stage.top() + (stage.height() - height) / 2)
        if overlay.position.startswith("bottom"):
            top = stage.bottom() - stage.height() * 0.05 - height

        if overlay.x is not None and overlay.y is not None:
            left = stage.left() + overlay.x * stage.width() - width / 2
            top = stage.top() + overlay.y * stage.height() - height / 2

        box = QRectF(left, top, width, height)
        return QFont(), [], box, 0.0, height

    def overlay_at(self, point: QPointF) -> int | None:
        """
        Which caption is under the cursor, topmost first.

        Captions are drawn over the characters, so a caption is grabbed
        before the sprite underneath it -- the same order they are painted
        in, or a caption the author can see would be one they cannot move.
        """
        scene = self.scene
        if scene is None:
            return None
        timeline = self.timeline()
        if timeline is None:
            return None
        stage = self._stage_rect()
        seconds = timeline.seconds(self.progress)
        for index in range(len(scene.overlays) - 1, -1, -1):
            overlay = scene.overlays[index]
            if not overlay.text.strip() and not overlay.image_file:
                continue
            state = timeline.overlay_state(overlay, seconds)
            if state is None or not state.text.strip():
                continue
            layout = self.overlay_layout(overlay, stage, state)
            if layout is None:
                continue
            _font, _lines, box, padding, _line_height = layout
            if box.adjusted(-padding, -padding, padding, padding).contains(point):
                return index
        return None

    def _paint_overlays(self, painter: QPainter, stage: QRectF) -> None:
        """
        Draw the scene's captions where the render will put them.
        """
        scene = self.scene
        assert scene is not None
        timeline = self.timeline()
        assert timeline is not None
        seconds = timeline.seconds(self.progress)
        for index, overlay in enumerate(scene.overlays):
            if not overlay.text.strip() and not overlay.image_file:
                continue
            state = timeline.overlay_state(overlay, seconds)
            if state is None or not state.text.strip():
                # Not showing at this moment: the caption has its own window.
                continue
            layout = self.overlay_layout(overlay, stage, state)
            if layout is None:
                continue
            font, lines, box, padding, line_height = layout
            block_width = box.width()

            painter.save()
            painter.setFont(font)
            painter.setOpacity(max(0.0, min(1.0, state.opacity)))
            if overlay.image_file:
                pixmap = self._pixmap_for(overlay.image_file)
                painter.drawPixmap(box, pixmap, QRectF(pixmap.rect()))
                painter.restore()
                if index == self.selected_overlay:
                    painter.setPen(QPen(QColor("#4ea3ff"), 1, Qt.DashLine))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(box.adjusted(-6, -6, 6, 6))
                continue
            if overlay.frame != "none":
                self._paint_caption_frame(
                    painter,
                    box,
                    kind=overlay.frame,
                    padding=padding,
                    tail=tail_direction(overlay.position),
                    fill=overlay.frame_fill,
                    stroke=overlay.frame_stroke,
                    width=max(overlay.stroke_width, 2),
                )

            # The stroke is what keeps a caption readable over a bright
            # picture, so it is drawn twice: once fat in the stroke colour,
            # then the fill on top.
            for line_index, line in enumerate(lines):
                line_box = QRectF(
                    box.left(),
                    box.top() + line_index * line_height,
                    block_width,
                    line_height,
                )
                flags = int(Qt.AlignLeft | Qt.AlignVCenter)
                if overlay.stroke_width > 0:
                    painter.setPen(
                        QPen(QColor(overlay.stroke_color), overlay.stroke_width)
                    )
                    painter.drawText(line_box, flags, line)
                painter.setPen(QPen(QColor(overlay.color)))
                painter.drawText(line_box, flags, line)
            painter.restore()

            if index == self.selected_overlay:
                painter.setPen(QPen(QColor("#4ea3ff"), 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(box.adjusted(-6, -6, 6, 6))

    def _paint_narrator(self, painter: QPainter, stage: QRectF) -> None:
        host_path = self._narrator_path()
        sprite = self._pixmap_for(host_path) if host_path else QPixmap()
        if sprite.isNull():
            return
        height = self.frame.host_height * stage.height()
        width = height * (sprite.width() / max(1, sprite.height()))
        centre = stage.left() + self.frame.host_x * stage.width()
        foot = stage.top() + self.frame.host_y * stage.height()
        rect = QRectF(centre - width / 2, foot - height, width, height)
        if self.frame.host_flip:
            painter.save()
            painter.translate(rect.center())
            painter.scale(-1, 1)
            painter.drawPixmap(
                QRectF(-width / 2, -height / 2, width, height), sprite, QRectF(sprite.rect())
            )
            painter.restore()
        else:
            painter.drawPixmap(rect, sprite, QRectF(sprite.rect()))

    def _narrator_path(self) -> str | None:
        """
        Where the narrator sprite lives.

        A picture dropped in `assets/narrators/` names itself
        (`story_frame.image_file`); a registry key (`story_frame.use`) is
        looked up instead, which is what brings the entry's mouth flap and
        poses along.  The library reads the registry (repo's, then the
        project's own), so preview and pipeline agree on a key's artwork.
        """
        if self.frame.image_file:
            return self.frame.image_file
        key = self.frame.use
        if not key:
            return None
        entry = self.library.registry().get(key)
        return str(entry.get("image_file")) if isinstance(entry, dict) else None

    def _hit_move(self, position: QPointF) -> bool:
        """Pick the walk stop under the cursor, if the walk is visible."""
        placement = self.current()
        if placement is None or not placement.moves or self.scene is None:
            return False
        stage = self._stage_rect()
        radius = 14.0
        for index, move in enumerate(placement.moves):
            point = QPointF(
                stage.left() + move.x * stage.width(),
                stage.top() + placement.y * stage.height(),
            )
            if (point - position).manhattanLength() <= radius:
                self.select_move(index)
                return True
        return False

    def _handles(self, rect: QRectF) -> list[QRectF]:
        size = HANDLE
        return [
            QRectF(rect.right() - size, rect.bottom() - size, size * 2, size * 2),
            QRectF(rect.left() - size, rect.bottom() - size, size * 2, size * 2),
        ]

    # -- mouse ------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.scene is None:
            return
        position = event.position()
        # A stop marker takes the click before the sprite does, so a dot on
        # the floor can be picked without hitting a small character.
        if self._hit_move(position):
            self.selection_changed.emit(
                self.selected if self.selected is not None else -1
            )
            self.move_changed.emit(self.selected_move if self.selected_move is not None else -1)
            self.update()
            return
        # Topmost first, so the character drawn last is the one grabbed.
        for index in range(len(self.scene.characters) - 1, -1, -1):
            rect = self.sprite_rect(self.scene.characters[index])
            for handle in self._handles(rect):
                if handle.contains(position):
                    self._resizing = True
                    self.selected = index
                    self.selection_changed.emit(index)
                    self._last_pos = position.toPoint()
                    self.update()
                    return
            if rect.contains(position):
                additive = bool(event.modifiers() & Qt.ShiftModifier)
                if additive:
                    self.toggle_selection(index)
                    if self.selected is None:
                        return
                elif not self.is_selected(index):
                    self.select(index)

                self._drag_index = index
                placement = self.scene.characters[index]
                # Everything in the current selection moves together, so a
                # whole stage can be nudged or re-centred in one gesture.
                self._drag_group = [
                    self.scene.characters[i]
                    for i in sorted(self.also_selected | {self.selected})
                    if i is not None and 0 <= i < len(self.scene.characters)
                ]
                self.selection_changed.emit(
                    self.selected if self.selected is not None else -1
                )
                self._grab_offset = (
                    position.x() - rect.center().x(),
                    position.y() - rect.bottom(),
                )
                self._last_pos = position.toPoint()
                self.update()
                return

        # Captions are drawn over the characters, so they are grabbed after
        # them -- but before the click falls through to "deselect", because a
        # caption the author can see has to be one they can move.
        overlay_index = self.overlay_at(position)
        if overlay_index is not None and event.button() == Qt.LeftButton:
            self.select_overlay(overlay_index)
            self._drag_overlay = overlay_index
            # The grab offset is measured from the caption's own centre, so
            # the drag moves the caption rather than jumping its centre to
            # wherever the cursor happened to land.
            state = self.timeline().overlay_state(
                self.scene.overlays[overlay_index],
                self.timeline().seconds(self.progress),
            )
            layout = self.overlay_layout(
                self.scene.overlays[overlay_index], self._stage_rect(), state
            )
            centre = (
                layout[2].center()
                if layout is not None
                else QPointF(position)
            )
            self._grab_offset = (position.x() - centre.x(), position.y() - centre.y())
            self._last_pos = position.toPoint()
            self.update()
            return

        if event.button() == Qt.LeftButton:
            self.select(None)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.scene is None or self._last_pos is None:
            return

        if self._drag_overlay is not None:
            self._move_overlay(event.position())
            return

        placement = self.current()
        if placement is None:
            return
        position = event.position()
        delta = position - QPointF(self._last_pos)
        stage = self._stage_rect()

        if self._resizing:
            # Dragging the handle scales the sprite by its own aspect ratio,
            # so a character is never stretched by a careless resize.
            if stage.height() <= 0:
                return
            fraction = delta.y() / stage.height()
            placement.height = max(
                MIN_SPRITE_FRACTION, round(placement.height + fraction, 4)
            )
        else:
            dx = delta.x() / max(1.0, stage.width())
            dy = delta.y() / max(1.0, stage.height())
            for member in self._drag_group or [placement]:
                member.x = _clamp(member.x + dx)
                member.y = _clamp(member.y + dy)

        self._last_pos = position.toPoint()
        if self.project is not None:
            self.project.mark_dirty()
        self.changed.emit()
        self.update()

    def _move_overlay(self, position: QPointF) -> None:
        """
        Put a dragged caption where the cursor is.

        The grab offset is kept, so the caption does not jump its centre to
        the cursor the moment it is grabbed -- it follows the cursor by the
        same offset it was picked up at, which is what makes the drag feel
        like moving the thing rather than teleporting it.

        A dragged caption gets a free position, which then outranks its
        `position` setting; the panel shows both, so nothing is hidden.
        """
        scene = self.scene
        if scene is None or not 0 <= self._drag_overlay < len(scene.overlays):
            return
        overlay = scene.overlays[self._drag_overlay]
        stage = self._stage_rect()
        if stage.width() <= 0 or stage.height() <= 0:
            return

        overlay.x = _clamp((position.x() - self._grab_offset[0] - stage.left())
                           / stage.width())
        overlay.y = _clamp((position.y() - self._grab_offset[1] - stage.top())
                           / stage.height())
        self._last_pos = position.toPoint()
        if self.project is not None:
            self.project.mark_dirty()
        self.overlay_changed.emit(self._drag_overlay)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_overlay is not None:
            self._drag_overlay = None
            self._last_pos = None
            self.changed.emit()
            return
        if self._drag_index is not None or self._resizing:
            self._drag_index = None
            self._drag_group = []
            self._resizing = False
            self._last_pos = None
            self.changed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Double-click a character to take it off the stage."""
        if self.scene is None:
            return
        position = event.position()
        for index in range(len(self.scene.characters) - 1, -1, -1):
            if self.sprite_rect(self.scene.characters[index]).contains(position):
                del self.scene.characters[index]
                self.select(None)
                if self.project is not None:
                    self.project.mark_dirty()
                self.changed.emit()
                self.update()
                return

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Scroll over a selected sprite to resize it without the handles."""
        placement = self.current()
        if placement is None:
            return
        delta = event.angleDelta().y() / 600.0
        placement.height = max(MIN_SPRITE_FRACTION, round(placement.height + delta, 4))
        if self.project is not None:
            self.project.mark_dirty()
        self.changed.emit()
        self.update()

    # -- drag and drop ----------------------------------------------------
    def choose_narrator(self, key: str) -> None:
        """
        Name the frame's storyteller from a registry key.

        The frame is switched on in the same stroke: a narrator behind a
        switched-off frame draws nothing, which reads as the pick having
        been ignored.  Position, style and the rest are left alone -- the
        author asked for a storyteller, not for a layout.
        """
        if not key:
            return
        already = (
            self.frame.use == key
            and not self.frame.image_file
            and self.frame.enabled
            and self.frame.show_narrator
        )
        self.frame.use = key
        # A key and a picture are two ways to name the same slot: keeping
        # both would leave the renderer reading the picture while the panel
        # shows the key.
        self.frame.image_file = None
        self.frame.enabled = True
        self.frame.show_narrator = True
        if already:
            return
        if self.project is not None:
            self.project.mark_dirty()
        self.changed.emit()
        self.update()

    def choose_narrator_image(self, relative: str) -> None:
        """
        Name the frame's storyteller by picture.

        This is the path a file dropped in `assets/narrators/` takes: no
        registry entry to write, no key to remember -- the file name is the
        identity, and the schema renders `story_frame.image_file` directly.
        """
        if not relative:
            return
        already = (
            self.frame.image_file == relative
            and self.frame.enabled
            and self.frame.show_narrator
        )
        self.frame.image_file = relative
        self.frame.use = None
        self.frame.enabled = True
        self.frame.show_narrator = True
        if already:
            return
        if self.project is not None:
            self.project.mark_dirty()
        self.changed.emit()
        self.update()

    def choose_narrator_payload(self, payload: str) -> str:
        """
        Pick a storyteller from a library row's payload.

        A row carries a registry key when the registry describes it, and
        the picture's path when it is just a file in `assets/narrators/`,
        so the canvas decides which slot to write.  Returns the name to
        report back to the author.
        """
        entry = self.library.registry().get(payload)
        if isinstance(entry, dict) and entry.get("image_file"):
            self.choose_narrator(payload)
            return payload
        self.choose_narrator_image(payload)
        return Path(payload).stem or payload

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if any(event.mimeData().hasFormat(mime) for mime in self.MIMES):
            event.acceptProposedAction()
            self._drop_target = self._stage_rect()
            self.update()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if any(event.mimeData().hasFormat(mime) for mime in self.MIMES):
            event.acceptProposedAction()
            self._drop_target = self._stage_rect()
            self.update()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._drop_target = None
        if self.scene is None:
            return
        mime = event.mimeData()
        if mime.hasFormat(MIME_BACKGROUND):
            relative = bytes(mime.data(MIME_BACKGROUND)).decode("utf-8")
            self.scene.image_file = relative
            self._pixmaps.pop(relative, None)
            if self.project is not None:
                self.project.mark_dirty()
            self.changed.emit()
            self.update()
            event.acceptProposedAction()
            return

        if mime.hasFormat(MIME_NARRATOR):
            payload = bytes(mime.data(MIME_NARRATOR)).decode("utf-8")
            self.choose_narrator_payload(payload)
            event.acceptProposedAction()
            return

        if mime.hasFormat(MIME_CHARACTER):
            relative = bytes(mime.data(MIME_CHARACTER)).decode("utf-8")
            x, y = self._to_fraction(event.position())
            self.scene.characters.append(
                Placement(
                    image_file=relative,
                    x=_clamp(x),
                    y=_clamp(max(y, 0.1)),
                    height=0.3,
                )
            )
            self._pixmaps.pop(relative, None)
            self.select(len(self.scene.characters) - 1)
            if self.project is not None:
                self.project.mark_dirty()
            self.changed.emit()
            self.update()
            event.acceptProposedAction()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


# The option lists the studio offers, read from the domain so the UI cannot
# drift from what the schema accepts.
TRANSITION_CHOICES: tuple[str, ...] = tuple(sorted(TRANSITION_TYPES))
MOTION_CHOICES: tuple[str, ...] = tuple(KEN_BURNS_TYPES)
FRAME_STYLE_CHOICES: tuple[str, ...] = tuple(
    style for style in FRAME_STYLES if style != "custom"
)
ENTER_CHOICES: tuple[str, ...] = tuple(sorted(CHARACTER_ENTER_TYPES))
IDLE_CHOICES: tuple[str, ...] = tuple(sorted(CHARACTER_IDLE_TYPES))
EXIT_CHOICES: tuple[str, ...] = tuple(sorted(CHARACTER_EXIT_TYPES))