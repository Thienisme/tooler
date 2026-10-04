"""
The clock behind the studio's live preview.

The render resolves every cue against the *measured* narration -- stage 2's
reports turn each sentence into real seconds.  The studio has no audio yet,
so it estimates the same timeline the validator estimates: the scene's text
at the TTS rate the project declares, split across its sentences in
proportion to their length.  That is close enough to judge the one question
the preview exists to answer -- "does this scene read the way I meant?" --
and the render stays the source of truth.

Everything here is plain Python and takes fractions of the frame, so it can
be tested without a QApplication and reused by both the canvas (what to draw
at `progress`) and the window (how long a Play run takes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from autovid.domain.sentences import split_sentences

from .store import Impact, Move, Placement, Scene, TextOverlay

# The same reading rate `application/validate.py` estimates with, so the
# scrub's idea of "how long this scene runs" matches the validator's.
CHARS_PER_SECOND = 14.0

# A scene is never shorter than this, however thin its text: entrance and
# exit animations still need room to be visible while scrubbing.
MIN_USABLE_S = 3.0

# Where the preview parks when a scene is loaded: just after everyone has
# arrived, so a freshly dragged character is on screen instead of hanging
# off its own entrance.
SETTLE_MARGIN_S = 0.15

# Size curves, mirrored from infrastructure/video/sprites.py so the preview
# grows a sprite the way the render will.
ENTER_SERIES: dict[str, tuple[float, ...]] = {
    "pop": (0.15, 0.5, 0.82, 1.06, 1.0),
    "zoom_in": (0.3, 0.55, 0.78, 1.0),
}
EXIT_SERIES: dict[str, tuple[float, ...]] = {
    "shrink_out": (1.0, 0.86, 0.6, 0.32, 0.04),
    "zoom_out": (1.0, 0.92, 0.74, 0.5, 0.24, 0.04),
}

# The share of a drop that is the fall; the rest is the bounce.
DROP_FALL_SHARE = 0.65
# How high the bounce lifts the sprite, as a fraction of its own height.
DROP_BOUNCE_SHARE = 0.12

# An idle's amplitude is written in pixels, because that is what the
# renderer's position expressions speak in.  The preview works in frame
# fractions, so the pixels are divided by the frame the project delivers
# (1920x1080) -- the same numbers the render will use, not a guess at the
# widget size on screen.
IDLE_PIXEL_WIDTH = 1920.0
IDLE_PIXEL_HEIGHT = 1080.0


def ease(progress: float, kind: str) -> float:
    """
    Smooth a clamped 0..1 walk ramp the way the renderer smooths it.

    A walk only reads as a walk if it starts and stops gently; a linear
    slide looks like the sprite is being dragged across the screen.

    These lines and `_ease` in the sprite planner are the same curve written
    twice, so they have to be changed together: if they drift, the Studio
    previews a walk the finished video will not perform.
    """
    value = max(0.0, min(1.0, progress))
    if kind == "linear":
        return value
    if kind == "out":
        # Quadratic ease-out, to match the renderer. `1-(1-p)^3` expands to
        # exactly `3p-3p^2+p^3`, so a cubic "out" is not a quicker walk at
        # all -- it is the in/out curve under a second name.
        return 1.0 - (1.0 - value) ** 2
    # Smoothstep, to match the renderer: slope zero at both ends.
    return 3.0 * value**2 - 2.0 * value**3


def _series(values: tuple[float, ...], progress: float) -> float:
    """Straight-line interpolation through a size curve's steps."""
    if progress <= 0.0:
        return values[0]
    if progress >= 1.0:
        return values[-1]
    position = progress * (len(values) - 1)
    index = int(position)
    fraction = position - index
    return values[index] * (1.0 - fraction) + values[index + 1] * fraction


def _edge_offset(
    edge: str, x: float, y: float, width: float, height: float, amount: float
) -> tuple[float, float]:
    """
    The (dx, dy) that carries a sprite `amount` of the way off `edge`.

    `x`/`y` are the sprite's centre and foot line in frame fractions, and
    `width`/`height` its size, so `amount=1.0` parks it just outside the
    frame -- which is where an entrance travels in from and an exit travels
    out to.
    """
    dx = 0.0
    dy = 0.0
    if edge in ("left", "top_left", "bottom_left"):
        dx = -(x + width / 2.0)
    elif edge in ("right", "top_right", "bottom_right"):
        dx = (1.0 + width / 2.0) - x
    if edge in ("top", "top_left", "top_right"):
        dy = -y
    elif edge in ("bottom", "bottom_left", "bottom_right"):
        dy = 1.0 - y + height
    return dx * amount, dy * amount


@dataclass(frozen=True)
class ActorState:
    """Where one character stands, and how it is being drawn, at a moment."""

    x: float
    flip: bool = False
    opacity: float = 1.0
    scale: float = 1.0
    # Frame-fraction offsets: entrances and exits travel, the walk does not.
    dx: float = 0.0
    dy: float = 0.0
    rotation: float = 0.0


@dataclass(frozen=True)
class OverlayState:
    """A caption's appearance at a moment, or None when it is not on screen."""

    text: str
    opacity: float = 1.0
    dx: float = 0.0


class SceneTimeline:
    """
    An estimated clock for one scene: how long it runs and when things happen.

    Sentence anchors resolve against estimated windows instead of stage 2's
    measured ones, and everything drops out of `span`, which is what the
    scrubber's 0..100 maps onto.
    """

    def __init__(self, scene: Scene, speed: float = 1.0) -> None:
        self.scene = scene
        self.speed = speed if speed > 0 else 1.0
        rate = max(CHARS_PER_SECOND * self.speed, 1.0)
        self.narration_s = len(scene.text or "") / rate
        # The words stretch to fill at least MIN_USABLE_S, and any pause the
        # scene asks for extends the clock past them -- matching the render,
        # where an open-ended cue runs to the clip's end.
        self.usable_s = max(MIN_USABLE_S, self.narration_s)
        self.pause_s = max(0, scene.pause_after_ms) / 1000.0
        self.span = self.usable_s + self.pause_s
        self.windows = self._sentence_windows()

    # -- sentences --------------------------------------------------------
    def _sentence_windows(self) -> list[tuple[float, float]]:
        """Estimated scene-local start/end seconds for every sentence."""
        parts = split_sentences(self.scene.text or "")
        if not parts:
            return []
        total = sum(len(part) for part in parts) or 1
        windows: list[tuple[float, float]] = []
        cursor = 0.0
        for part in parts:
            length = self.usable_s * len(part) / total
            windows.append((cursor, cursor + length))
            cursor += length
        return windows

    def window_start(self, sentence: int) -> float | None:
        """Where an estimated sentence begins, or None if there is no such one."""
        position = sentence - 1
        if 0 <= position < len(self.windows):
            return self.windows[position][0]
        return None

    def seconds(self, progress: float) -> float:
        """The scene time the scrubber's 0..1 stands for."""
        return max(0.0, min(1.0, progress)) * self.span

    # -- cues -------------------------------------------------------------
    def move_start(self, move: Move) -> float:
        """
        When a walk stop begins.

        A stop anchored to a sentence uses that sentence's estimated start; a
        sentence the estimate cannot find (or a script with no text yet)
        falls back to the stop's own millisecond offset, exactly the way the
        domain resolves a cue with no measured windows.
        """
        if move.at_sentence is not None:
            start = self.window_start(move.at_sentence)
            if start is not None:
                return start
        if move.at_offset_ms is not None:
            return max(0.0, move.at_offset_ms) / 1000.0
        return 0.0

    def cue_window(self, placement: Placement) -> tuple[float, float]:
        """When one character is on screen: [start, end] in scene seconds."""
        start = 0.0
        if placement.at_sentence is not None:
            found = self.window_start(placement.at_sentence)
            if found is not None:
                start = found
        end = self.span
        if placement.for_sentences is not None:
            base = (placement.at_sentence or 1) - 1
            last = base + max(1, placement.for_sentences) - 1
            if 0 <= last < len(self.windows):
                end = self.windows[last][1]
        return start, max(start, end)

    def impact_offset(self) -> float:
        """When the punch-in fires, in scene seconds."""
        impact: Impact = self.scene.impact
        if impact.at_sentence is not None:
            found = self.window_start(impact.at_sentence)
            if found is not None:
                return found
            if self.windows:
                # A sentence that does not exist fires on the last beat the
                # scene has, which is what the renderer's own fallback does.
                return self.windows[-1][0]
        return max(0.0, impact.at_offset_ms) / 1000.0

    # -- motion -----------------------------------------------------------
    def walk_position(self, placement: Placement, seconds: float) -> tuple[float, bool]:
        """
        The character's x and facing at `seconds`, walk included.

        Every stop contributes the distance from the previous one, ramped by
        its own eased progress -- the same sum the renderer's walk expression
        builds, so the preview and the finished video walk a spot the same
        way.  A stop that names a facing turns the character when the stop
        begins and holds it, which is exactly when the render turns it.
        """
        x, flip, _lean = self.walk_pose(placement, seconds)
        return x, flip

    def walk_pose(
        self, placement: Placement, seconds: float
    ) -> tuple[float, bool, float]:
        """
        x, facing and body lean (degrees) at `seconds`.

        The lean is the renderer's walk sway worked out in closed form: the
        same remaining-distance weighting and the same square-root reach, so
        an author tuning `sway_deg` against this preview tunes the same thing
        the video will do.  Zero outside every walk, which is why the
        character stands upright whenever it is not moving.
        """
        x = placement.x
        previous = placement.x
        flip = placement.flip
        lean = 0.0
        for move in placement.moves:
            start = self.move_start(move)
            if move.flip is not None and seconds >= start:
                flip = move.flip
            duration = max(move.travel_ms / 1000.0, 1e-3)
            progress = max(0.0, min(1.0, (seconds - start) / duration))
            ramp = ease(progress, move.ease)
            distance = move.x - previous
            x += distance * ramp
            previous = move.x

            sway = max(0.0, float(move.sway_deg))
            if sway <= 0.0 or abs(distance) < 0.01:
                continue
            reach = min(1.0, (abs(distance) / 0.4) ** 0.5)
            # The lean eases in and out over the stop and peaks at exactly
            # `sway_deg` mid-stride -- the same `sin` envelope the renderer's
            # walk lean expression uses, so the preview and the video agree.
            direction = 1.0 if distance > 0 else -1.0
            lean += sway * reach * direction * math.sin(math.pi * progress)
        return x, flip, lean

    def idle_sway(
        self, placement: Placement, seconds: float, start: float
    ) -> tuple[float, float, float]:
        """
        What the standing idle adds at `seconds`: x offset, y offset (both
        as fractions of the frame) and a lean in degrees.

        Every curve here is the one infrastructure/video/sprites.py builds
        for the same idle, with the same amplitudes and the same origin --
        the cue's own start, so a character that has just walked in and one
        that has been standing there both sway in phase with the video --
        which is the whole point: an author tuning an amplitude against
        this preview is tuning the render.

        The rotational idles (`tilt`, `lean`) contribute no offset at all:
        they turn the sprite and leave the box where it is.
        """
        kind = placement.idle_type
        amplitude = max(int(placement.idle_amplitude_px), 0)
        if kind == "none" or amplitude <= 0:
            return 0.0, 0.0, 0.0

        period = max(float(placement.idle_period_s), 0.2)
        phase = 2.0 * math.pi * (seconds - start) / period
        # `bob`-shaped idles swing 0..amplitude rather than -amplitude
        # ..+amplitude, so the half keeps `amplitude` the real peak.
        half = 0.5 * (1.0 - math.cos(phase))

        dx = dy = 0.0
        lean = 0.0

        if kind == "bob":
            dy = -amplitude * half
        elif kind == "sway":
            dx = amplitude * math.sin(phase)
        elif kind == "bob_sway":
            dy = -amplitude * half
            # The x half is phase-shifted against the y half, which is what
            # turns one bob into a figure-eight instead of a bounce.
            dx = round(amplitude * 0.7) * math.sin(phase + 1.1)
        elif kind == "shake":
            # A nervous tremble: the stored period is the envelope, the
            # tremor itself is several times faster than that.
            fast = 2.0 * math.pi * (seconds - start) / max(period / 4.0, 0.05)
            dx = amplitude * math.sin(fast)
            dy = round(amplitude * 0.6) * math.sin(fast)
        elif kind == "talk":
            # A short quick bob, faster than `bob`, so it reads as speech
            # rather than as breathing.
            talk = min(max(period, 0.2), 0.5)
            dy = (
                -round(amplitude * 0.6)
                * abs(math.sin(math.pi * (seconds - start) / talk))
            )
        elif kind == "tilt":
            lean = amplitude * half
        elif kind == "lean":
            # The standing sway: left and right around the character's own
            # feet, upright again at the start of every cycle.
            lean = amplitude * math.sin(phase)

        return (
            dx / IDLE_PIXEL_WIDTH,
            dy / IDLE_PIXEL_HEIGHT,
            lean,
        )

    def actor_state(
        self,
        placement: Placement,
        seconds: float,
        sprite: tuple[float, float] = (0.0, 0.0),
    ) -> ActorState | None:
        """
        How one character is drawn at `seconds`, or None when it is not there.

        None covers both ends of a cue: before it starts (a character waiting
        for its sentence) and after it has finished leaving.
        """
        start, end = self.cue_window(placement)
        if seconds < start or seconds > end:
            return None

        x, flip, lean = self.walk_pose(placement, seconds)
        state = ActorState(x=x, flip=flip)

        # The idle runs for the whole cue, under the walk and under the
        # entrance, exactly as it does in the render: the character is
        # breathing the entire time it is on screen, not only once it has
        # finished arriving.
        idle_dx, idle_dy, idle_lean = self.idle_sway(placement, seconds, start)
        state = replace(state, x=state.x + idle_dx, dy=state.dy + idle_dy)

        enter_s = self.enter_seconds(placement)
        spinning = False
        if enter_s > 0.0:
            progress = max(0.0, min(1.0, (seconds - start) / enter_s))
            state = self._enter(state, placement, progress, sprite)
            # Only while the entrance is actually turning: a spin that has
            # finished must hand the angle back to the idle.
            spinning = progress < 1.0 and placement.enter_type in (
                "spin_in",
                "spin_out",
            )

        exit_s = self.exit_seconds(placement)
        if exit_s > 0.0:
            exit_start = max(start, end - exit_s)
            progress = max(0.0, min(1.0, (seconds - exit_start) / exit_s))
            if progress >= 1.0:
                return None
            state = self._exit(state, placement, progress, sprite)
            spinning = spinning or placement.exit_type in (
                "spin_in",
                "spin_out",
            )

        # The leans are added last, so an entrance spin or an exit shrink
        # cannot silently cancel them.  The walk's lean rides on top of a
        # spin because the render sums them; the idle's does not, because
        # the render's `elif` stands the rotation idle down for the length
        # of a spin -- two rotations of one sprite would otherwise add up
        # into something neither of them asked for.
        if lean:
            state = replace(state, rotation=state.rotation + lean)
        if idle_lean and not spinning:
            state = replace(state, rotation=state.rotation + idle_lean)
        return state

    def enter_seconds(self, placement: Placement) -> float:
        if placement.enter_type == "none":
            return 0.0
        return max(0.0, placement.enter_duration_ms / 1000.0)

    def exit_seconds(self, placement: Placement) -> float:
        if placement.exit_type == "none":
            return 0.0
        return max(0.0, placement.exit_duration_ms / 1000.0)

    def _enter(
        self,
        state: ActorState,
        placement: Placement,
        progress: float,
        sprite: tuple[float, float],
    ) -> ActorState:
        kind = placement.enter_type
        width, height = sprite
        if progress >= 1.0:
            return state

        if kind == "fade_in":
            return replace(state, opacity=state.opacity * progress)
        if kind in ENTER_SERIES:
            return replace(state, scale=state.scale * _series(ENTER_SERIES[kind], progress))

        eased = 1.0 - (1.0 - progress) ** 3
        remaining = 1.0 - eased
        if kind in ("fly_in", "slide_in", "spin_in"):
            if kind == "slide_in":
                remaining = 1.0 - progress
            edge = placement.enter_from or "bottom"
            if edge == "center":
                # "From the middle" is a grow, not a journey.
                return replace(
                    state,
                    scale=state.scale * _series((0.3, 0.55, 0.78, 1.0), progress),
                )
            dx, dy = _edge_offset(edge, state.x, placement.y, width, height, remaining)
            rotation = -360.0 * (1.0 - eased) if kind == "spin_in" else 0.0
            return replace(state, dx=dx, dy=dy, rotation=rotation)
        if kind == "drop_bounce":
            return replace(state, dy=self._drop_offset(placement, sprite, progress))
        return state

    def _exit(
        self,
        state: ActorState,
        placement: Placement,
        progress: float,
        sprite: tuple[float, float],
    ) -> ActorState:
        kind = placement.exit_type
        width, height = sprite

        if kind == "fade_out":
            return replace(state, opacity=state.opacity * (1.0 - progress))
        if kind in EXIT_SERIES:
            return replace(state, scale=state.scale * _series(EXIT_SERIES[kind], progress))

        eased = 1.0 - (1.0 - progress) ** 3
        if kind in ("fly_out", "slide_out", "spin_out"):
            # An exit travels the other way: at its start the character is
            # still on its mark, and the offset grows until it is clear.
            amount = progress if kind == "slide_out" else eased
            edge = placement.exit_to or "bottom"
            if edge == "center":
                return replace(
                    state,
                    scale=state.scale * _series((1.0, 0.78, 0.55, 0.3, 0.05), progress),
                )
            dx, dy = _edge_offset(edge, state.x, placement.y, width, height, amount)
            rotation = 360.0 * eased if kind == "spin_out" else 0.0
            return replace(state, dx=dx, dy=dy, rotation=rotation)
        if kind == "drop_out":
            # A fall accelerates, so the offset grows with the square.
            _, dy = _edge_offset(
                placement.exit_to or "bottom",
                state.x,
                placement.y,
                width,
                height,
                progress**2,
            )
            return replace(state, dy=dy)
        return state

    def _drop_offset(
        self, placement: Placement, sprite: tuple[float, float], progress: float
    ) -> float:
        """The vertical offset of a drop in: fall fast, then bounce to rest."""
        _, height = sprite
        fall = min(1.0, progress / DROP_FALL_SHARE)
        # Accelerating fall from above the frame, landing at the foot line.
        offset = -placement.y * (1.0 - fall**2)
        if progress > DROP_FALL_SHARE:
            bounce = (progress - DROP_FALL_SHARE) / (1.0 - DROP_FALL_SHARE)
            # One positive hump: the sprite lifts off the ground and settles.
            offset = -DROP_BOUNCE_SHARE * height * math.sin(math.pi * bounce)
        return offset

    # -- overlays ---------------------------------------------------------
    def overlay_state(self, overlay: TextOverlay, seconds: float) -> OverlayState | None:
        """
        A caption's appearance at `seconds`, or None when it is not showing.

        Mirrors the renderer's alpha fades: `fade_in`/`pop` fade in, every
        animation but `none` fades out again, and `typewriter` reveals its
        characters as it goes.
        """
        start = max(0.0, overlay.start_offset_ms) / 1000.0
        end = max(start, overlay.end_offset_ms) / 1000.0
        if seconds < start or seconds > end:
            return None

        animation = overlay.animation
        if animation == "none":
            return OverlayState(text=overlay.text)

        window = min(
            max(overlay.animation_duration_ms / 1000.0, 0.05), max(end - start, 1e-3) / 2.0
        )
        text = overlay.text
        opacity = 1.0
        dx = 0.0
        if seconds < start + window:
            progress = (seconds - start) / window
            if animation == "typewriter":
                text = text[: int(round(len(text) * progress))]
            elif animation == "slide_in":
                # Slides in from the left edge, like the renderer's offset.
                dx = -(1.0 - progress)
            else:
                # `fade_in` and `pop` both read as a fade at preview size.
                opacity = progress
        if seconds > end - window:
            opacity = min(opacity, max(0.0, (end - seconds) / window))
        return OverlayState(text=text, opacity=opacity, dx=dx)

    # -- the frame the studio parks on ------------------------------------
    def design_seconds(self, placements: list[Placement]) -> float:
        """
        The moment the preview parks at when a scene is loaded.

        Time zero is the truth of a film -- characters are still arriving --
        but it is a useless thing to show an author who just dragged a
        character in, so the studio opens just after everyone has finished
        entering and before the first one starts leaving.  When a scene
        stages those two demands on top of each other (one character exits
        before a later one arrives) the middle is the most honest answer.
        """
        if not placements:
            return min(self.span, SETTLE_MARGIN_S)

        settled = 0.0
        first_exit: float | None = None
        for placement in placements:
            start, end = self.cue_window(placement)
            settled = max(settled, start + self.enter_seconds(placement))
            exit_s = self.exit_seconds(placement)
            if exit_s > 0.0:
                exit_start = max(start, end - exit_s)
                first_exit = exit_start if first_exit is None else min(first_exit, exit_start)

        design = settled + SETTLE_MARGIN_S
        if first_exit is not None and design > first_exit:
            design = (settled + first_exit) / 2.0
        return max(0.0, min(design, self.span))
