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

Each layer is a full-frame transparent PNG with the text already in its
final place, so compositing is a plain `overlay=0:0` and needs no position
maths in ffmpeg.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Distance from the frame edge for non-centred placements.
TEXT_MARGIN_FRACTION = 0.05

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
    position: str, frame_size: tuple[int, int]
) -> tuple[str, tuple[float, float]]:
    """PIL anchor code and the point it is anchored at."""
    width, height = frame_size
    margin_x = width * TEXT_MARGIN_FRACTION
    margin_y = height * TEXT_MARGIN_FRACTION
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
) -> TextLayer:
    """
    Draw `text` onto a transparent frame-sized layer and save it as PNG.

    `text` may be a prefix of the full overlay, which is how the typewriter
    animation is built: a sequence of growing prefixes, each shown from a
    later moment.
    """
    frame_width, frame_height = frame_size

    layer = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    font = ImageFont.truetype(str(font_path), font_size)
    anchor, xy = _anchor_xy(position, frame_size)

    _draw(
        layer,
        text,
        font=font,
        anchor=anchor,
        xy=xy,
        colour=colour,
        stroke_colour=stroke_colour,
        stroke_width=stroke_width,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    layer.save(destination, format="PNG", compress_level=6)

    width, height = _measure(text, font, stroke_width)
    return TextLayer(path=destination, text_width=width, text_height=height)


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
) -> list[TextLayer]:
    """
    Render `text` as a sequence of growing prefixes: the typewriter reveal.

    Each prefix is drawn from a **fixed left edge** rather than the usual
    anchor.  Anchoring every prefix on the centre would make the growing
    text creep outwards from the middle, which reads as a wobble instead
    of a reveal, so the left edge is placed where the *finished* text will
    start and stays there.

    The final prefix is the whole text, so the reveal ends on the complete
    line and the layer can then simply be held.
    """
    frame_width, frame_height = frame_size
    font = ImageFont.truetype(str(font_path), font_size)

    full_width, full_height = _measure(text, font, stroke_width)
    anchor, (anchor_x, anchor_y) = _anchor_xy(position, frame_size)
    vertical = anchor[1]

    if anchor[0] == "m":
        left = anchor_x - full_width / 2
    elif anchor[0] == "r":
        left = anchor_x - full_width
    else:
        left = anchor_x

    steps = max(2, min(steps, len(text)))
    destination_dir.mkdir(parents=True, exist_ok=True)

    layers: list[TextLayer] = []
    for index in range(steps):
        length = max(1, round(len(text) * (index + 1) / steps))
        prefix = text[:length]

        layer = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
        _draw(
            layer,
            prefix,
            font=font,
            anchor=f"l{vertical}",
            xy=(left, anchor_y),
            colour=colour,
            stroke_colour=stroke_colour,
            stroke_width=stroke_width,
        )

        destination = destination_dir / f"{stem}_tw{index:02d}.png"
        layer.save(destination, format="PNG", compress_level=6)
        layers.append(
            TextLayer(
                path=destination,
                text_width=full_width,
                text_height=full_height,
            )
        )

    return layers
