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

    [0:v] zoompan=... , setsar=1 , split=2 [bgfull][bgart];
    [bgfull] crop=<panel> , pad=<backdrop> [bg];
    [1:v] format=rgba [fr];
    [bg][fr] overlay=<panel xy> [bfg];
    [bgart] crop=<art window> [art];
    [bfg][art] overlay=<art xy> [bfart];
    [2:v] format=rgba, <animation> [ov0];
    [bfart][ov0] overlay=...:enable=... [v0];
    ...
    [vN] format=yuv420p [out]

(the split/panel/art lines exist only when a story frame is configured)

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
* An **animated overlay is looped differently**.  The `gif` demuxer has no
  `-framerate` input option -- `ffmpeg -framerate 30 -i x.gif` fails with
  "Option framerate not found" -- so a GIF is looped with `-stream_loop -1`
  and resampled to the scene's rate by an `fps` filter inside the chain.
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

# Where the story frame sits, the rest of the canvas is a matte, not part
# of the picture: the scene is cropped into the panel's window and the
# surrounding canvas is filled with this near-black backdrop.  Art never
# bleeds past the frame's edge, and the narrator reads against a surface
# that never fights the picture for attention.
STORY_FRAME_BACKDROP_RGB = (31, 35, 45)

# Artwork-window inset per frame style, in panel fractions.  A float is
# uniform on all sides (of the panel's short side, like before); a dict
# names sides explicitly: t/b are fractions of the panel HEIGHT, l/r of
# the panel WIDTH.  Must match ART_INSETS in make_story_frame.py -- that
# is the mat the drawn frame promises, and the crop has to honour it.
STYLE_ART_INSETS = {
    "border": 0.035,
    "none": 0.035,
    "polaroid": 0.045,
    "tv_retro": {"t": 0.05, "b": 0.155, "l": 0.04, "r": 0.04},
    "night": 0.035,
}


def story_inset_for_style(style: str) -> float:
    """The artwork-window inset for a frame style, in panel fractions."""
    return STYLE_ART_INSETS.get(style, 0.035)

# The picture sits inside the panel on a cream mat, like a matted print:
# this share of the panel's own short side is left as mat between the
# border ring and the artwork window on every side.
# Default mat inset for callers that do not name a style (panel fractions
# of the short side).
STORY_FRAME_ART_INSET = 0.035

# The blinking pilot lamp: a 128x64 sheet (green lamp left, red lamp right)
# overlaid on the panel, hopping between halves every half period.  cx/cy
# are the lamp centre in panel fractions; the socket is painted by the
# panel PNG itself, the sheet only supplies the glowing bulb.
STYLE_LAMPS = {
    "tv_retro": {"cx": 0.892, "cy": 0.915, "period": 1.0},
}
LAMP_SHEET = "assets/frames/tv_lamp_blink.png"
LAMP_HALF = 64

# A gentle whole-panel wobble (the buttons and lamp ride along): the
# overlay positions get a small sine offset, and the artwork overlay uses
# the identical shift so the picture stays glued inside the bezel.
STORY_FRAME_WOBBLES = {
    "tv_retro": {"ax": 3.0, "ay": 2.0, "period": 2.4},
}


def _wobble_shifts(story_frame: dict) -> tuple[str, str]:
    """Expression suffixes for the panel/art/lamp overlays, or ('','')."""
    wobble = story_frame.get("wobble") or {}
    ax, ay, period = wobble.get("ax"), wobble.get("ay"), wobble.get("period")
    if not (ax and ay and period):
        return "", ""
    return (
        f"+sin(2*PI*t/{period})*{ax}",
        f"+sin(2*PI*t/{period}+PI/2)*{ay}",
    )


@dataclass(frozen=True)
class OverlayLayer:
    """One text layer and its animation window, in scene-relative seconds."""

    path: Path
    start_s: float
    end_s: float
    animation: str = "fade_in"
    animation_duration_s: float = 0.3
    # A caption is baked full-frame and lands at 0:0; a picture is placed by
    # `box` (x, y, width, height in output pixels) and scaled on the way in,
    # because its size is the author's choice rather than the frame's.
    media: str = "text"
    box: tuple[int, int, int, int] | None = None
    # A GIF arrives at whatever rate the file happens to carry and keeps it;
    # only the loop that keeps it running past its last frame is special.
    animated: bool = False


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


def _sprite_enable(layer: CharacterLayer) -> str:
    """
    The overlay gate for one sprite layer, hide windows subtracted.

    A layer with show windows is the merged-mouth case: one baked input
    that is visible during a long list of short flap windows.  Its gate is
    a sum of `between(...)` terms -- ffmpeg's enable treats any nonzero
    value as on -- which keeps one input alive for the whole cue instead
    of one input per window.

    The plain case is `between(t, start, end)`.  When the planner marked
    windows where a pose/talk variant owns the frame, those are multiplied
    out -- `between(...) * not(between(...))` -- so the base sprite goes
    dark exactly while the variant is up, instead of ghosting through it.
    The expression stays a single product of 0/1 terms, which enable
    treats as plain truthiness.
    """
    if layer.show_windows:
        expression = "+".join(
            f"between(t,{_number(s)},{_number(e)})"
            for s, e in layer.show_windows
        )
        return f":enable='{expression}'"
    expression = f"between(t,{_number(layer.start_s)},{_number(layer.end_s)})"
    for hide_start, hide_end in layer.hide_windows:
        expression = (
            f"({expression}"
            f"*not(between(t,{_number(hide_start)},{_number(hide_end)})))"
        )
    return f":enable='{expression}'"


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

    if layer.media == "image" and layer.box is not None:
        x_px, y_px, width_px, height_px = layer.box
        # An animated overlay keeps the timing the file carries: resampling
        # it to the scene rate would make a hand-drawn GIF play faster than
        # it was drawn, and `overlay` holds the last frame of a slow source
        # until the next one arrives, which is what a GIF is supposed to do.
        chain = [f"scale={max(width_px, 2)}:{max(height_px, 2)}", "format=rgba"]
        chain.extend(_overlay_animation_filters(layer))
        enable = (
            f":enable='between(t,{_number(layer.start_s)},"
            f"{_number(layer.end_s)})'"
        )
        if layer.animation == "slide_in":
            overlay = (
                f"overlay=x='{_slide_x_expression(layer)}':"
                f"y={y_px}:format=auto{enable}"
            )
        else:
            overlay = f"overlay={x_px}:{y_px}:format=auto{enable}"
        return [
            f"[{input_index}:v]{','.join(chain)}{source}",
            f"{background}{source}{overlay}{output}",
        ]

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

    # A walk that turns around asks for the opposite facing.  The box is
    # baked at the cue's own facing, so the mirror happens here, gated to
    # the stretches the planner marked -- flipping outside them would undo
    # the baked artwork, and flipping for only the walk's own duration
    # would put the character back the way it started the instant it
    # arrived.
    if layer.walk_flips:
        expression = "+".join(
            f"between(t,{_number(s)},{_number(e)})"
            for s, e, facing in layer.walk_flips
            if facing
        )
        if expression:
            chain.append(f"hflip=enable='{expression}'")

    # Fades are capped by the layer's own length, not by half of it: a
    # departure layer exists only to fade, so halving its window would
    # silently shorten the effect that was asked for.
    span = max(layer.end_s - layer.start_s, 0.05)

    enable = _sprite_enable(layer)

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


def build_story_frame_block(
    *,
    input_index: int,
    background: str,
    story_frame: dict,
) -> list[str]:
    """
    Composite the storytelling frame's panel right above the background.

    The panel is a baked RGBA PNG placed at exactly (x, y), full opacity,
    for the whole clip: the frame is a fixture of the layout, not a layer
    with moods.  The background underneath it is already just backdrop and
    the panel box itself -- the panel is opaque, so nothing of that plate
    survives -- and the artwork goes on top in `build_story_art_block`.

    Styles with a pilot lamp (STYLE_LAMPS) take one extra input right
    after the panel: a two-lamp sheet whose overlay hops between the
    green and the red half, so the lamp blinks without any second graph.
    """
    x = int(story_frame["x"])
    y = int(story_frame["y"])
    wx, wy = _wobble_shifts(story_frame)
    if wx:
        panel_xy = f"x='{x}{wx}':y='{y}{wy}'"
    else:
        panel_xy = f"{x}:{y}"
    lines = [
        f"[{input_index}:v]format=rgba[fr]",
        f"{background}[fr]overlay={panel_xy}:format=auto[bfg]",
    ]
    lamp = story_frame.get("lamp")
    if lamp:
        lx, ly = int(lamp["x"]), int(lamp["y"])
        half, period = int(lamp["half"]), float(lamp["period"])
        lamp_x = f"{lx}{wx}-{half}*gte(mod(t,{period}),0.5)"
        lines.extend(
            [
                f"[{input_index + 1}:v]format=rgba[lampsheet]",
                f"[bfg][lampsheet]overlay=x='{lamp_x}':y='{ly}{wy}':format=auto[bfg]",
            ]
        )
    return lines


def build_story_art_block(
    *,
    background: str,
    story_frame: dict,
) -> list[str]:
    """
    Crop the Ken Burns picture into the panel's window and lay it on the mat.

    The zoompan output is split: one branch becomes the backdrop plate (see
    `build_scene_filtergraph`), the other is cropped to the artwork window
    -- the panel box shrunk by the style's mat inset -- and composited back
    at the window's own position.  The picture therefore exists *only*
    inside the frame, exactly the storytelling look the layout promises.
    """
    x = int(story_frame["x"])
    y = int(story_frame["y"])
    w = int(story_frame["w"])
    h = int(story_frame["h"])
    # An explicit `art_inset` in the script (a hand-drawn frame's declared
    # window) wins over the style's built-in mat.
    inset = story_frame.get("inset") or story_inset_for_style(
        story_frame.get("style", "border")
    )
    if isinstance(inset, dict):
        # Per-side window: a styled frame can keep a fat column for knobs
        # and a chin under the screen, so the mat is intentionally uneven.
        top = max(int(round(h * inset.get("t", 0.0))), 1)
        bottom = max(int(round(h * inset.get("b", 0.0))), 1)
        left = max(int(round(w * inset.get("l", 0.0))), 1)
        right = max(int(round(w * inset.get("r", 0.0))), 1)
    else:
        top = bottom = left = right = max(
            int(round(min(w, h) * inset)), 1
        )
    art_w = max(w - left - right, 2)
    art_h = max(h - top - bottom, 2)
    art_x = x + left
    art_y = y + top
    wx, wy = _wobble_shifts(story_frame)
    if wx:
        art_xy = f"x='{art_x}{wx}':y='{art_y}{wy}'"
    else:
        art_xy = f"{art_x}:{art_y}"
    return [
        f"[bgart]crop={art_w}:{art_h}:{art_x}:{art_y}[art]",
        f"{background}[art]overlay={art_xy}:format=auto[bfart]",
    ]


def _story_backdrop_chain(story_frame: dict, frame_size: tuple[int, int]) -> str:
    """
    Crop the background plate to the panel box and pad it out to full frame.

    `pad` both re-expands the canvas and paints everything around the panel
    box with the backdrop colour, so no second colour input is needed.
    `setsar=1` was already applied before the split; crop and pad preserve
    sample aspect, so the plate stays square-pixelled.
    """
    width, height = frame_size
    x = int(story_frame["x"])
    y = int(story_frame["y"])
    w = int(story_frame["w"])
    h = int(story_frame["h"])
    r, g, b = STORY_FRAME_BACKDROP_RGB
    return (
        f"crop={w}:{h}:{x}:{y},"
        f"pad={width}:{height}:{x}:{y}:color=0x{r:02X}{g:02X}{b:02X}"
    )


def build_scene_filtergraph(
    *,
    frames: int,
    frame_size: tuple[int, int],
    fps: int,
    motion: dict | None,
    overlays: list[OverlayLayer],
    sprites: list[CharacterLayer] | None = None,
    impact: ImpactPunch | None = None,
    story_frame: dict | None = None,
) -> str:
    """
    Complete filtergraph for one scene.

    The order is the compositing order and it is deliberate:

        Ken Burns background -> story frame -> characters -> text -> flash

    With a story frame, "background" means the matted plate -- the scene
    cropped into the panel box, the rest of the canvas filled with the
    dark backdrop -- and the scene's artwork is laid onto the panel's mat
    before anything else composites.  The picture only ever exists inside
    the frame.

    Characters go *under* the text, because a host standing on top of a
    caption reads as a mistake, and the flash goes last so a punch-in hits
    the whole frame rather than only the background plate.

    Callers must pass inputs in the matching order: `[0:v]` is the prepared
    image, then -- when `story_frame` is set -- the frame's panel PNG, then
    one input per sprite layer, then one per text overlay, then (only when
    the scene punches in with a flash) one colour source.
    """
    width, height = frame_size
    sprites = sprites or []

    zoompan = build_zoompan_filter(
        motion=motion, frames=frames, frame_size=(width, height), fps=fps,
        impact=impact,
    )

    statements: list[str]
    if story_frame is None:
        statements = [f"[0:v]{zoompan},setsar=1[bg]"]
        current = "[bg]"
    else:
        # One Ken Burns run, two consumers: the backdrop plate and the
        # artwork that is cropped into the panel's window.
        statements = [f"[0:v]{zoompan},setsar=1,split=2[bgfull][bgart]"]
        statements.append(f"[bgfull]{_story_backdrop_chain(story_frame, (width, height))}[bg]")
        current = "[bg]"

    if story_frame is not None:
        statements.extend(
            build_story_frame_block(
                input_index=1,
                background=current,
                story_frame=story_frame,
            )
        )
        statements.extend(
            build_story_art_block(
                background="[bfg]",
                story_frame=story_frame,
            )
        )
        current = "[bfart]"

    def _after_frame(offset: int) -> int:
        """Input slots shift by the panel (1) and its lamp sheet (0 or 1)."""
        shift = 1 if story_frame is not None else 0
        if story_frame is not None and story_frame.get("lamp"):
            shift += 1
        return offset + shift

    for position, sprite in enumerate(sprites):
        statements.extend(
            build_sprite_block(
                input_index=_after_frame(1 + position),
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
                input_index=_after_frame(1 + len(sprites) + index),
                background=current,
            )
        )
        current = f"[v{index}]"

    if impact is not None and impact.flash != "none":
        statements.extend(
            build_flash_block(
                input_index=_after_frame(1 + len(sprites) + len(overlays)),
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
