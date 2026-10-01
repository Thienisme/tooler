#!/usr/bin/env python3
"""
Draw the storytelling-frame overlay PNG.

The frame is a rounded panel sized to `--width`x`--height` pixels, painted
with an opaque mat fill and a style-specific border treatment.  It is
composited at exactly (x*W, y*H) by the pipeline, so this PNG never needs to
know the output resolution -- it only needs its own size, which assembly
stores in `story_frame.json` next to it.

Each style also carries its own `inset`: the fraction of the panel's short
side kept as visible mat around the artwork.  The pipeline reads it via
`story_inset_for_style` (filters.py), so the art window always matches what
the frame promises -- a polaroid keeps a fat lip at the bottom, a retro TV
sits behind a thick walnut bezel.

Styles:
  border     the classic cream mat with a dark keyline
  none       the same panel, no keyline
  polaroid   instant-film card: fat white lip under the picture
  tv_retro   cartoon TV: walnut cabinet, screen, knobs, legs
  night      deep-navy mat with a silver keyline, for sad/spooky scenes

Usage:
  python3 tools/make_story_frame.py --size 1188x1584 \
      --out assets/frames/story_frame_border.png [--style border]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw

# Fraction of the panel's own short side the corner radius takes.  A radius
# this large reads as a soft storytelling panel rather than as a UI card.
RADIUS_SHARE = 0.045

# The fill under the artwork: fully opaque.  The pipeline crops the scene
# into the panel's window and composites it over this fill, so the fill is
# the visible mat around the picture -- any translucency here would only
# let the dark backdrop bleed through the card.
FILL_ALPHA = 255

BORDER_WIDTH_SHARE = 0.014


def _rounded_mask(width: int, height: int) -> Image.Image:
    """Antialiased rounded-rectangle alpha mask covering the whole panel."""
    radius = max(8, int(round(min(width, height) * RADIUS_SHARE)))
    # Supersample the circle arithmetic -- PIL's arc edges are jaggy at 1x.
    mask = Image.new("L", (width * 2, height * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * 2 - 1, height * 2 - 1), radius=radius * 2, fill=255
    )
    return mask.resize((width, height), Image.LANCZOS)


def _stroke(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    radius: int,
    *,
    rgb: tuple[int, int, int],
    width_share: float,
    inset_share: float = 0.0,
) -> None:
    """One rounded-rectangle outline, inset from the panel edge."""
    inset = max(4, int(round(min(width, height) * inset_share)))
    stroke = max(3, int(round(min(width, height) * width_share)))
    draw.rounded_rectangle(
        (inset, inset, width - 1 - inset, height - 1 - inset),
        radius=max(radius - inset, 4),
        outline=rgb + (255,),
        width=stroke,
    )


def _draw_border(
    draw: ImageDraw.ImageDraw, width: int, height: int, radius: int
) -> None:
    _stroke(
        draw, width, height, radius,
        rgb=(43, 52, 63), width_share=BORDER_WIDTH_SHARE, inset_share=0.012,
    )


def _draw_none(
    draw: ImageDraw.ImageDraw, width: int, height: int, radius: int
) -> None:
    del draw, width, height, radius  # a bare panel: nothing to draw


def _draw_polaroid(
    draw: ImageDraw.ImageDraw, width: int, height: int, radius: int
) -> None:
    # Instant film: soft grey keyline for the card edge, plus the signature
    # groove that separates the picture window from the fat bottom lip.
    _stroke(
        draw, width, height, radius,
        rgb=(150, 146, 138), width_share=0.005, inset_share=0.008,
    )
    groove_y = int(round(height * 0.775))
    groove = max(3, int(round(min(width, height) * 0.006)))
    draw.line(
        (int(width * 0.035), groove_y, int(width * 0.965), groove_y),
        fill=(208, 203, 193, 255),
        width=groove,
    )


def _draw_tv_retro(
    draw: ImageDraw.ImageDraw, width: int, height: int, radius: int
) -> None:
    """A cartoon television set around the artwork window (v3, wide screen).

    Cherry-red toy cabinet, cream faceplate, and a screen that takes the
    whole upper front -- the storytelling area wins over the furniture.
    The controls live in a slim strip along the BOTTOM: speaker grille,
    two knobs, and a pilot-lamp socket (the blinking green/red bulb is
    overlaid by the pipeline at STYLE_LAMPS fractions -- the socket is
    painted dark here so the off state reads as off).  Rabbit ears on
    top, stubby feet below.  Drawn shapes must respect the tv_retro art
    window (STYLES["tv_retro"]["inset"]) -- that is the crop the pipeline
    applies to the scene.
    """
    del radius  # the cabinet has its own chunky corners
    W, H = float(width), float(height)
    px = lambda frac: int(round(W * frac))  # noqa: E731
    py = lambda frac: int(round(H * frac))  # noqa: E731

    RED = (217, 74, 57, 255)
    RED_DARK = (140, 40, 30, 255)
    CREAM = (248, 238, 214, 255)
    DARK = (60, 30, 24, 255)
    BEZEL = (52, 44, 40, 255)
    GRILLE = (150, 44, 36, 255)

    # -- rabbit ears ----------------------------------------------------
    base_x, base_y = px(0.40), py(0.024)
    ear_w = max(px(0.009), 4)
    ball = max(px(0.013), 5)
    for tip_x, tip_y in ((0.16, 0.004), (0.60, 0.002)):
        draw.line((base_x, base_y, px(tip_x), py(tip_y)), fill=DARK, width=ear_w)
        draw.ellipse((px(tip_x) - ball, py(tip_y) - ball,
                      px(tip_x) + ball, py(tip_y) + ball), fill=RED)
    draw.ellipse((base_x - ball, base_y - ball,
                  base_x + ball, base_y + ball), fill=DARK)

    # -- cabinet + faceplate ----------------------------------------------
    draw.rounded_rectangle(
        (px(0.006), py(0.018), px(0.994), py(0.982)),
        radius=px(0.05), fill=RED, outline=RED_DARK, width=max(px(0.007), 3),
    )
    draw.rounded_rectangle(
        (px(0.02), py(0.032), px(0.98), py(0.968)),
        radius=px(0.035), fill=CREAM,
    )
    # -- screen bezel (dark ring around the window) -----------------------
    gap = px(0.014)
    draw.rounded_rectangle(
        (px(0.04) - gap, py(0.05) - gap, px(0.96) + gap, py(0.845) + gap),
        radius=px(0.028), fill=BEZEL,
    )

    # -- bottom control strip ---------------------------------------------
    # Speaker grille: four slats on the left.
    for i in range(4):
        y0 = py(0.885 + i * 0.017)
        draw.rounded_rectangle(
            (px(0.055), y0, px(0.40), y0 + py(0.011)),
            radius=py(0.005), fill=GRILLE,
        )
    # Two knobs with pointer ticks.
    for kx, angle in ((0.50, 0.7), (0.615, -0.9)):
        r = px(0.034)
        draw.ellipse((px(kx) - r, py(0.915) - r, px(kx) + r, py(0.915) + r),
                     fill=CREAM, outline=DARK, width=max(px(0.005), 2))
        draw.line((px(kx), py(0.915),
                   px(kx) + int(r * math.sin(angle)),
                   py(0.915) - int(r * math.cos(angle))),
                  fill=DARK, width=max(px(0.007), 2))
    # Pilot-lamp socket: dark square; the blinking bulb is an overlay.
    socket = px(0.026)
    draw.rounded_rectangle(
        (px(0.892) - socket, py(0.915) - socket,
         px(0.892) + socket, py(0.915) + socket),
        radius=px(0.010), fill=DARK,
    )

    # -- feet -------------------------------------------------------------
    for fx in (0.10, 0.70):
        draw.rounded_rectangle(
            (px(fx), py(0.958), px(fx + 0.10), py(0.995)),
            radius=px(0.012), fill=DARK,
        )


def _draw_night(
    draw: ImageDraw.ImageDraw, width: int, height: int, radius: int
) -> None:
    # Deep navy mat with a cold silver keyline and a faint inner echo --
    # sober frame for sad or spooky telling.
    _stroke(
        draw, width, height, radius,
        rgb=(190, 197, 212), width_share=0.008, inset_share=0.010,
    )
    _stroke(
        draw, width, height, radius,
        rgb=(60, 68, 92), width_share=0.004, inset_share=0.026,
    )


# `inset` is the artwork window inset as a fraction of the panel's short
# side.  It must match what `story_inset_for_style()` returns in
# filters.py -- the crop the pipeline applies to the Ken Burns picture.
STYLES: dict[str, dict] = {
    "border": {
        "readme": "cream mat with a dark keyline",
        "fill_rgb": (250, 247, 240),
        "inset": 0.035,
        "draw": _draw_border,
    },
    "none": {
        "readme": "bare rounded panel, no keyline",
        "fill_rgb": (250, 247, 240),
        "inset": 0.035,
        "draw": _draw_none,
    },
    "polaroid": {
        "readme": "instant-film card with a fat bottom lip",
        "fill_rgb": (247, 243, 235),
        "inset": 0.045,
        "draw": _draw_polaroid,
    },
    "tv_retro": {
        "readme": "cartoon toy TV: cherry-red cabinet, big screen, knobs",
        "fill_rgb": (217, 74, 57),
        "inset": {"t": 0.06, "b": 0.07, "l": 0.045, "r": 0.22},
        "draw": _draw_tv_retro,
    },
    "night": {
        "readme": "deep-navy mat with a silver keyline",
        "fill_rgb": (24, 28, 40),
        "inset": 0.035,
        "draw": _draw_night,
    },
}


def draw_frame_png(
    *,
    width: int,
    height: int,
    style: str,
    destination: Path,
) -> Path:
    """One rounded panel with a style-specific treatment, as an RGBA PNG."""
    spec = STYLES[style]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    mask = _rounded_mask(width, height)

    fill = Image.new("RGBA", (width, height), spec["fill_rgb"] + (FILL_ALPHA,))
    image = Image.composite(fill, image, mask)

    radius = max(8, int(round(min(width, height) * RADIUS_SHARE)))
    draw = ImageDraw.Draw(image)
    spec["draw"](draw, width, height, radius)

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", compress_level=6)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", default="1188x1584", help="WxH of the PNG")
    parser.add_argument(
        "--style", choices=sorted(STYLES), default="border",
        help="frame edge style (must match script.json's story_frame.style)",
    )
    parser.add_argument(
        "--out", default="assets/frames/story_frame_border.png",
        help="destination PNG",
    )
    args = parser.parse_args()

    width_s, _, height_s = args.size.lower().partition("x")
    width, height = int(width_s), int(height_s)
    if width <= 0 or height <= 0:
        raise SystemExit(f"--size must be WxH, got {args.size!r}")

    out = draw_frame_png(
        width=width,
        height=height,
        style=args.style,
        destination=Path(args.out),
    )
    print(f"frame PNG: {out} ({width}x{height}, style={args.style})")


if __name__ == "__main__":
    main()
