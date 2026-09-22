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
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from autovid.domain.characters import CharacterCue
from autovid.domain.script import CharacterIdle

# Headroom around the character inside its box.
FRAME_PAD = 1.10
# A rotating sprite sweeps its diagonal through the box, so it needs more.
SPIN_PAD = 1.45

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

    @property
    def box_w(self) -> int:
        return self.frame.box_w

    @property
    def box_h(self) -> int:
        return self.frame.box_h

    def to_dict(self) -> dict:
        return {
            "file": str(self.frame.path),
            "start_s": round(self.start_s, 4),
            "end_s": round(self.end_s, 4),
            "fade_in_s": round(self.fade_in_s, 4),
            "fade_out_s": round(self.fade_out_s, 4),
            "travel": self.travel,
            "travel_edge": self.travel_edge,
            "travel_s": round(self.travel_s, 4),
            "travel_px": [self.travel_x, self.travel_y],
            "spin": self.spin,
            "idle": self.idle.type if self.idle else "none",
            "position": [self.box_x, self.box_y],
            "box": [self.frame.box_w, self.frame.box_h],
        }

    # -- expressions ------------------------------------------------------

    def x_expression(self) -> str:
        return _position_expression(self, axis="x")

    def y_expression(self) -> str:
        return _position_expression(self, axis="y")

    def angle_expression(self) -> str | None:
        """Rotation in radians, or None when the layer does not spin."""
        if self.spin == "none" or self.spin_s <= 0:
            return None
        progress = _progress(self.start_s, self.spin_s)
        if self.spin == "in":
            # A whole turn that lands facing the viewer.
            return f"-2*PI*(1-{progress})"
        return f"2*PI*{progress}"


def _number(value: float) -> str:
    """Compact fixed-point number for a filter argument."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _progress(start_s: float, duration_s: float) -> str:
    """Clamped 0..1 progress from `start_s` over `duration_s`."""
    duration = max(duration_s, 1e-3)
    return f"min(max((t-{_number(start_s)})/{_number(duration)},0),1)"


def _position_expression(layer: CharacterLayer, *, axis: str) -> str:
    terms = [str(layer.box_x if axis == "x" else layer.box_y)]
    travel = _travel_expression(layer, axis=axis)
    if travel:
        terms.append(travel)
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
) -> SpriteFrame:
    """
    Scale a character to `height_px * scale` and paste it onto its box.

    `box` fixes the canvas so every step of a size animation shares the same
    overlay geometry; omitted, the box is derived from this step.
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
                )
            )

        if exit_start is None:
            return layers

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
                )
            )

        return layers

    # -- internals --------------------------------------------------------

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
                )
            )
        return layers


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
