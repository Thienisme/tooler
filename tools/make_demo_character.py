#!/usr/bin/env python3
"""
Draw a demo cartoon host, so character effects can be tried before the real
artwork exists.

The pipeline wants one thing from a character: a PNG with a transparent
background, exported with the whole figure inside the frame.  This makes a
few of those -- the same figure in three poses -- which is enough to run the
preview tool, the tests, and a demo project.

It is a placeholder, not art direction.  When the real character arrives,
point `image_file` at it and delete this file's output; nothing else in the
pipeline changes.

    python3 tools/make_demo_character.py                     # assets/characters/
    python3 tools/make_demo_character.py --out /tmp/host
    python3 tools/make_demo_character.py --height 1600
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# A small palette, so the demo reads as one character in different moods
# rather than as three unrelated drawings.
SKIN = (244, 206, 160, 255)
HAIR = (58, 42, 36, 255)
SHIRT = (58, 132, 214, 255)
SHIRT_DARK = (40, 100, 172, 255)
TROUSERS = (46, 56, 74, 255)
SHOES = (26, 30, 38, 255)
LINE = (32, 36, 44, 255)
MOUTH = (150, 66, 66, 255)

# The figure is drawn on a 1000x1400 canvas and scaled at the end, so every
# coordinate below is readable as a percentage of the body.
CANVAS = (1000, 1400)


def _draw_host(draw: ImageDraw.ImageDraw, *, pose: str) -> None:
    """One figure, in one pose, on a transparent canvas."""
    # Legs and shoes.
    draw.rounded_rectangle((360, 980, 470, 1290), radius=40, fill=TROUSERS)
    draw.rounded_rectangle((530, 980, 640, 1290), radius=40, fill=TROUSERS)
    draw.rounded_rectangle((330, 1265, 495, 1330), radius=32, fill=SHOES)
    draw.rounded_rectangle((505, 1265, 670, 1330), radius=32, fill=SHOES)

    # Body.
    draw.rounded_rectangle((320, 470, 680, 1010), radius=90, fill=SHIRT)
    draw.rounded_rectangle((320, 470, 680, 620), radius=90, fill=SHIRT_DARK)

    # Arms, by pose.
    if pose == "point":
        # One arm up and out, pointing at whatever is being explained.
        draw.rounded_rectangle((640, 380, 730, 560), radius=45, fill=SHIRT)
        draw.ellipse((690, 300, 800, 400), fill=SKIN)
        draw.rounded_rectangle((200, 560, 320, 900), radius=50, fill=SHIRT)
        draw.ellipse((170, 860, 300, 960), fill=SKIN)
    elif pose == "shrug":
        draw.rounded_rectangle((640, 470, 790, 640), radius=45, fill=SHIRT)
        draw.ellipse((720, 560, 860, 680), fill=SKIN)
        draw.rounded_rectangle((210, 470, 360, 640), radius=45, fill=SHIRT)
        draw.ellipse((140, 560, 280, 680), fill=SKIN)
    else:
        draw.rounded_rectangle((210, 520, 330, 900), radius=50, fill=SHIRT)
        draw.ellipse((180, 850, 310, 950), fill=SKIN)
        draw.rounded_rectangle((670, 520, 790, 900), radius=50, fill=SHIRT)
        draw.ellipse((690, 850, 820, 950), fill=SKIN)

    # Head.
    draw.ellipse((330, 180, 670, 520), fill=SKIN)
    draw.polygon(
        [(320, 300), (360, 150), (500, 120), (650, 165), (690, 320), (640, 250),
         (500, 215), (360, 250)],
        fill=HAIR,
    )

    # Eyes: a brow per pose, so the mood is visible at a glance.
    draw.ellipse((420, 320, 470, 375), fill=LINE)
    draw.ellipse((540, 320, 590, 375), fill=LINE)
    if pose == "shrug":
        draw.line((410, 295, 480, 275), fill=HAIR, width=14)
        draw.line((530, 275, 600, 295), fill=HAIR, width=14)
    elif pose == "point":
        draw.line((410, 285, 480, 300), fill=HAIR, width=14)
        draw.line((530, 300, 600, 285), fill=HAIR, width=14)
    else:
        draw.line((410, 295, 480, 295), fill=HAIR, width=14)
        draw.line((530, 295, 600, 295), fill=HAIR, width=14)

    # Mouth: open when pointing (talking), flat when shrugging.
    if pose == "shrug":
        draw.line((470, 440, 545, 440), fill=MOUTH, width=14)
    elif pose == "point":
        draw.ellipse((460, 410, 550, 470), fill=MOUTH)
    else:
        draw.arc((455, 390, 560, 470), start=20, end=160, fill=MOUTH, width=14)
        draw.ellipse((490, 425, 520, 455), fill=MOUTH)

    # A soft shadow at the feet, which keeps a walking character from
    # looking like it is floating once it is composited on a background.
    draw.ellipse((330, 1290, 670, 1360), fill=(0, 0, 0, 60))


def build_character(*, pose: str, height: int, destination: Path) -> Path:
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    _draw_host(ImageDraw.Draw(canvas), pose=pose)

    bounding_box = canvas.getbbox()
    if bounding_box is not None:
        canvas = canvas.crop(bounding_box)

    width = max(2, int(round(canvas.width * height / canvas.height)))
    canvas = canvas.resize((width, height), Image.LANCZOS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG", compress_level=6)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draw a demo cartoon character (placeholder artwork)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "assets" / "characters",
        help="directory to write the PNGs into (default: assets/characters)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1200,
        help="height of the exported figure in pixels (default: 1200)",
    )
    parser.add_argument(
        "--name",
        default="host",
        help="file stem for the poses (default: host)",
    )
    args = parser.parse_args()

    written = []
    for pose in ("idle", "point", "shrug"):
        destination = args.out / f"{args.name}_{pose}.png"
        build_character(
            pose=pose, height=args.height, destination=destination
        )
        written.append(destination)

    print(f"{len(written)} pose(s) written to {args.out}:")
    for path in written:
        with Image.open(path) as image:
            print(f"  {path.name}  {image.width}x{image.height}  {image.mode}")
    print()
    print(
        "Use one in script.json as \"image_file\": "
        f"\"assets/characters/{written[0].name}\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
