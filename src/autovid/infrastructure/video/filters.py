"""
ffmpeg filtergraph construction for one scene.

Two things here are load-bearing and were verified against the bundled
ffmpeg before being written down (see `notes` in the stage-4 tests):

1. `zoompan` does **not** support the `t` timeline variable.  Progress has
   to be driven by `on`, the output frame index, which counts from 0 for
   the first frame.  A graph that uses `t` silently produces a static
   frame, which is exactly the kind of failure that would look like "the
   Ken Burns just did not apply" much later.
2. One input image plus `d=<frames>` produces exactly `<frames>` output
   frames, so `frames = round(duration * fps)` is the whole frame count
   contract.  No `-t` needed, and no off-by-one.

The scene graph therefore looks like:

    [0:v] zoompan=... , setsar=1 [bg];
    [1:v] format=rgba, <animation> [ov0];
    [bg][ov0] overlay=...:enable=... [v0];
    ...
    [vN] format=yuv420p [out]

Input 0 is the prepared still, then one input per character layer, then one
per text layer, then (when the scene flashes) one colour source.

Two ffmpeg behaviours make the details below load-bearing, and both were
found by rendering and measuring frames rather than by reading the code:

* A **still image input is a single frame at t=0**, and `overlay` repeats
  that one frame for the rest of the clip.  A timeline filter downstream
  therefore only ever sees t=0: `fade=in:st=2` evaluates at t=0, decides
  the layer is not visible yet, and the text never appears at all.  The
  caller must supply each layer with real frames (`-loop 1`) so the fade
  has a timeline to act on.
* `overlay` has no notion of when a layer should be visible, so a layer
  with no fade sits on screen for the whole clip unless it is gated with
  `enable`.  Every overlay is gated, whether or not it fades.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autovid.infrastructure.video.sprites import CharacterLayer

# Categorised Ken Burns moves.  Zoom changes the visible window size;
# pan keeps the size fixed and slides the window across the image.
ZOOM_TYPES = {"zoom_in", "zoom_out"}
PAN_TYPES = {"pan_left", "pan_right", "pan_up", "pan_down"}

# Content travel below this looks frozen rather than slow, because the
# integer pixel positions round to the same value for several frames.
MIN_TRAVEL_PX_PER_FRAME = 0.25

# A punch-in decays to nothing over this many times its own length, so the
# shake is over well before the next beat.
IMPACT_DECAY_FACTOR = 1.6
# The shake oscillates at roughly this many cycles per second.
IMPACT_SHAKE_HZ = 16.0
# How much of the flash colour a punch-in lays over the frame at its peak.
IMPACT_FLASH_STRENGTH = 0.55


@dataclass(frozen=True)
class OverlayLayer:
    """One text layer and its animation window, in scene-relative seconds."""

    path: Path
    start_s: float
    end_s: float
    animation: str = "fade_in"
    animation_duration_s: float = 0.3


@dataclass(frozen=True)
class ImpactPunch:
    """
    A punch-in: a zoom spike, a camera shake, and optionally a flash.

    All three decay from the same instant, which is what sells the beat --
    a zoom that spikes and a camera that shakes but settles while the zoom
    is still visible reads as a zoom, not as an impact.
    """

    offset_s: float
    intensity: float
    shake_px: int
    duration_s: float
    flash: str = "none"

    @property
    def decay_s(self) -> float:
        return max(self.duration_s * IMPACT_DECAY_FACTOR, 0.05)


def _number(value: float) -> str:
    """Compact fixed-point number for a filter argument (no exponents)."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _progress(frames: int) -> str:
    """`on`-driven 0..1 progress expression."""
    last = max(frames - 1, 1)
    return f"on/{last}"


def _impact_term(punch: ImpactPunch, *, frames: int, fps: int, scale: float) -> str:
    """
    The decaying pulse of a punch-in, as an `on`-driven expression.

    Zero before the beat, so the same expression can be added to a zoom or
    an offset without a separate gate.
    """
    start_frame = max(int(round(punch.offset_s * fps)), 0)
    decay_frames = max(punch.decay_s * fps, 1.0)
    return (
        f"if(gt(on,{start_frame}),"
        f"{_number(scale)}*exp(-(on-{start_frame})/{_number(decay_frames)}),0)"
    )


def _impact_shake_term(
    punch: ImpactPunch, *, fps: int, amplitude: float, phase: float
) -> str:
    """A decaying oscillation, for the camera shake half of a punch-in."""
    if amplitude <= 0:
        return ""
    start_frame = max(int(round(punch.offset_s * fps)), 0)
    decay_frames = max(punch.decay_s * fps, 1.0)
    period_frames = max(fps / IMPACT_SHAKE_HZ, 1.0)
    return (
        f"if(gt(on,{start_frame}),"
        f"{_number(amplitude)}*exp(-(on-{start_frame})/{_number(decay_frames)})"
        f"*sin(2*PI*(on-{start_frame})/{_number(period_frames)}+{_number(phase)}),0)"
    )


def build_zoompan_filter(
    *,
    motion: dict | None,
    frames: int,
    frame_size: tuple[int, int],
    fps: int,
    impact: ImpactPunch | None = None,
) -> str:
    """
    Build the `zoompan` filter for one scene.

    `motion` is the schema's `ken_burns` block, or None when the scene has
    no movement.  A disabled or unrecognised move still goes through
    `zoompan` so every scene has an identical, single-frame-input shape.
    """
    width, height = frame_size
    motion = motion or {}

    enabled = bool(motion.get("enabled", False))
    kind = motion.get("type", "none") if enabled else "none"

    if enabled:
        start = float(motion.get("start_scale", 1.0))
        end = float(motion.get("end_scale", start))
    else:
        # A disabled effect means no zoom at all, not "hold at start_scale".
        # Stage 3 prepares such a frame at 1:1, so zooming here would spend
        # sharpness the frame never had.
        start = end = 1.0

    if kind in ZOOM_TYPES and abs(end - start) > 1e-6:
        zoom = f"{_number(start)}+(({_number(end)}-{_number(start)})*{_progress(frames)})"
    else:
        # Flat scale.  Pan needs it above 1.0 to have room to slide.
        zoom = _number(start)

    centred_x = "(iw-iw/zoom)/2"
    centred_y = "(ih-ih/zoom)/2"

    if kind == "pan_left":
        x, y = f"(iw-iw/zoom)*(1-{_progress(frames)})", centred_y
    elif kind == "pan_right":
        x, y = f"(iw-iw/zoom)*{_progress(frames)}", centred_y
    elif kind == "pan_up":
        x, y = centred_x, f"(ih-ih/zoom)*(1-{_progress(frames)})"
    elif kind == "pan_down":
        x, y = centred_x, f"(ih-ih/zoom)*{_progress(frames)}"
    else:
        x, y = centred_x, centred_y

    # The punch-in rides on top of whatever the scene was already doing:
    # a scene that was panning still pans, and the beat lands on top of it.
    if impact is not None and impact.intensity > 0:
        zoom = f"({zoom})+" + _impact_term(
            impact, frames=frames, fps=fps, scale=impact.intensity
        )
    if impact is not None and impact.shake_px > 0:
        shake_x = _impact_shake_term(
            impact, fps=fps, amplitude=float(impact.shake_px), phase=0.0
        )
        shake_y = _impact_shake_term(
            impact, fps=fps, amplitude=impact.shake_px * 0.7, phase=1.2
        )
        if shake_x:
            x = f"({x})+{shake_x}"
        if shake_y:
            y = f"({y})+{shake_y}"

    return (
        f"zoompan=z='{zoom}':x='{x}':y='{y}'"
        f":d={frames}:s={width}x{height}:fps={fps}"
    )


def _overlay_animation_filters(layer: OverlayLayer) -> list[str]:
    """
    Alpha filters that fade a layer in and out.

    `fade=alpha=1` acts on the alpha channel of a full-frame RGBA layer, so
    the text itself is never dimmed against its own outline.  These filters
    read the frame time, which only advances because the caller loops the
    still image into a real stream (see the module docstring).
    """
    start = layer.start_s
    end = layer.end_s
    duration = max(layer.end_s - layer.start_s, 1e-3)
    animation = layer.animation

    if animation == "none":
        return []

    # Pop is a short fade in; typewriter and slide reveal on their own and
    # only need the fade out.
    fade_in = animation in {"fade_in", "pop"}
    fade_out = True

    filters: list[str] = []
    if fade_in:
        window = min(max(layer.animation_duration_s, 0.05), duration / 2)
        filters.append(f"fade=t=in:st={_number(start)}:d={_number(window)}:alpha=1")
    if fade_out:
        window = min(max(layer.animation_duration_s, 0.05), duration / 2)
        filters.append(
            f"fade=t=out:st={_number(end - window)}:d={_number(window)}:alpha=1"
        )
    return filters


def _slide_x_expression(layer: OverlayLayer) -> str:
    """Slide the layer in from the left edge over the animation window."""
    window = max(layer.animation_duration_s, 0.05)
    start = layer.start_s
    return (
        f"if(lt(t,{_number(start)}),-W,"
        f"if(lt(t,{_number(start + window)}),"
        f"-W+W*(t-{_number(start)})/{_number(window)},0))"
    )


def build_overlay_block(
    index: int,
    layer: OverlayLayer,
    *,
    input_index: int | None = None,
    background: str | None = None,
) -> list[str]:
    """
    Filter statements that composite one text layer onto the running frame.

    Labels must be bracketed in full -- `[v0]_src` is not a label, it is a
    parse error -- so the transformed layer gets its own complete label.

    `input_index` and `background` are supplied by the scene graph, because
    characters are composited before the text and they consume input slots:
    the numbering is the graph's business, not each layer's.
    """
    if input_index is None:
        input_index = index + 1
    if background is None:
        background = "[bg]" if index == 0 else f"[v{index - 1}]"

    source = f"[ov{index}]"
    output = f"[v{index}]"

    chain = ["format=rgba"]
    chain.extend(_overlay_animation_filters(layer))

    # Gate the layer to its own window.  Without this a layer that does not
    # fade is composited from the first frame to the last, whatever the
    # script's offsets said.
    enable = (
        f":enable='between(t,{_number(layer.start_s)},{_number(layer.end_s)})'"
    )

    if layer.animation == "slide_in":
        overlay = (
            f"overlay=x='{_slide_x_expression(layer)}':y=0:format=auto{enable}"
        )
    else:
        overlay = f"overlay=0:0:format=auto{enable}"

    return [
        f"[{input_index}:v]{','.join(chain)}{source}",
        f"{background}{source}{overlay}{output}",
    ]


def build_sprite_block(
    *,
    input_index: int,
    label_index: int,
    background: str,
    layer: CharacterLayer,
) -> list[str]:
    """
    Composite one character layer onto the running frame.

    The three motions are composed here in the order that keeps them
    independent: rotate the sprite in its own padded box, ramp its alpha,
    then place the box with a time-varying position.  Rotating *after*
    placing would rotate the placement too, which would turn a spin-in from
    the left into an arc.
    """
    source = f"[sp{label_index}]"
    output = f"[s{label_index}]"

    chain = ["format=rgba"]

    angle = layer.angle_expression()
    if angle:
        # `c=none` keeps the padded box transparent; without it rotate
        # fills the corners with black and the character gains a black
        # square the moment it spins.
        chain.append(
            f"rotate=a='{angle}':ow={layer.box_w}:oh={layer.box_h}:c=none"
        )

    # Fades are capped by the layer's own length, not by half of it: a
    # departure layer exists only to fade, so halving its window would
    # silently shorten the effect that was asked for.
    span = max(layer.end_s - layer.start_s, 0.05)

    if layer.fade_in_s > 0:
        window = min(max(layer.fade_in_s, 0.05), span)
        chain.append(
            f"fade=t=in:st={_number(layer.start_s)}"
            f":d={_number(window)}:alpha=1"
        )

    if layer.fade_out_s > 0:
        window = min(max(layer.fade_out_s, 0.05), span)
        chain.append(
            f"fade=t=out:st={_number(layer.end_s - window)}"
            f":d={_number(window)}:alpha=1"
        )

    enable = (
        f":enable='between(t,{_number(layer.start_s)},{_number(layer.end_s)})'"
    )
    overlay = (
        f"overlay=x='{layer.x_expression()}':y='{layer.y_expression()}'"
        f":format=auto{enable}"
    )

    return [
        f"[{input_index}:v]{','.join(chain)}{source}",
        f"{background}{source}{overlay}{output}",
    ]


def build_flash_block(
    *,
    input_index: int,
    background: str,
    punch: ImpactPunch,
) -> list[str]:
    """
    Composite a flash frame that fades out from the moment of the impact.

    `fade=t=in:color=` cannot do this job: a fade-in paints the *whole* run
    before its start time in the flash colour (measured, not assumed), so a
    flash at 00:12 would white out the first twelve seconds.  A colour
    source whose timestamp starts at the beat and fades out is the effect
    that was actually wanted.
    """
    offset = _number(punch.offset_s)
    duration = _number(max(punch.duration_s, 0.05))
    return [
        f"[{input_index}:v]setpts=PTS-STARTPTS+{offset}/TB,format=rgba"
        f",colorchannelmixer=aa={_number(IMPACT_FLASH_STRENGTH)}"
        f",fade=t=out:st={offset}:d={duration}:alpha=1[fl]",
        f"{background}[fl]overlay=0:0:format=auto"
        f":enable='between(t,{offset},{_number(punch.offset_s + float(duration))})'[vf]",
    ]


def build_scene_filtergraph(
    *,
    frames: int,
    frame_size: tuple[int, int],
    fps: int,
    motion: dict | None,
    overlays: list[OverlayLayer],
    sprites: list[CharacterLayer] | None = None,
    impact: ImpactPunch | None = None,
) -> str:
    """
    Complete filtergraph for one scene.

    The order is the compositing order and it is deliberate:

        Ken Burns background -> characters -> text overlays -> flash

    Characters go *under* the text, because a host standing on top of a
    caption reads as a mistake, and the flash goes last so a punch-in hits
    the whole frame rather than only the background plate.

    Callers must pass inputs in the matching order: `[0:v]` is the prepared
    image, then one input per sprite layer, then one per text overlay, then
    (only when the scene punches in with a flash) one colour source.
    """
    width, height = frame_size
    sprites = sprites or []

    statements = [
        f"[0:v]{build_zoompan_filter(motion=motion, frames=frames, frame_size=(width, height), fps=fps, impact=impact)},setsar=1[bg]"
    ]
    current = "[bg]"

    for position, sprite in enumerate(sprites):
        statements.extend(
            build_sprite_block(
                input_index=1 + position,
                label_index=position,
                background=current,
                layer=sprite,
            )
        )
        current = f"[s{position}]"

    for index, layer in enumerate(overlays):
        statements.extend(
            build_overlay_block(
                index,
                layer,
                input_index=1 + len(sprites) + index,
                background=current,
            )
        )
        current = f"[v{index}]"

    if impact is not None and impact.flash != "none":
        statements.extend(
            build_flash_block(
                input_index=1 + len(sprites) + len(overlays),
                background=current,
                punch=impact,
            )
        )
        current = "[vf]"

    statements.append(f"{current}format=yuv420p[out]")

    return ";\n".join(statements)


def inspect_motion(
    motion: dict | None,
    *,
    frames: int,
    frame_size: tuple[int, int],
) -> dict:
    """
    Summarise a Ken Burns move in output pixels.

    A slow zoom is the move most likely to be configured too gently to see:
    the visible window shrinks by `width * (1/s1 - 1/s0)` output pixels over
    the whole scene, and if that is under a pixel per frame the integer
    positions repeat and the still looks frozen.  Reporting the number makes
    that visible instead of mysterious.
    """
    width, height = frame_size
    motion = motion or {}
    enabled = bool(motion.get("enabled", False))
    kind = motion.get("type", "none") if enabled else "none"

    report = {
        "enabled": enabled and kind != "none",
        "type": kind,
        "scale": [
            float(motion.get("start_scale", 1.0)),
            float(motion.get("end_scale", motion.get("start_scale", 1.0))),
        ],
        "frames": frames,
        "travel_px": 0.0,
        "travel_px_per_frame": 0.0,
        "too_slow": False,
    }

    if not report["enabled"]:
        return report

    start, end = report["scale"]

    if kind in ZOOM_TYPES:
        travel = abs(width * (1.0 / end - 1.0 / start))
        axis = "horizontal"
    elif kind in PAN_TYPES:
        fixed = start
        travel = (height if kind in {"pan_up", "pan_down"} else width) * (fixed - 1.0)
        axis = "vertical" if kind in {"pan_up", "pan_down"} else "horizontal"
    else:
        return report

    per_frame = travel / max(frames - 1, 1)
    report.update(
        {
            "travel_px": round(travel, 2),
            "travel_px_per_frame": round(per_frame, 4),
            "axis": axis,
            "too_slow": per_frame < MIN_TRAVEL_PX_PER_FRAME,
        }
    )
    return report
