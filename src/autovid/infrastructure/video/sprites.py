"""
Character layers: a cartoon host composited over the background.

Why a sprite is *baked* before ffmpeg sees it
---------------------------------------------
Three of the four things an animated character needs are native ffmpeg
motion: position (`overlay` x/y expressions), rotation (`rotate` with an
angle expression) and opacity (`fade=alpha=1`).  **Size is not.**  The
`scale` filter takes no timeline variables and a stream may not change
dimensions mid-render, so an animated size has to exist before the graph is
built.

Size is therefore baked, the same way the typewriter reveal bakes growing
text prefixes: a `pop` is five pre-scaled PNGs, each shown for its slice of
the entrance.  Every step is pasted onto an *identically sized* canvas --
the box -- with the character's feet on the same baseline, so the composite
position is one number for the whole cue and the steps only differ in how
much of that box the character fills.  A short alpha ramp between steps
turns the stack into a blend rather than a flicker.

The box is not the character.  It carries headroom for the motion: 10% for
travel, and 45% when the cue spins, because a rotating sprite needs its
diagonal to fit or its corners get sheared off.

Everything here is pure geometry plus PIL.  The ffmpeg statements are built
in `filters.py`, which is also where the input order is fixed, so this
module never needs to know how many text overlays share the graph.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw

from autovid.domain.characters import CharacterCue
from autovid.domain.script import (
    IDLE_DEGREE_LIMITS_DEGREES,
    CharacterIdle,
)

# Headroom around the character inside its box.
FRAME_PAD = 1.10
# A rotating sprite sweeps its diagonal through the box, so it needs more.
SPIN_PAD = 1.45

# A tilt keeps the box: a few degrees of wobble never leave the padding a
# non-spinning box already carries, so the sprite is not baked with the
# extra diagonal headroom a full spin needs.
TILT_PAD = FRAME_PAD

# Overlap between consecutive size steps, as a share of the step length.
# The ramp has to finish before the step it belongs to does, and the step
# has to outlive the *next* ramp, or a single frame lands with no layer at
# full opacity and the character flickers halfway through its own pop.
STEP_FADE_SHARE = 0.5
MAX_STEP_FADE_S = 0.08

# The fall occupies the first part of a drop, the hops the rest.
DROP_FALL_SHARE = 0.6
DROP_HOP_HEIGHT = 0.22

# Size curves.  `pop` overshoots and settles, which is what makes it read
# as a cartoon entrance rather than as a zoom.
ENTER_SCALES: dict[str, tuple[float, ...]] = {
    "pop": (0.15, 0.5, 0.82, 1.06, 1.0),
    "zoom_in": (0.3, 0.55, 0.78, 1.0),
}
EXIT_SCALES: dict[str, tuple[float, ...]] = {
    "shrink_out": (1.0, 0.86, 0.6, 0.32, 0.04),
    "zoom_out": (1.0, 0.92, 0.74, 0.5, 0.24, 0.04),
}

# How each scripted motion is realised.  `series` bakes sizes, `travel`
# moves the box, `drop` falls and bounces, `hold` is the plain case.
ENTER_KINDS: dict[str, dict] = {
    "none": {"kind": "hold"},
    "fade_in": {"kind": "hold", "fade_in": True},
    "pop": {"kind": "series", "scales": ENTER_SCALES["pop"]},
    "zoom_in": {"kind": "series", "scales": ENTER_SCALES["zoom_in"]},
    "fly_in": {"kind": "travel", "ease": "out"},
    "slide_in": {"kind": "travel", "ease": "linear"},
    "drop_bounce": {"kind": "drop"},
    "spin_in": {"kind": "travel", "ease": "out", "spin": "in"},
}

EXIT_KINDS: dict[str, dict] = {
    "none": {"kind": "hold"},
    "fade_out": {"kind": "hold", "fade_out": True},
    "shrink_out": {"kind": "series", "scales": EXIT_SCALES["shrink_out"]},
    "zoom_out": {"kind": "series", "scales": EXIT_SCALES["zoom_out"]},
    "fly_out": {"kind": "travel", "ease": "out"},
    "slide_out": {"kind": "travel", "ease": "linear"},
    "drop_out": {"kind": "drop_out"},
    "spin_out": {"kind": "travel", "ease": "out", "spin": "out"},
}

SPINNING_ENTER = frozenset({"spin_in"})
SPINNING_EXIT = frozenset({"spin_out"})


@dataclass(frozen=True)
class SpriteFrame:
    """One baked PNG and the geometry it was pasted with."""

    path: Path
    box_w: int
    box_h: int
    content_w: int
    content_h: int
    feet_inset_px: int
    centre_inset_px: int
    # Where the artwork came from: a mouth variant re-bakes the resting
    # sprite with a patch on, so it needs the original path again.
    source_path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "file": str(self.path),
            "box": [self.box_w, self.box_h],
            "content": [self.content_w, self.content_h],
            "feet_inset_px": self.feet_inset_px,
        }


@dataclass(frozen=True)
class CharacterLayer:
    """
    One baked PNG with its own slice of the cue.

    A cue is one layer for a travel, spin, fade or drop, and a stack of
    layers for a size animation.  Each layer carries its own visible window,
    so the alpha ramps inside one filtergraph stay independent.
    """

    frame: SpriteFrame
    start_s: float
    end_s: float
    box_x: int = 0
    box_y: int = 0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0
    # "none" | "out" (arrive from off-screen) | "in" (leave the frame)
    # | "drop" (fall in and bounce) | "drop_out" (fall out of frame)
    travel: str = "none"
    travel_edge: str = "bottom"
    travel_s: float = 0.0
    travel_x: int = 0
    travel_y: int = 0
    travel_ease: str = "out"
    spin: str = "none"
    spin_s: float = 0.0
    idle: CharacterIdle | None = None
    idle_origin_s: float = 0.0
    # Windows *inside* this layer's own span where the layer must not be
    # drawn: the times a pose/talk variant owns the frame.  The resting
    # sprite stays enabled underneath otherwise, and a variant whose
    # silhouette is smaller than the resting art composites over it -- two
    # characters showing at once, the old one ghosting through the new one.
    hide_windows: tuple[tuple[float, float], ...] = ()
    # The inverse idea: instead of one layer per short window (a mouth flap
    # would be fifty inputs a scene), ONE layer per distinct artwork shows
    # itself during every listed window through a summed enable expression.
    show_windows: tuple[tuple[float, float], ...] = ()
    # Where the cue's walk wants the sprite, relative to `box_x`/`box_y` in
    # output pixels.  One expression covering every stop, added to the
    # position the way the entrance travel is.
    walk_x: str = ""
    walk_y: str = ""
    # The stretches of this cue where the character faces the other way.
    # Each entry is (start_s, end_s, facing) and the windows are *disjoint
    # and exhaustive* over the cue: `facing` is what the sprite shows for
    # that whole stretch, so the renderer can simply flip inside those
    # windows.  A window that only covered the walk itself would put the
    # character back the way it started the moment it arrived, which reads
    # as the turn being cancelled.
    walk_flips: tuple[tuple[float, float, bool], ...] = ()
    # The rotation, in radians, that the body leans into the direction of
    # travel while a walk is under way.  Zero outside every walk, so the
    # character stands upright the rest of the cue.  Additive with the spin
    # and the tilt idle: they are all rotations of the same sprite, and
    # adding them is what keeps an entrance from cancelling the lean.
    walk_angle: str = ""

    @property
    def box_w(self) -> int:
        return self.frame.box_w

    @property
    def box_h(self) -> int:
        return self.frame.box_h

    @property
    def tilts(self) -> bool:
        """Whether the idle oscillates rotation (the box never moves for it)."""
        return (
            self.idle is not None
            and self.idle.type in IDLE_DEGREE_LIMITS_DEGREES
        )

    def to_dict(self) -> dict:
        return {
            "file": str(self.frame.path),
            "start_s": round(self.start_s, 4),
            "end_s": round(self.end_s, 4),
            "fade_in_s": round(self.fade_in_s, 4),
            "fade_out_s": round(self.fade_out_s, 4),
            "travel": self.travel,
            "show_windows": [list(w) for w in self.show_windows],
            "travel_edge": self.travel_edge,
            "travel_s": round(self.travel_s, 4),
            "travel_px": [self.travel_x, self.travel_y],
            "spin": self.spin,
            # Amplitude and period change the position expression without
            # changing the type, so they belong in the cache signature too:
            # without them a retuned idle would silently reuse the old clip.
            "idle": self.idle.type if self.idle else "none",
            "idle_amplitude_px": self.idle.amplitude_px if self.idle else 0,
            "idle_period_s": self.idle.period_s if self.idle else 0.0,
            "position": [self.box_x, self.box_y],
            "box": [self.frame.box_w, self.frame.box_h],
            # The walk changes the position expression without changing any
            # artwork, so it belongs in the cache signature: without it a
            # retimed walk would silently reuse the previous clip.
            "walk_x": self.walk_x,
            "walk_y": self.walk_y,
            "walk_flips": [list(w) for w in self.walk_flips],
            # The lean changes the rotate expression without touching the
            # position, so a retuned sway must invalidate the cached clip too.
            "walk_angle": self.walk_angle,
            # The hide windows change the enable expression without touching
            # anything else in the layer, so they belong in the signature a
            # filtergraph cache keys on.
            "hide_windows": [
                [round(s, 4), round(e, 4)] for s, e in self.hide_windows
            ],
        }

    # -- expressions ------------------------------------------------------

    def x_expression(self) -> str:
        return _position_expression(self, axis="x")

    def y_expression(self) -> str:
        return _position_expression(self, axis="y")

    def angle_expression(self) -> str | None:
        """Rotation in radians, or None when the layer never rotates.

        A layer can be rotating for more than one reason at once -- spinning
        in through the door while leaning into its first stride, say -- so
        the reasons are summed rather than chosen between.  Only a layer that
        would genuinely never turn returns None, which is what tells the
        filter graph it can leave the `rotate` out entirely.
        """
        terms: list[str] = []
        if self.spin != "none" and self.spin_s > 0:
            progress = _progress(self.start_s, self.spin_s)
            if self.spin == "in":
                # A whole turn that lands facing the viewer.
                terms.append(f"-2*PI*(1-{progress})")
            else:
                terms.append(f"2*PI*{progress}")
        elif self.tilts:
            # A rotational idle is the resting posture; an in-flight spin owns
            # the angle outright, so summing them would fight for the same
            # frame.
            rotation = _tilt_expression(self) or _lean_expression(self)
            if rotation:
                terms.append(rotation)
        if self.walk_angle:
            terms.append(self.walk_angle)
        if not terms:
            return None
        return "+".join(terms)


def _number(value: float) -> str:
    """Compact fixed-point number for a filter argument."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _progress(start_s: float, duration_s: float) -> str:
    """Clamped 0..1 progress from `start_s` over `duration_s`."""
    duration = max(duration_s, 1e-3)
    return f"min(max((t-{_number(start_s)})/{_number(duration)},0),1)"


def _ease(progress: str, kind: str) -> str:
    """
    Smooth a clamped progress term.

    A walk only reads as a walk if it starts and stops gently; a linear
    slide looks like the sprite being dragged across the screen.

    The two curves are genuinely different, which for a while they were not:

    * `in_out` is the true smoothstep `3p^2-2p^3`, whose slope is zero at
      *both* ends.  The character eases off the mark and plants the final
      foot, which is the walk that reads as steps rather than as a shove.
    * `out` is the quadratic ease-out `1-(1-p)^2`: it leaves at speed and
      decelerates into place, for a character that hurries across the stage
      and then sets its feet.

    An earlier version used `3p-3p^2+p^3` for `in_out` and `1-(1-p)^3` for
    `out`.  Those are the *same* curve -- the second expands to exactly the
    first -- so the two options did nothing different at all.  Worse, the
    curve they shared has a slope of 3.0 at p=0, so every walk in the
    project lurched off its mark at three times the average speed and
    crawled the rest of the way.  That, rather than the duration, is why a
    walk read as the character being hurried across the stage.
    """
    if kind == "linear":
        return progress
    if kind == "out":
        # Front-loaded: leaves at speed, decelerates to a stop.
        return f"(1-pow(1-{progress},2))"
    # Smoothstep: slope zero at p=0 and at p=1.
    return f"(3*pow({progress},2)-2*pow({progress},3))"


def _walk_expression(
    cue,
    *,
    axis: str,
    frame_size: tuple[int, int],
    box_w: int,
) -> str:
    """
    The offset a cue's walk adds to its position, in output pixels.

    Each stop contributes the distance from the previous stop, ramped by its
    own eased progress.  Summing the ramps is what makes a multi-stop walk
    work: before its stop a term reads zero, during it eases in, and after
    it holds -- so the character walks a path through every place in turn
    without the expression ever needing to know which stop is "current".

    The horizontal distance is measured between sprite *centres*, while the
    box was placed from the cue's starting x, so once every ramp has
    completed the sum telescopes onto the last stop's position.
    """
    stops = tuple(getattr(cue, "moves", ()) or ())
    if not stops:
        return ""

    frame_w, frame_h = frame_size
    origin_x = cue.x * frame_w - box_w / 2.0
    origin_y = cue.y * frame_h

    terms: list[str] = []
    previous_x = origin_x
    previous_y = origin_y
    for stop in stops:
        if axis == "x":
            target = stop.x * frame_w - box_w / 2.0
            distance = target - previous_x
            previous_x = target
        else:
            # Only a stop that names its own ground line moves vertically;
            # otherwise the character walks along the floor it started on.
            target = origin_y if stop.y is None else stop.y * frame_h
            distance = target - previous_y
            previous_y = target

        if abs(distance) < 0.5:
            continue
        ramp = _ease(_progress(stop.start_s, stop.duration_s), stop.ease)
        magnitude = _number(abs(distance))
        sign = "-" if distance < 0 else ""
        terms.append(f"{sign}{magnitude}*({ramp})")

    if not terms:
        return ""
    return "+".join(terms)


def _walk_angle_expression(cue) -> str:
    """
    The rotation that makes a sliding sprite read as walking.

    A cut-out dragged across the floor looks like a sticker being moved; a
    body that leans the way it is travelling looks like a person taking a
    step.  So each stop contributes a lean into its own direction of travel,
    scaled by the stop's own `sway_deg`.

    Three details make it read as a step rather than a tilt:

    * The lean rises and falls with a `sin` envelope over the stop, so the
      character eases into the stride and stands back up on arrival.  It is
      not simply proportional to the eased position progress: that curve
      starts and ends at zero, so a lean built on it would never reach the
      angle the author asked for.
    * The lean grows with the square root of how far the walk is, because a
      character crossing the whole stage takes longer strides and leans more
      than one shifting a hand's width.
    * The lean is authored in *screen* space but the `rotate` filter runs
      before the turn's `hflip`, so a character showing its mirrored
      artwork has to lean the opposite way in baked space to appear to lean
      into its own direction of travel on screen.  Facing it wrong makes the
      character lean back the way it came, which is the one thing this
      effect must never do.
    """
    stops = tuple(getattr(cue, "moves", ()) or ())
    if not stops:
        return ""

    baked_facing = bool(getattr(cue, "flip", False))
    terms: list[str] = []

    # The horizontal distance is measured in frame fractions, the same unit
    # the position expression walks in, so the two agree on how far is far.
    previous_x = cue.x
    facing = baked_facing
    for stop in stops:
        # The turn happens *when the stop begins*, so this stop's own facing
        # is the one its walk is drawn in -- it has to be applied before the
        # lean is signed, not after.
        if stop.flip is not None:
            facing = bool(stop.flip)
        distance = stop.x - previous_x
        previous_x = stop.x
        if abs(distance) < 0.01 or stop.sway_rad <= 0.0:
            continue
        progress = _progress(stop.start_s, stop.duration_s)
        # The lean has to be zero outside the walk and zero at both ends of
        # it, or a finished walk would leave the character standing at an
        # angle -- and because these terms sum, every later stop would tilt
        # it further.  `sin(PI*p)` is the cheapest envelope that does both
        # and peaks at exactly 1, which is what makes `sway_deg` mean the
        # angle the author actually asked for instead of a fraction of it.
        envelope = f"sin(PI*{progress})"
        # Longer walks lean further, but with diminishing returns -- sqrt
        # keeps a full-stage crossing from looking like a bow.
        reach = min(1.0, (abs(distance) / 0.4) ** 0.5)
        # `facing != baked` is exactly the stretch the renderer mirrors, and
        # `hflip` runs after `rotate`, so the baked-space lean is the
        # screen-space one inverted.
        screen_sign = 1.0 if distance > 0 else -1.0
        baked_sign = -screen_sign if facing != baked_facing else screen_sign
        terms.append(
            f"{_number(stop.sway_rad * reach * baked_sign)}*({envelope})"
        )

    if not terms:
        return ""
    return "+".join(terms)


def _walk_flip_windows(
    stops: tuple, *, start_s: float, end_s: float, baked_facing: bool
) -> tuple[tuple[float, float, bool], ...]:
    """
    The stretches of the cue where the character faces the other way.

    A stop may name its own facing, which is what turns a character around
    as it walks past someone.  The turn happens *when the stop begins* and
    then holds: a flip window that ended with the walk would put the
    character back where it started facing the instant it arrived, so the
    turn would look like it had been cancelled.

    The artwork is already baked at `baked_facing` (the cue's own `flip`),
    so only the stretches that ask for the *opposite* facing are returned --
    a window that agreed with the baked artwork would flip it the wrong way.
    """
    turn_stops = [stop for stop in stops if stop.flip is not None]
    if not turn_stops or end_s <= start_s:
        return ()

    # Every instant of the cue is governed by the facing asked for by the
    # last turn stop at or before it, so the turn stops -- plus the cue's
    # own ends -- are the boundaries of the answer.
    edges = [float(start_s)]
    for stop in turn_stops:
        edges.append(max(min(float(stop.start_s), end_s), start_s))
    edges.append(float(end_s))

    windows: list[tuple[float, float, bool]] = []
    for index in range(len(edges) - 1):
        window_start, window_end = edges[index], edges[index + 1]
        if window_end <= window_start:
            continue
        facing = baked_facing
        for stop in turn_stops:
            if stop.start_s <= window_start:
                facing = bool(stop.flip)
        if facing == baked_facing:
            continue
        windows.append(
            (round(window_start, 4), round(window_end, 4), True)
        )

    return tuple(windows)


def _position_expression(layer: CharacterLayer, *, axis: str) -> str:
    terms = [str(layer.box_x if axis == "x" else layer.box_y)]
    travel = _travel_expression(layer, axis=axis)
    if travel:
        terms.append(travel)
    walk = layer.walk_x if axis == "x" else layer.walk_y
    if walk:
        terms.append(walk)
    idle = _idle_expression(layer, axis=axis)
    if idle:
        terms.append(idle)
    return "+".join(
        f"({term})" if term.startswith("-") else term for term in terms
    )


def _travel_expression(layer: CharacterLayer, *, axis: str) -> str:
    if layer.travel == "none" or layer.travel_s <= 0:
        return ""

    distance = layer.travel_x if axis == "x" else layer.travel_y
    if distance == 0:
        return ""

    progress = _progress(layer.start_s, layer.travel_s)
    magnitude = _number(abs(distance))
    sign = "-" if distance < 0 else ""

    if layer.travel == "out":
        # Arriving: starts at the full distance and ends at the rest
        # position.  `ease-out` (the default) front-loads the movement so
        # the character arrives and settles instead of drifting in.
        if layer.travel_ease == "linear":
            return f"{sign}{magnitude}*(1-{progress})"
        return f"{sign}{magnitude}*pow(1-{progress},3)"

    if layer.travel == "in":
        # Leaving: accelerates away, because a departure that eases out
        # looks like the character changed its mind.
        if layer.travel_ease == "linear":
            return f"{sign}{magnitude}*{progress}"
        return f"{sign}{magnitude}*pow({progress},3)"

    if layer.travel == "drop":
        # The fall has to *reach zero* at touchdown and stay there.  An
        # unclamped ramp is negative past the landing point, and squaring a
        # negative brings it back up: the character then hovers above the
        # ground line for the whole rest of the cue (measured, at 228px).
        fall_share = DROP_FALL_SHARE
        hop_height = _number(abs(distance) * DROP_HOP_HEIGHT)
        ramp = f"min(max(({fall_share}-{progress})/{fall_share},0),1)"
        # The bounce phase is clamped too: past the landing it would be
        # evaluated on a negative base, and `pow` there is a nan, which
        # ffmpeg turns into an unpositionable overlay rather than an error.
        bounce = f"min(max(({progress}-{fall_share})/{1 - fall_share},0),1)"
        hop = f"abs(sin(PI*{bounce}))*pow(1-{bounce},1.6)"
        return (
            f"-{magnitude}*pow({ramp},2)"
            f"+{hop_height}*if(gt({progress},{_number(fall_share)}),{hop},0)"
        )

    if layer.travel == "drop_out":
        return f"{sign}{magnitude}*pow({progress},2)"

    return ""


def _tilt_expression(layer: CharacterLayer) -> str | None:
    """
    The rotation wobble for a `tilt` idle, in radians.

    `amplitude_px` carries degrees for this idle type (the schema caps it at
    fifteen so it reads as a head/upper-body sway, not a metronome).  A
    `1-cos` curve starts and ends upright, and eases through the extremes
    the way a relaxed sway does.  `period_s` is the full there-and-back
    cycle, exactly as for `bob`.
    """
    idle = layer.idle
    if idle is None or idle.type != "tilt":
        return None
    degrees = max(idle.amplitude_px, 0)
    if degrees <= 0:
        return None
    origin = _number(layer.idle_origin_s)
    period = _number(max(idle.period_s, 0.2))
    radians = degrees * math.pi / 180.0
    # The 0.5 matches the bob: `1-cos` swings 0..2, so halving it makes
    # `amplitude` the actual peak of the sway, not half of it.
    return f"{radians:.6f}*0.5*(1-cos(2*PI*(t-{origin})/{period}))"


def _lean_expression(layer: CharacterLayer) -> str | None:
    """
    The standing sway for a `lean` idle, in radians.

    This is the whole body pivoting on its feet, so it swings symmetrically:
    `amplitude_px` carries degrees (the schema caps them at forty-five) and
    `sin` puts zero at the cue start and half a cycle away -- the character
    is upright whenever it arrives, and upright again halfway through, which
    is what makes it read as standing there rather than as drifting.  A
    one-sided curve would leave the character cocked over while it waits.

    `period_s` is the full there-and-back cycle, exactly as for `bob`.
    """
    idle = layer.idle
    if idle is None or idle.type != "lean":
        return None
    degrees = max(idle.amplitude_px, 0)
    if degrees <= 0:
        return None
    origin = _number(layer.idle_origin_s)
    period = _number(max(idle.period_s, 0.2))
    radians = degrees * math.pi / 180.0
    return f"{radians:.6f}*sin(2*PI*(t-{origin})/{period})"


def _idle_expression(layer: CharacterLayer, *, axis: str) -> str:
    """
    The oscillation that keeps a still cut-out looking alive.

    Every layer of one cue shares `idle_origin_s`, so the size steps and the
    travel layers oscillate in phase; otherwise the composite would visibly
    come apart during a size animation.
    """
    idle = layer.idle
    if idle is None or idle.type == "none" or idle.amplitude_px <= 0:
        return ""

    amplitude = idle.amplitude_px
    origin = _number(layer.idle_origin_s)
    period = _number(max(idle.period_s, 0.2))

    if idle.type == "bob":
        if axis != "y":
            return ""
        return f"-{amplitude}*0.5*(1-cos(2*PI*(t-{origin})/{period}))"

    if idle.type == "sway":
        if axis != "x":
            return ""
        return f"{amplitude}*sin(2*PI*(t-{origin})/{period})"

    if idle.type == "bob_sway":
        if axis == "y":
            return f"-{amplitude}*0.5*(1-cos(2*PI*(t-{origin})/{period}))"
        return (
            f"{int(round(amplitude * 0.7))}"
            f"*sin(2*PI*(t-{origin})/{period}+1.1)"
        )

    if idle.type == "shake":
        # A nervous tremble: the schema's period is the envelope, the
        # oscillation itself is several times faster than that.
        fast = _number(max(idle.period_s / 4.0, 0.05))
        if axis == "x":
            return f"{amplitude}*sin(2*PI*(t-{origin})/{fast})"
        return (
            f"{int(round(amplitude * 0.6))}"
            f"*sin(2*PI*(t-{origin})/{fast})"
        )

    if idle.type == "talk":
        # A short quick bob, faster than `bob`, so it reads as speech
        # rather than as breathing.
        if axis != "y":
            return ""
        talk_period = _number(min(max(idle.period_s, 0.2), 0.5))
        return (
            f"-{int(round(amplitude * 0.6))}"
            f"*abs(sin(PI*(t-{origin})/{talk_period}))"
        )

    return ""


# --------------------------------------------------------------------------
# baking
# --------------------------------------------------------------------------


def _cache_key(*parts) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def sprite_transparency(path: Path) -> tuple[bool, float]:
    """
    Whether a sprite has an alpha channel, and how much of it is empty.

    A character layer is only usable with transparency: a JPEG or a
    flattened PNG composites as an opaque rectangle over the scene, which
    looks like a mistake in the edit rather than like art.  The empty share
    is returned too, because a fully opaque "transparent" PNG is the case
    that slips through a boolean check.
    """
    with Image.open(path) as image:
        converted = image.convert("RGBA")
        alpha = converted.getchannel("A")
        histogram = alpha.histogram()
        transparent = sum(histogram[:8])
        total = max(sum(histogram), 1)
        return image.mode in ("RGBA", "LA", "PA") or "A" in image.getbands(), transparent / total


def _load_trimmed(path: Path, *, flip: bool) -> Image.Image:
    """
    The character's own pixels, with the transparent margin removed.

    Trimming is what makes `height` mean something: a sprite exported with
    a generous transparent border would otherwise be scaled down by exactly
    that border and appear much smaller than the script asked for.
    """
    sprite = Image.open(path).convert("RGBA")
    if flip:
        sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)

    bounding_box = sprite.getbbox()
    if bounding_box is not None:
        sprite = sprite.crop(bounding_box)
    return sprite


def _content_size(
    sprite: Image.Image, *, height_px: int, scale: float
) -> tuple[int, int]:
    content_h = max(2, int(round(height_px * scale)))
    content_w = max(2, int(round(sprite.width * content_h / sprite.height)))
    return content_w, content_h


def _box_size(
    content_w: int, content_h: int, *, spinning: bool
) -> tuple[int, int]:
    pad = SPIN_PAD if spinning else FRAME_PAD
    box_w = max(int(round(content_w * pad)), content_w + 2)
    box_h = max(int(round(content_h * pad)), content_h + 2)
    return box_w, box_h


def bake_sprite(
    *,
    source: Path,
    height_px: int,
    scale: float = 1.0,
    flip: bool = False,
    spinning: bool = False,
    box: tuple[int, int] | None = None,
    anchor: str = "bottom",
    destination_dir: Path,
    mouth: Path | None = None,
    mouth_anchor: tuple[float, float] = (0.5, 0.5),
    mouth_size: tuple[float, float] = (0.2, 0.1),
) -> SpriteFrame:
    """
    Scale a character to `height_px * scale` and paste it onto its box.

    `box` fixes the canvas so every step of a size animation shares the same
    overlay geometry; omitted, the box is derived from this step.

    `mouth` composites a small opaque patch *onto* the artwork -- the mouth
    flap.  `mouth_anchor` is the patch centre as a fraction of the trimmed
    artwork's content box, `mouth_size` the patch size in the same units;
    both come from tools/cut_mouth_from_frames.py.  A flipped sprite
    mirrors the anchor and the patch, exactly like the body it belongs to.
    """
    sprite = _load_trimmed(source, flip=flip)
    content_w, content_h = _content_size(
        sprite, height_px=height_px, scale=scale
    )
    derived_w, derived_h = _box_size(
        content_w, content_h, spinning=spinning
    )
    box_w, box_h = box if box is not None else (derived_w, derived_h)

    resized = sprite.resize((content_w, content_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))

    left = (box_w - content_w) // 2
    top = (box_h - content_h) // 2 if anchor == "center" else box_h - content_h
    canvas.alpha_composite(resized, (left, top))

    if mouth is not None:
        patch = Image.open(mouth).convert("RGBA")
        if flip:
            patch = patch.transpose(Image.FLIP_LEFT_RIGHT)
        patch_w = max(2, int(round(content_w * mouth_size[0])))
        patch_h = max(2, int(round(content_h * mouth_size[1])))
        patch = patch.resize((patch_w, patch_h), Image.LANCZOS)
        # A feathered edge melts the rectangular patch into the artwork
        # instead of drawing a visible seam around the mouth.
        feather = max(2, min(patch_w, patch_h) // 10)
        alpha = Image.new("L", (patch_w, patch_h), 255)
        alpha_draw = ImageDraw.Draw(alpha)
        for step in range(feather):
            value = int(255 * (step + 1) / feather)
            alpha_draw.rectangle(
                (step, step, patch_w - 1 - step, patch_h - 1 - step),
                outline=value,
            )
        patch.putalpha(alpha)
        anchor_x, anchor_y = mouth_anchor
        if flip:
            anchor_x = 1.0 - anchor_x
        patch_x = left + int(round(content_w * anchor_x)) - patch_w // 2
        patch_y = top + int(round(content_h * anchor_y)) - patch_h // 2
        patch_x = max(0, min(patch_x, box_w - patch_w))
        patch_y = max(0, min(patch_y, box_h - patch_h))
        canvas.alpha_composite(patch, (patch_x, patch_y))

    stat = source.stat()
    key = _cache_key(
        source.resolve(),
        stat.st_size,
        int(stat.st_mtime),
        height_px,
        round(scale, 4),
        flip,
        spinning,
        box_w,
        box_h,
        anchor,
        str(mouth.resolve()) if mouth is not None else "",
        round(mouth_anchor[0], 4),
        round(mouth_anchor[1], 4),
        round(mouth_size[0], 4),
        round(mouth_size[1], 4),
    )
    destination = destination_dir / f"{key}.png"
    if not destination.exists():
        destination_dir.mkdir(parents=True, exist_ok=True)
        canvas.save(destination, format="PNG", compress_level=6)

    return SpriteFrame(
        path=destination,
        box_w=box_w,
        box_h=box_h,
        content_w=content_w,
        content_h=content_h,
        feet_inset_px=top + content_h,
        centre_inset_px=top + content_h // 2,
        source_path=source,
    )


class SpritePlanner:
    """Bakes the layers one cue needs, caching them on disk as it goes."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.fps = 30

    def plan(
        self,
        cue: CharacterCue,
        *,
        source: Path,
        frame_size: tuple[int, int],
        fps: int = 30,
        variants: tuple = (),
    ) -> list[CharacterLayer]:
        """Every composited layer for one cue, in composite order."""
        frame_w, frame_h = frame_size
        self.fps = max(int(fps), 1)
        height_px = max(8, int(round(frame_h * cue.height)))
        spinning = (
            cue.enter.type in SPINNING_ENTER or cue.exit.type in SPINNING_EXIT
        )
        anchor = "center" if spinning else "bottom"

        enter_spec = ENTER_KINDS.get(cue.enter.type, {"kind": "hold"})
        exit_spec = EXIT_KINDS.get(cue.exit.type, {"kind": "hold"})

        enter_s = min(cue.enter.duration_ms / 1000.0, cue.duration_s)
        exit_s = min(cue.exit.duration_ms / 1000.0, cue.duration_s)

        # Who owns the character at each moment.  The layers stack, so a
        # layer that is still opaque covers everything under it: the "held"
        # entrance has to hand over at the instant the departure starts, or
        # the full-size sprite keeps showing through a shrink or a fade and
        # the exit silently does nothing (which is exactly what the first
        # measured render did).
        holds = exit_spec["kind"] == "hold" and not exit_spec.get("fade_out")
        fades_out = exit_spec["kind"] == "hold" and exit_spec.get("fade_out")
        if holds:
            # Nothing happens at the end: one layer covers the whole cue.
            enter_end = cue.end_s
            exit_start: float | None = None
        elif fades_out:
            exit_start = max(cue.end_s - exit_s, cue.start_s)
            enter_end = exit_start
        else:
            # A size series or a travel provides the image from its own
            # start, so the entrance layer stops exactly there.
            exit_start = max(cue.end_s - exit_s, cue.start_s)
            enter_end = exit_start

        # The largest size the cue ever reaches decides the shared box.
        largest = 1.0
        for spec, table in ((enter_spec, ENTER_SCALES), (exit_spec, EXIT_SCALES)):
            if spec["kind"] == "series":
                largest = max(largest, max(spec["scales"]))

        box = self._box_for(
            source,
            height_px=height_px,
            scale=largest,
            spinning=spinning,
            flip=cue.flip,
            anchor=anchor,
        )

        # A pose wider than the resting artwork (arms out, pointing) would
        # be sheared by a box sized for the resting art alone, so the box is
        # widened to fit the widest body swap before anything is baked into
        # it.  Height is untouched: feet stay on the baseline, and a taller
        # pose is the author asking for a different character size.
        body_swaps: tuple = ()
        if variants:
            body_swaps = tuple(
                variant for variant in variants if variant.kind != "mouth"
            )
            widest_variant_px = 0
            for variant in body_swaps:
                sprite = _load_trimmed(
                    Path(variant.image_file),
                    flip=bool(variant.flip) != bool(cue.flip),
                )
                widest_variant_px = max(
                    widest_variant_px,
                    int(round(sprite.width * height_px / sprite.height)),
                )
            if widest_variant_px:
                needed_w = max(
                    int(round(widest_variant_px * FRAME_PAD)),
                    widest_variant_px + 2,
                )
                box = (max(box[0], needed_w), box[1])

        # Where the box sits, in output pixels.  `y` is the ground line the
        # character stands on rather than the top of the sprite, so a cue
        # stays put when its height changes.
        resting = self._bake(
            source,
            height_px=height_px,
            scale=1.0,
            flip=cue.flip,
            spinning=spinning,
            box=box,
            anchor=anchor,
        )
        feet_y = int(round(cue.y * frame_h))
        box_x = int(round(cue.x * frame_w - resting.box_w / 2))
        box_y = feet_y - resting.feet_inset_px

        geometry = {
            "frame_size": (frame_w, frame_h),
            "box_x": box_x,
            "box_y": box_y,
            "box_w": resting.box_w,
            "box_h": resting.box_h,
        }

        layers: list[CharacterLayer] = []

        # The windows a pose/talk variant owns.  The *base* layers (the
        # resting sprite and its size steps) must switch off while a variant
        # is up, or the resting artwork keeps showing through underneath a
        # variant whose silhouette is smaller -- two hosts at once.  A mouth
        # patch is composited *onto* the resting artwork, so its windows are
        # not subtractions and must stay out of this list.
        base_hide_windows = _hide_windows(
            body_swaps, start=cue.start_s, end=enter_end
        )

        # Windows the whole sprite must switch off for, decided above the
        # planner.  Only the persistent host carries these: a scene
        # character landing in its corner suppresses the host instead of
        # compositing underneath it.
        cue_hide_windows: tuple[tuple[float, float], ...] = getattr(
            cue, "hide_windows", ()
        )

        if enter_spec["kind"] == "series":
            layers.extend(
                self._series_layers(
                    source=source,
                    cue=cue,
                    scales=enter_spec["scales"],
                    start_s=cue.start_s,
                    span_s=enter_s,
                    height_px=height_px,
                    spinning=spinning,
                    box=box,
                    anchor=anchor,
                    geometry=geometry,
                    hold_until=enter_end,
                    hide_windows=base_hide_windows + cue_hide_windows,
                )
            )
        else:
            travel_x, travel_y = _travel_distances(
                kind=enter_spec["kind"],
                edge=cue.enter.edge,
                idle_amplitude=cue.idle.amplitude_px,
                **geometry,
            )
            layers.append(
                CharacterLayer(
                    frame=resting,
                    start_s=cue.start_s,
                    end_s=enter_end,
                    box_x=box_x,
                    box_y=box_y,
                    fade_in_s=enter_s if enter_spec.get("fade_in") else 0.0,
                    travel=_travel_kind(enter_spec, phase="enter"),
                    travel_edge=cue.enter.edge,
                    travel_s=enter_s,
                    travel_x=travel_x,
                    travel_y=travel_y,
                    travel_ease=enter_spec.get("ease", "out"),
                    spin=enter_spec.get("spin", "none"),
                    spin_s=enter_s,
                    idle=cue.idle,
                    idle_origin_s=cue.start_s,
                    hide_windows=base_hide_windows + cue_hide_windows,
                )
            )

        if exit_start is None:
            if variants:
                layers.extend(
                    self._variant_layers(
                        variants=variants,
                        cue=cue,
                        resting=resting,
                        anchor=anchor,
                        geometry=geometry,
                    )
                )
            return self._apply_walk(cue, layers, frame_size, box[0])

        if variants:
            layers.extend(
                self._variant_layers(
                    variants=variants,
                    cue=cue,
                    resting=resting,
                    anchor=anchor,
                    geometry=geometry,
                )
            )

        if exit_spec["kind"] == "series":
            layers.extend(
                self._series_layers(
                    source=source,
                    cue=cue,
                    scales=exit_spec["scales"],
                    start_s=exit_start,
                    span_s=exit_s,
                    height_px=height_px,
                    spinning=spinning,
                    box=box,
                    anchor=anchor,
                    geometry=geometry,
                    hold_until=cue.end_s,
                    fading_out=True,
                    hide_windows=base_hide_windows + cue_hide_windows,
                )
            )
        else:
            travel_x, travel_y = _travel_distances(
                kind=exit_spec["kind"],
                edge=cue.exit.edge,
                idle_amplitude=cue.idle.amplitude_px,
                **geometry,
            )
            layers.append(
                CharacterLayer(
                    frame=resting,
                    # Every exit layer starts when it takes over the frame,
                    # which is also when the entrance layer stops.
                    start_s=exit_start,
                    end_s=cue.end_s,
                    box_x=box_x,
                    box_y=box_y,
                    fade_out_s=exit_s if exit_spec.get("fade_out") else 0.0,
                    travel=_travel_kind(exit_spec, phase="exit"),
                    travel_edge=cue.exit.edge,
                    travel_s=exit_s,
                    travel_x=travel_x,
                    travel_y=travel_y,
                    travel_ease=exit_spec.get("ease", "out"),
                    spin=exit_spec.get("spin", "none"),
                    spin_s=exit_s,
                    idle=cue.idle,
                    idle_origin_s=cue.start_s,
                    hide_windows=base_hide_windows + cue_hide_windows,
                )
            )

        return self._apply_walk(cue, layers, frame_size, box[0])

    # -- internals --------------------------------------------------------

    def _apply_walk(
        self,
        cue: CharacterCue,
        layers: list[CharacterLayer],
        frame_size: tuple[int, int],
        box_w: int,
    ) -> list[CharacterLayer]:
        """
        Give every layer of this cue its walk expression.

        The walk is a property of the cue, not of one layer: a character
        that walks while it changes pose must keep walking through the
        swap, or it teleports on every frame change.  Applying it once to
        the finished stack is what guarantees the whole cue moves as one
        body -- and it keeps the entrance, the pose stack and the exit from
        drifting out of step with each other.
        """
        stops = tuple(getattr(cue, "moves", ()) or ())
        if not stops:
            return layers

        walk_x = _walk_expression(cue, axis="x", frame_size=frame_size, box_w=box_w)
        walk_y = _walk_expression(cue, axis="y", frame_size=frame_size, box_w=box_w)
        walk_angle = _walk_angle_expression(cue)
        walk_flips = _walk_flip_windows(
            stops,
            start_s=cue.start_s,
            end_s=cue.end_s,
            baked_facing=bool(getattr(cue, "flip", False)),
        )

        # The layer is a frozen dataclass, so a layer that walks is a new
        # one; the caller's list is rebuilt rather than mutated in place.
        return [
            replace(
                layer,
                walk_x=walk_x,
                walk_y=walk_y,
                walk_flips=walk_flips,
                walk_angle=walk_angle,
            )
            for layer in layers
        ]

    def _variant_layers(
        self,
        *,
        variants: tuple,
        cue: CharacterCue,
        resting: SpriteFrame,
        anchor: str,
        geometry: dict,
    ) -> list[CharacterLayer]:
        """
        One baked input per distinct pose, talk frame or mouth patch.

        A body variant is baked from its own artwork but into the *resting*
        box at the resting content height, so the composite position is one
        number for the whole cue and a swap changes pixels, not geometry:
        feet stay on the baseline, different aspect ratios just fill the
        box's width differently.  `travel="none"` keeps the box still while
        the idle expression continues to move it, so a talking host keeps
        breathing through a pose change.

        A mouth variant bakes the resting sprite *with the patch already
        composited on*, so from ffmpeg's point of view these layers are
        indistinguishable from body variants -- but the body pixels are
        identical between them and only the face moves.
        """
        layers: list[CharacterLayer] = []
        frames: dict[tuple, SpriteFrame] = {}
        mouth_spec = cue.mouth

        # The mouth flap is a schedule of short alternating windows over two
        # (or a few) patches.  One *layer per window* would make every scene
        # carry fifty-plus ffmpeg inputs -- each with its own decoder and
        # filter buffers, which is what made renders eat the whole machine.
        # So the windows are grouped by patch file and one layer per file
        # shows itself through a summed enable expression instead.
        mouth_windows: dict[str, list[tuple[float, float]]] = {}
        mouth_order: list[str] = []
        for variant in variants:
            if variant.kind != "mouth":
                continue
            if mouth_spec is None or not variant.mouth_file:
                continue
            window = (
                max(variant.start_s, cue.start_s),
                min(variant.end_s, cue.end_s),
            )
            if window[1] <= window[0]:
                continue
            if variant.mouth_file not in mouth_windows:
                mouth_windows[variant.mouth_file] = []
                mouth_order.append(variant.mouth_file)
            mouth_windows[variant.mouth_file].append(
                (round(window[0], 4), round(window[1], 4))
            )

        for variant in variants:
            if variant.kind == "mouth":
                continue
            key = (variant.image_file, bool(variant.flip) != bool(cue.flip))
            frame = frames.get(key)
            if frame is None:
                frame = self._bake(
                    Path(variant.image_file),
                    height_px=resting.content_h,
                    scale=1.0,
                    flip=key[1],
                    spinning=False,
                    box=(resting.box_w, resting.box_h),
                    anchor=anchor,
                )
                frames[key] = frame
            layers.append(
                CharacterLayer(
                    frame=frame,
                    start_s=variant.start_s,
                    end_s=variant.end_s,
                    box_x=geometry["box_x"],
                    box_y=geometry["box_y"],
                    idle=cue.idle,
                    idle_origin_s=cue.start_s,
                )
            )

        # One input per distinct patch, visible during every window it owns.
        # The enable expression replaces the old one-layer-per-flap schedule,
        # which turned a talking scene into fifty ffmpeg inputs.
        for mouth_file in mouth_order:
            windows = _merged_windows(mouth_windows[mouth_file])
            if not windows:
                continue
            frame = self._bake(
                resting.source_path,
                height_px=resting.content_h,
                scale=1.0,
                flip=cue.flip,
                spinning=False,
                box=(resting.box_w, resting.box_h),
                anchor=anchor,
                mouth=Path(mouth_file),
                mouth_anchor=(mouth_spec.x, mouth_spec.y),
                mouth_size=tuple(mouth_spec.size),
            )
            layers.append(
                CharacterLayer(
                    frame=frame,
                    start_s=cue.start_s,
                    end_s=cue.end_s,
                    box_x=geometry["box_x"],
                    box_y=geometry["box_y"],
                    idle=cue.idle,
                    idle_origin_s=cue.start_s,
                    show_windows=tuple(windows),
                )
            )

        return layers

    def _bake(
        self,
        source: Path,
        *,
        height_px: int,
        scale: float,
        flip: bool,
        spinning: bool,
        box: tuple[int, int] | None,
        anchor: str,
        mouth: Path | None = None,
        mouth_anchor: tuple[float, float] = (0.5, 0.5),
        mouth_size: tuple[float, float] = (0.2, 0.1),
    ) -> SpriteFrame:
        return bake_sprite(
            source=source,
            height_px=height_px,
            scale=scale,
            flip=flip,
            spinning=spinning,
            box=box,
            anchor=anchor,
            destination_dir=self.cache_dir,
            mouth=mouth,
            mouth_anchor=mouth_anchor,
            mouth_size=mouth_size,
        )

    def _box_for(
        self,
        source: Path,
        *,
        height_px: int,
        scale: float,
        spinning: bool,
        flip: bool,
        anchor: str,
    ) -> tuple[int, int]:
        frame = self._bake(
            source,
            height_px=height_px,
            scale=scale,
            flip=flip,
            spinning=spinning,
            box=None,
            anchor=anchor,
        )
        return frame.box_w, frame.box_h

    def _series_layers(
        self,
        *,
        source: Path,
        cue: CharacterCue,
        scales: tuple[float, ...],
        start_s: float,
        span_s: float,
        height_px: int,
        spinning: bool,
        box: tuple[int, int],
        anchor: str,
        geometry: dict,
        hold_until: float,
        fading_out: bool = False,
        hide_windows: tuple[tuple[float, float], ...] = (),
    ) -> list[CharacterLayer]:
        """
        Bake a size animation as a stack of held steps.

        Each step starts while the previous one is still visible and ramps
        its alpha in over a fraction of the step, so the composite slides
        from one size to the next instead of snapping between them.
        """
        steps = max(1, len(scales))
        span = max(span_s, 0.05)
        step_s = span / steps
        fade_s = min(step_s * STEP_FADE_SHARE, MAX_STEP_FADE_S)
        # One frame of slack on the handover: the next step is only fully
        # opaque at `next_start + fade_s`, so the previous one must still be
        # there at that instant.
        margin = 1.0 / self.fps

        layers: list[CharacterLayer] = []
        for index, scale in enumerate(scales):
            step_start = start_s + step_s * index
            is_last = index == steps - 1
            layers.append(
                CharacterLayer(
                    frame=self._bake(
                        source,
                        height_px=height_px,
                        scale=scale,
                        flip=cue.flip,
                        spinning=spinning,
                        box=box,
                        anchor=anchor,
                    ),
                    start_s=step_start,
                    # The final step holds until the cue hands over, so a
                    # settled entrance needs no second layer to stay
                    # visible; earlier steps only have to outlast the next
                    # one's fade.
                    end_s=(
                        hold_until
                        if is_last
                        else min(step_start + step_s + fade_s + margin, hold_until)
                    ),
                    box_x=geometry["box_x"],
                    box_y=geometry["box_y"],
                    fade_in_s=fade_s,
                    fade_out_s=(
                        min(fade_s, span) if (is_last and fading_out) else 0.0
                    ),
                    idle=cue.idle,
                    idle_origin_s=cue.start_s,
                    hide_windows=hide_windows,
                )
            )
        return layers


def _merged_windows(
    windows: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Sort windows and fuse the ones that touch or overlap."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(windows):
        if merged and start - merged[-1][1] <= 1.0 / 1000:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((round(start, 4), round(end, 4)))
    return merged


def _hide_windows(
    variants: tuple, *, start: float, end: float
) -> tuple[tuple[float, float], ...]:
    """
    The slices of a base layer a pose/talk variant owns.

    Variants composite *over* the resting sprite, so a silhouette smaller
    than the resting art leaves the old pose ghosting around the new one.
    The fix is to switch the base layer off for exactly those windows and
    let the variant carry the frame alone -- the composite position is one
    box, so the swap changes pixels, not geometry.  Windows are clamped to
    the base layer's own span and dropped when nothing usable remains.
    """
    windows = [
        (max(variant.start_s, start), min(variant.end_s, end))
        for variant in variants
        if variant.end_s > start and variant.start_s < end
    ]
    merged: list[tuple[float, float]] = []
    for window_start, window_end in sorted(windows):
        if merged and window_start - merged[-1][1] <= 1.0 / 1000:
            merged[-1] = (merged[-1][0], max(merged[-1][1], window_end))
        else:
            merged.append((window_start, window_end))
    return tuple(
        (round(s, 4), round(e, 4)) for s, e in merged if e > s
    )


def _travel_kind(spec: dict, *, phase: str) -> str:
    """Travel is the same geometry read in opposite directions."""
    if spec["kind"] == "travel":
        return "out" if phase == "enter" else "in"
    if spec["kind"] == "drop":
        return "drop"
    if spec["kind"] == "drop_out":
        return "drop_out"
    return "none"


def _travel_distances(
    *,
    kind: str,
    edge: str,
    idle_amplitude: int,
    frame_size: tuple[int, int],
    box_x: int,
    box_y: int,
    box_w: int,
    box_h: int,
) -> tuple[int, int]:
    """
    How far a sprite has to travel to be completely off screen.

    Signed: negative means off the left/top, positive off the right/bottom,
    so the caller never has to guess a direction from the edge name.

    The idle oscillation is added to the distance rather than ignored: an
    idle sways the sprite around its rest position *while it is still
    travelling*, so a distance measured to the frame edge leaves a sliver of
    the character on screen at the first frame of an entrance (measured, at
    7px on a 320px frame -- and the schema allows amplitudes eight times
    that).
    """
    frame_w, frame_h = frame_size
    slack = max(idle_amplitude, 0)

    if kind == "drop":
        return 0, box_y + box_h + slack
    if kind == "drop_out":
        return 0, (frame_h - box_y) + box_h * 2
    if kind != "travel":
        return 0, 0

    horizontal = {
        "left": -(box_x + box_w + slack),
        "top_left": -(box_x + box_w + slack),
        "bottom_left": -(box_x + box_w + slack),
        "right": (frame_w - box_x) + box_w + slack,
        "top_right": (frame_w - box_x) + box_w + slack,
        "bottom_right": (frame_w - box_x) + box_w + slack,
    }
    vertical = {
        "top": -(box_y + box_h + slack),
        "top_left": -(box_y + box_h + slack),
        "top_right": -(box_y + box_h + slack),
        "bottom": (frame_h - box_y) + box_h + slack,
        "bottom_left": (frame_h - box_y) + box_h + slack,
        "bottom_right": (frame_h - box_y) + box_h + slack,
    }

    travel_x = horizontal.get(edge, 0)
    travel_y = vertical.get(edge, 0)

    # An edge with no direction in it ("center") still has to travel
    # somewhere: use the side the character is nearer, which is where a host
    # would leave the stage anyway.
    if travel_x == 0 and travel_y == 0:
        travel_x = (
            -(box_x + box_w + slack)
            if box_x < frame_w / 2
            else (frame_w - box_x) + box_w + slack
        )

    return travel_x, travel_y
