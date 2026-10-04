"""
Text overlays as pre-rendered PIL layers.

The bundled ffmpeg in `bin/` has no `drawtext` filter -- verified with
`ffmpeg -h filter=drawtext`, which reports "Unknown filter" even though the
build's configure line lists `--enable-libfreetype`.  (`ass` and `subtitles`
*are* present, via libass, which is the tool for stage 7's captions; there
is simply no timeline-capable `drawtext`.)

Compositing pre-rendered PIL layers is the better design here anyway:

* Vietnamese text, colons, quotes and percent signs go through PIL
  untouched, instead of fighting ffmpeg's filtergraph escaping.
* Position, outline and sizing come from the same PIL code that stage 3
  measures overlay fit with, so what the report promises ("size 62 fits")
  is exactly what gets rendered.
* Animation becomes compositing: alpha ramps and moving overlays are done
  with ffmpeg's `fade=alpha=1` and animated `overlay` positions, which are
  standard, well-tested filters.
* The same is true of a caption's *box*: a speech bubble or a thought cloud
  is drawn into this layer with PIL, so it costs nothing in ffmpeg and
  cannot drift from the text it wraps.

Each layer is a full-frame transparent PNG with the caption already in its
final place, so compositing is a plain `overlay=0:0` and needs no position
maths in ffmpeg.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from autovid.infrastructure.image.fonts import wrap_lines

# Distance from the frame edge for non-centred placements.
TEXT_MARGIN_FRACTION = 0.05

# The kinds of box a caption can sit in.  `speech` and `thought` are the two
# a story actually needs -- somebody talking, or somebody thinking -- and
# `shout` is the third because a joke in a video wants a spiky one.
TEXT_FRAMES = ("none", "speech", "thought", "shout")

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# PIL anchor codes: horizontal (l/m/r) then vertical (t/m/b).
POSITION_ANCHORS: dict[str, str] = {
    "center": "mm",
    "top": "mt",
    "bottom": "mb",
    "top_left": "lt",
    "top_right": "rt",
    "bottom_left": "lb",
    "bottom_right": "rb",
}


@dataclass(frozen=True)
class TextLayer:
    path: Path
    text_width: int
    text_height: int

    def to_dict(self) -> dict:
        return {
            "file": str(self.path),
            "text_size": [self.text_width, self.text_height],
        }


@dataclass(frozen=True)
class TextBlock:
    """
    A caption laid out over as many lines as it needs.

    `width` and `height` are the block's own **ink** box, stroke included --
    the box a bubble is drawn around and the box the studio measures to
    decide whether the caption still fits.  Glyphs do not fill their line
    box, so the height runs ink to ink rather than ascender to descender;
    measured any other way, every caption would sit visibly high of where
    the author dropped it.

    `ink_offset` records how far below the first line's top the glyphs
    start.  Drawing does not need it -- PIL's `t` anchor already lands the
    stroked ink on the y it is given -- but a report that says how much air
    a face leaves above its words needs it.
    """

    lines: tuple[str, ...]
    line_height: int
    width: int
    height: int
    ink_offset: int = 0


@dataclass(frozen=True)
class BubbleBox:
    """Where a caption's box goes, and which way its tail points."""

    left: float
    top: float
    right: float
    bottom: float
    tail: str  # "down" or "up"


def parse_hex_colour(value: str, *, default: tuple[int, int, int] = (255, 255, 255)):
    """
    Parse '#RRGGBB' or '#RGB' into an RGB tuple.

    Unparseable values fall back to the default rather than raising: a
    colour typo should not stop a 13 minute render.
    """
    match = _HEX.match(value.strip())
    if match is None:
        return default

    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)

    return (
        int(digits[0:2], 16),
        int(digits[2:4], 16),
        int(digits[4:6], 16),
    )


def _anchor_xy(
    position: str,
    frame_size: tuple[int, int],
    *,
    inset: int = 0,
) -> tuple[str, tuple[float, float]]:
    """
    PIL anchor code and the point it is anchored at.

    `inset` pulls the anchor further in from the frame edge, which is how a
    caption keeps its bubble inside the frame: the text sits `inset` pixels
    inside the margin and the box grows outwards from there.
    """
    width, height = frame_size
    margin_x = width * TEXT_MARGIN_FRACTION + inset
    margin_y = height * TEXT_MARGIN_FRACTION + inset
    anchor = POSITION_ANCHORS.get(position, "mm")

    points = {
        "mm": (width / 2, height / 2),
        "mt": (width / 2, margin_y),
        "mb": (width / 2, height - margin_y),
        "lt": (margin_x, margin_y),
        "rt": (width - margin_x, margin_y),
        "lb": (margin_x, height - margin_y),
        "rb": (width - margin_x, height - margin_y),
    }
    return anchor, points[anchor]


def bubble_inset(font_size: int, kind: str) -> int:
    """
    How far a caption's text must sit inside its box, in pixels.

    A bubble that hugs its text looks like a mistake, and one that overflows
    the frame edge looks worse still, so the padding is reserved from the
    anchor outwards and the wrapping width is reduced to match.
    """
    if kind in ("none", ""):
        return 0
    padding = max(int(font_size * 0.42), 12)
    if kind == "shout":
        # The spikes reach further than the rounded corners do.
        return int(padding * 1.3)
    return padding


def tail_direction(position: str) -> str:
    """
    Which way a caption's tail points, worked out from where it sits.

    A bubble near the top of the frame points its tail up at whatever is
    above it; one near the bottom points down.  Asking for this per caption
    would be one more control for one more mistake.
    """
    return "up" if position.startswith("top") else "down"


def layout_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    stroke_width: int = 0,
    max_width: int | None = None,
) -> TextBlock:
    """
    Break `text` into lines that fit `max_width`, and measure the block.

    Without a width the text is one line, which is what every caption that
    was short enough to fit always was.
    """
    ascent, descent = font.getmetrics()
    line_height = ascent + descent

    lines = wrap_lines(text, font, max_width, stroke_width=stroke_width)
    width = 0
    first_top: int | None = None
    last_bottom = 0
    for line in lines:
        left, top, right, bottom = font.getbbox(line)
        width = max(width, right - left)
        if first_top is None or top < first_top:
            first_top = top
        last_bottom = max(last_bottom, bottom)
    width += 2 * stroke_width

    # Glyphs do not fill their line box -- a face with no descenders on the
    # first line leaves air above and below -- so the block is measured from
    # ink to ink.  Measuring from the ascender instead would put every
    # caption visibly high of where the author dropped it.
    offset = first_top or 0
    ink_height = max(last_bottom - offset, 1) + line_height * (len(lines) - 1)

    return TextBlock(
        lines=tuple(lines),
        line_height=line_height,
        width=max(width, 1),
        # The stroke sits outside the glyphs, so it is part of the box.
        height=ink_height + 2 * stroke_width,
        ink_offset=offset,
    )


def block_origin(
    block: TextBlock,
    position: str,
    frame_size: tuple[int, int],
    *,
    inset: int = 0,
    position_xy: tuple[float, float] | None = None,
) -> tuple[str, float, float, float]:
    """
    Where the block's own top-left corner goes, and its anchor.

    `position_xy` is a free position at the **centre** of the block, as
    fractions of the frame; it wins over `position` when both are given,
    which is what an author dragging a caption onto the canvas means.

    The block is positioned by the box the *whole* caption occupies, never
    by the box of the part that happens to be visible.  That is what keeps a
    typewriter reveal growing downwards from a fixed line instead of
    re-centring on every keystroke.
    """
    if position_xy is not None:
        frame_width, frame_height = frame_size
        centre_x = position_xy[0] * frame_width
        centre_y = position_xy[1] * frame_height
        return (
            # `t` because the caller draws with the block's own top edge;
            # centring is what `top` has already done.
            "lt",
            centre_x - block.width / 2,
            centre_y - block.height / 2,
            centre_y,
        )

    anchor, (anchor_x, anchor_y) = _anchor_xy(position, frame_size, inset=inset)
    horizontal = anchor[0]
    vertical = anchor[1]

    if horizontal == "m":
        left = anchor_x - block.width / 2
    elif horizontal == "r":
        left = anchor_x - block.width
    else:
        left = anchor_x

    if vertical == "t":
        top = anchor_y
    elif vertical == "b":
        top = anchor_y - block.height
    else:
        top = anchor_y - block.height / 2

    return f"{horizontal}{vertical}", left, top, anchor_y


def _bubble_box(
    block: TextBlock,
    left: float,
    top: float,
    *,
    padding: int,
    tail: str,
) -> BubbleBox:
    return BubbleBox(
        left=left - padding,
        top=top - padding,
        right=left + block.width + padding,
        bottom=top + block.height + padding,
        tail=tail,
    )


def _draw_tail(
    draw: ImageDraw.ImageDraw,
    box: BubbleBox,
    *,
    size: int,
    fill: tuple[int, int, int, int],
) -> None:
    """
    The little point that says who is talking.

    It sits a quarter of the way along the box rather than in the middle,
    because a tail in the centre reads as a label and a tail off to one side
    reads as a person.
    """
    if box.tail == "down":
        y = box.bottom
        direction = 1.0
    else:
        y = box.top
        direction = -1.0

    x = box.left + (box.right - box.left) * 0.32
    spread = size * 0.55
    draw.polygon(
        [
            (x - spread, y - size * 0.18 * direction),
            (x + spread, y - size * 0.18 * direction),
            (x + size * 0.35, y + size * direction),
        ],
        fill=fill,
    )


def _draw_speech_bubble(
    draw: ImageDraw.ImageDraw,
    box: BubbleBox,
    *,
    padding: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    width: int,
) -> None:
    radius = min((box.bottom - box.top) / 2, padding * 1.5)
    draw.rounded_rectangle(
        [box.left, box.top, box.right, box.bottom],
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )
    _draw_tail(draw, box, size=padding * 1.3, fill=fill)
    if outline[3]:
        # Re-stroke the tail's edges; `rounded_rectangle` never drew them.
        spread = padding * 1.3 * 0.55
        x = box.left + (box.right - box.left) * 0.32
        y = box.bottom if box.tail == "down" else box.top
        direction = 1.0 if box.tail == "down" else -1.0
        draw.line(
            [
                (x - spread, y - padding * 1.3 * 0.18 * direction),
                (x + padding * 1.3 * 0.35, y + padding * 1.3 * direction),
            ],
            fill=outline,
            width=width,
        )
        draw.line(
            [
                (x + spread, y - padding * 1.3 * 0.18 * direction),
                (x + padding * 1.3 * 0.35, y + padding * 1.3 * direction),
            ],
            fill=outline,
            width=width,
        )


def _draw_thought_cloud(
    draw: ImageDraw.ImageDraw,
    box: BubbleBox,
    *,
    padding: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    width: int,
) -> None:
    """
    A cloud of circles with a trail of shrinking puffs towards the thinker.

    Drawn as overlapping ellipses rather than one ragged outline because
    overlapping ellipses *are* the cartoon convention, and because a cloud
    with a hole in it is a hole in a caption.
    """
    left, top, right, bottom = box.left, box.top, box.right, box.bottom
    centre_x = (left + right) / 2
    centre_y = (top + bottom) / 2
    puff = padding * 1.1

    for point_x, point_y, scale in (
        (centre_x - (right - left) * 0.30, centre_y + (bottom - top) * 0.22, 1.05),
        (centre_x + (right - left) * 0.30, centre_y + (bottom - top) * 0.20, 1.00),
        (centre_x - (right - left) * 0.28, centre_y - (bottom - top) * 0.20, 0.92),
        (centre_x + (right - left) * 0.30, centre_y - (bottom - top) * 0.22, 0.96),
        (centre_x, centre_y + (bottom - top) * 0.28, 0.88),
    ):
        radius = puff * scale
        draw.ellipse(
            [point_x - radius, point_y - radius, point_x + radius, point_y + radius],
            fill=fill,
            outline=outline,
            width=width,
        )

    # The trail: three smaller puffs marching towards the tail.
    x = left + (right - left) * 0.34
    y = bottom if box.tail == "down" else top
    step = padding * 0.95
    direction = 1.0 if box.tail == "down" else -1.0
    for index, scale in enumerate((0.55, 0.40, 0.26)):
        radius = puff * scale
        centre = y + step * (index + 1) * direction
        draw.ellipse(
            [x - radius, centre - radius, x + radius, centre + radius],
            fill=fill,
            outline=outline,
            width=width,
        )


def _draw_shout(
    draw: ImageDraw.ImageDraw,
    box: BubbleBox,
    *,
    padding: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    width: int,
) -> None:
    """
    A starburst: the caption's own box with spikes cut into it.

    The spikes are placed on a fixed phase rather than at random so that a
    caption re-rendered after an unrelated edit comes back identical.
    """
    centre_x = (box.left + box.right) / 2
    centre_y = (box.top + box.bottom) / 2
    # Twenty spikes puts a point at each of the four cardinal directions, so
    # the burst always reaches past the box it is cut out of; the spokes
    # between them fall back in towards the words.
    spikes = 20
    half_x = (box.right - box.left) / 2
    half_y = (box.bottom - box.top) / 2
    points: list[tuple[float, float]] = []
    for index in range(spikes * 2):
        angle = math.pi * index / spikes - math.pi / 2
        spike = index % 2 == 0
        radius_x = half_x + padding * 0.55 if spike else half_x * 0.84
        radius_y = half_y + padding * 0.55 if spike else half_y * 0.84
        points.append(
            (centre_x + math.cos(angle) * radius_x,
             centre_y + math.sin(angle) * radius_y)
        )

    draw.polygon(points, fill=fill, outline=outline)
    if outline[3] and width > 1:
        draw.line(points + [points[0]], fill=outline, width=width)

    return box


def draw_frame(
    draw: ImageDraw.ImageDraw,
    block: TextBlock,
    left: float,
    top: float,
    *,
    kind: str,
    padding: int,
    tail: str,
    fill: str,
    outline: str,
    width: int,
) -> BubbleBox:
    """
    Draw the caption's box and return where it went.

    Returns the box even when nothing was drawn, so the caller can measure
    the layer the same way whether there is a frame or not.
    """
    box = _bubble_box(block, left, top, padding=padding, tail=tail)
    if kind in ("none", ""):
        return box

    fill_rgba = (*parse_hex_colour(fill, default=(255, 255, 255)), 235)
    outline_rgba = (*parse_hex_colour(outline, default=(16, 16, 16)), 255)
    line_width = max(width, 2)

    if kind == "speech":
        _draw_speech_bubble(
            draw, box, padding=padding, fill=fill_rgba,
            outline=outline_rgba, width=line_width,
        )
    elif kind == "thought":
        _draw_thought_cloud(
            draw, box, padding=padding, fill=fill_rgba,
            outline=outline_rgba, width=line_width,
        )
    elif kind == "shout":
        _draw_shout(
            draw, box, padding=padding, fill=fill_rgba,
            outline=outline_rgba, width=line_width,
        )
    return box


def _draw(
    layer: Image.Image,
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    anchor: str,
    xy: tuple[float, float],
    colour: str,
    stroke_colour: str,
    stroke_width: int,
) -> None:
    """Draw one run of text onto a layer."""
    ImageDraw.Draw(layer).text(
        xy,
        text,
        font=font,
        fill=(*parse_hex_colour(colour), 255),
        anchor=anchor,
        stroke_width=stroke_width,
        stroke_fill=(*parse_hex_colour(stroke_colour, default=(0, 0, 0)), 255),
    )


def _measure(
    text: str, font: ImageFont.FreeTypeFont, stroke_width: int
) -> tuple[int, int]:
    left, top, right, bottom = font.getbbox(text)
    return (
        right - left + 2 * stroke_width,
        bottom - top + 2 * stroke_width,
    )


def render_text_layer(
    *,
    text: str,
    font_path: Path,
    font_size: int,
    colour: str,
    stroke_colour: str,
    stroke_width: int,
    position: str,
    frame_size: tuple[int, int],
    destination: Path,
    max_width: int | None = None,
    frame: str = "none",
    frame_fill: str = "#FFFFFF",
    frame_stroke: str = "#101010",
    position_xy: tuple[float, float] | None = None,
) -> TextLayer:
    """
    Draw `text` onto a transparent frame-sized layer and save it as PNG.

    `max_width` is the width the caption may occupy; longer text wraps onto
    further lines instead of running off the frame edge.  `frame` names the
    box to draw around it -- `none`, `speech`, `thought` or `shout` --
    and `position_xy` moves the caption's centre to a fraction of the frame.
    """
    frame_width, frame_height = frame_size
    padding = bubble_inset(font_size, frame)
    font = ImageFont.truetype(str(font_path), font_size)

    wrap_width = None
    if max_width is not None:
        wrap_width = max(int(max_width) - 2 * padding, font_size)
    block = layout_text(
        text, font, stroke_width=stroke_width, max_width=wrap_width
    )
    horizontal, left, top, _anchor_y = block_origin(
        block,
        position,
        (frame_width, frame_height),
        inset=padding,
        position_xy=position_xy,
    )

    layer = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = draw_frame(
        draw,
        block,
        left,
        top,
        kind=frame,
        padding=padding,
        tail=tail_direction(position),
        fill=frame_fill,
        outline=frame_stroke,
        width=max(stroke_width, 2),
    )

    for index, line in enumerate(block.lines):
        _draw(
            layer,
            line,
            font=font,
            anchor=f"l{horizontal[1]}",
            xy=(
                    left,
                    top + stroke_width + index * block.line_height,
                ),
            colour=colour,
            stroke_colour=stroke_colour,
            stroke_width=stroke_width,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    layer.save(destination, format="PNG", compress_level=6)

    if frame in ("none", ""):
        width, height = block.width, block.height
    else:
        width = int(round(box.right - box.left))
        height = int(round(box.bottom - box.top))
    return TextLayer(path=destination, text_width=width, text_height=height)


def _visible_lines(
    block: TextBlock, revealed: int
) -> tuple[list[str], int]:
    """
    The lines to draw once `revealed` characters have appeared.

    Whole lines come first and the cut falls inside one line, which is what
    makes a multi-line typewriter read as typing rather than as a paragraph
    being replaced one character at a time.
    """
    lines: list[str] = []
    budget = revealed
    for line in block.lines:
        if budget <= 0:
            break
        if len(line) <= budget:
            lines.append(line)
            budget -= len(line)
            continue
        lines.append(line[:budget])
        budget = 0
    return lines or [""], len(lines)


def render_typewriter_layers(
    *,
    text: str,
    font_path: Path,
    font_size: int,
    colour: str,
    stroke_colour: str,
    stroke_width: int,
    position: str,
    frame_size: tuple[int, int],
    steps: int,
    destination_dir: Path,
    stem: str,
    max_width: int | None = None,
    frame: str = "none",
    frame_fill: str = "#FFFFFF",
    frame_stroke: str = "#101010",
    position_xy: tuple[float, float] | None = None,
) -> list[TextLayer]:
    """
    Render `text` as a sequence of growing prefixes: the typewriter reveal.

    Each step draws the caption as it stands that many characters in.  The
    block is laid out from the **whole** text, so the lines never move as
    the reveal grows -- which is the difference between typing and watching
    the caption re-centre itself on every keystroke.

    The final prefix is the whole text, so the reveal ends on the complete
    caption and the layer can then simply be held.
    """
    frame_width, frame_height = frame_size
    padding = bubble_inset(font_size, frame)
    font = ImageFont.truetype(str(font_path), font_size)

    wrap_width = None
    if max_width is not None:
        wrap_width = max(int(max_width) - 2 * padding, font_size)
    block = layout_text(
        text, font, stroke_width=stroke_width, max_width=wrap_width
    )
    horizontal, left, top, _anchor_y = block_origin(
        block,
        position,
        (frame_width, frame_height),
        inset=padding,
        position_xy=position_xy,
    )

    box = BubbleBox(
        left=left - padding,
        top=top - padding,
        right=left + block.width + padding,
        bottom=top + block.height + padding,
        tail=tail_direction(position),
    )

    steps = max(2, min(steps, len(text)))
    destination_dir.mkdir(parents=True, exist_ok=True)

    layers: list[TextLayer] = []
    for index in range(steps):
        revealed = max(1, round(len(text) * (index + 1) / steps))
        lines, _drawn = _visible_lines(block, revealed)

        layer = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        # The box is drawn whole from the first keystroke: a bubble that
        # grows as it is typed reads as a bubble being written, not as
        # somebody speaking.
        draw_frame(
            draw,
            block,
            left,
            top,
            kind=frame,
            padding=padding,
            tail=tail_direction(position),
            fill=frame_fill,
            outline=frame_stroke,
            width=max(stroke_width, 2),
        )

        for line_index, line in enumerate(lines):
            _draw(
                layer,
                line,
                font=font,
                anchor=f"l{horizontal[1]}",
                xy=(
                    left,
                    top + stroke_width + line_index * block.line_height,
                ),
                colour=colour,
                stroke_colour=stroke_colour,
                stroke_width=stroke_width,
            )

        destination = destination_dir / f"{stem}_tw{index:02d}.png"
        layer.save(destination, format="PNG", compress_level=6)
        if frame in ("none", ""):
            width, height = block.width, block.height
        else:
            width = int(round(box.right - box.left))
            height = int(round(box.bottom - box.top))
        layers.append(
            TextLayer(
                path=destination,
                text_width=width,
                text_height=height,
            )
        )

    return layers