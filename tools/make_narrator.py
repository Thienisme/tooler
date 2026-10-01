#!/usr/bin/env python3
"""
Draw the built-in storyteller narrator, frame by frame, with PIL.

Four transparent PNGs land in assets/narrator/:

  narrator_resting.png   standing, both arms down (the resting look)
  narrator_point.png     the same figure, one arm extended pointing LEFT --
                         toward the story frame, which sits on the screen's
                         left while the narrator stands right
  narrator_mouth_closed.png  the shut mouth, composited over the face
  narrator_mouth_open.png    the open mouth (the flap frame)

The figure is a deliberate left-facing silhouette: everything that must
line up across frames -- head centre, shoulders, body -- is derived from
shared constants, so the pose swap never wobbles.  The head carries no
mouth at all; the mouth lives entirely in the two patch PNGs, which the
registry composites onto the face at `mouth.anchor`.  The anchor below is
the fraction of the TRIMMED artwork the patch centres on -- measure it
against the resting frame this tool writes.

The palette is picked to sit pleasantly on the story frame's cream panel
and on dark backgrounds alike.  Regenerate after changing anything:

    python3 tools/make_narrator.py [--out assets/narrator] [--height 900]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# Everything is laid out on a 1000x1000 design grid, drawn on its own
# square canvas, then pasted onto a WIDER final canvas so the pointing arm
# (which reaches past the figure's left edge) never clips.
GRID = 1000
CANVAS_ASPECT = 1.3   # final width = height * this
FIGURE_SHIFT = 0.22   # figure's x offset on the final canvas, in heights

# -- palette ----------------------------------------------------------------
COAT = (38, 60, 92, 255)        # deep blue coat (the body)
COAT_DARK = (28, 44, 68, 255)   # coat shading / legs
SKIN = (244, 205, 168, 255)     # face and pointing hand
HAIR = (43, 34, 30, 255)        # dark hair
GLASSES = (24, 28, 34, 255)     # round glasses, reads as "storyteller"
SHOE = (18, 20, 26, 255)
MOUTH_LIP = (90, 44, 44, 255)   # the patch lips
MOUTH_CAVITY = (52, 24, 26, 255)  # open-mouth interior

# -- geometry (fractions of the design grid, y down) ------------------------
HEAD_CX = 0.46   # slightly left of centre: the figure leans toward the frame
HEAD_CY = 0.20
HEAD_R = 0.115
BODY_TOP = 0.315
BODY_BOTTOM = 0.80
BODY_LEFT = 0.335
BODY_RIGHT = 0.585
LEG_BOTTOM = 0.955
LEG_W = 0.085
FOOT_W = 0.125

ARM_W = 0.055


def _scale(height: int) -> float:
    return height / GRID


def _new_canvas(height: int) -> Image.Image:
    width = int(round(height * CANVAS_ASPECT))
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def _finish(design: Image.Image, height: int) -> Image.Image:
    """Resize the 1000px design to `height` and paste it onto the wide canvas."""
    design = design.resize((height, height), Image.LANCZOS)
    canvas = _new_canvas(height)
    canvas.alpha_composite(design, (int(round(FIGURE_SHIFT * height)), 0))
    return canvas


def _draw_shared(image: Image.Image) -> None:
    """Head, body and legs -- identical in every frame, in grid units."""
    draw = ImageDraw.Draw(image)

    cx, cy, r = HEAD_CX * GRID, HEAD_CY * GRID, HEAD_R * GRID

    # Neck and coat: a soft trapezoid, wider at the hem.
    top_y = BODY_TOP * GRID
    hem_y = BODY_BOTTOM * GRID
    draw.polygon(
        [
            (BODY_LEFT * GRID + 0.03 * GRID, top_y),
            (BODY_RIGHT * GRID - 0.03 * GRID, top_y),
            (BODY_RIGHT * GRID, hem_y),
            (BODY_LEFT * GRID, hem_y),
        ],
        fill=COAT,
    )

    # Legs and shoes.
    for leg_x in (0.395, 0.50):
        draw.rectangle(
            (
                leg_x * GRID - LEG_W * GRID / 2,
                hem_y - 4,
                leg_x * GRID + LEG_W * GRID / 2,
                LEG_BOTTOM * GRID,
            ),
            fill=COAT_DARK,
        )
        # A shoe toes LEFT: the figure faces the frame.
        draw.polygon(
            [
                (leg_x * GRID + LEG_W * GRID / 2, LEG_BOTTOM * GRID - 6),
                (leg_x * GRID - LEG_W * GRID / 2, LEG_BOTTOM * GRID - 6),
                (leg_x * GRID - FOOT_W * GRID / 2, LEG_BOTTOM * GRID),
            ],
            fill=SHOE,
        )

    # Head.
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=SKIN)
    # Hair: a cap over the top-left of the skull.
    draw.pieslice(
        (cx - r, cy - r, cx + r, cy + r), start=150, end=330, fill=HAIR
    )
    # Round glasses, looking LEFT.
    eye_y = cy + 0.12 * r
    for eye_dx in (-0.42, 0.05):
        ex = cx + eye_dx * r
        er = 0.30 * r
        draw.ellipse(
            (ex - er, eye_y - er, ex + er, eye_y + er),
            outline=GLASSES,
            width=max(2, int(0.09 * r)),
        )
    # The bridge.
    draw.line(
        (cx - 0.12 * r, eye_y, cx + 0.05 * r, eye_y),
        fill=GLASSES,
        width=max(2, int(0.07 * r)),
    )


def _draw_resting_arm(draw: ImageDraw.Draw) -> None:
    """The far arm hangs down along the body, in every frame."""
    x = BODY_RIGHT * GRID - 0.02 * GRID
    draw.rounded_rectangle(
        (
            x,
            BODY_TOP * GRID + 0.02 * GRID,
            x + ARM_W * GRID,
            BODY_BOTTOM * GRID - 0.03 * GRID,
        ),
        radius=ARM_W * GRID / 2,
        fill=COAT_DARK,
    )
    # The hand.
    draw.ellipse(
        (
            x,
            BODY_BOTTOM * GRID - 0.05 * GRID,
            x + ARM_W * GRID,
            BODY_BOTTOM * GRID + 0.01 * GRID,
        ),
        fill=SKIN,
    )


def draw_resting(height: int) -> Image.Image:
    design = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    _draw_shared(design)
    _draw_resting_arm(ImageDraw.Draw(design))

    # The near arm hangs slightly away from the body, hand open -- a relaxed
    # storyteller between beats.
    draw = ImageDraw.Draw(design)
    ax = BODY_LEFT * GRID + 0.01 * GRID
    draw.rounded_rectangle(
        (
            ax - ARM_W * GRID,
            BODY_TOP * GRID + 0.04 * GRID,
            ax,
            BODY_BOTTOM * GRID - 0.06 * GRID,
        ),
        radius=ARM_W * GRID / 2,
        fill=COAT,
    )
    draw.ellipse(
        (
            ax - ARM_W * GRID - 0.01 * GRID,
            BODY_BOTTOM * GRID - 0.08 * GRID,
            ax + 0.01 * GRID,
            BODY_BOTTOM * GRID - 0.02 * GRID,
        ),
        fill=SKIN,
    )
    return _finish(design, height)


def draw_pointing(height: int) -> Image.Image:
    """The near arm swings up and out: a straight finger aiming LEFT."""
    design = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    _draw_shared(design)
    _draw_resting_arm(ImageDraw.Draw(design))
    draw = ImageDraw.Draw(design)

    shoulder_x = BODY_LEFT * GRID + 0.035 * GRID
    shoulder_y = BODY_TOP * GRID + 0.05 * GRID
    arm_len = 0.26 * GRID
    hand_y = shoulder_y - 0.05 * GRID

    # Upper arm: a thick line from the shoulder out to the left.
    draw.line(
        (shoulder_x, shoulder_y, shoulder_x - arm_len, hand_y),
        fill=COAT,
        width=int(ARM_W * GRID * 1.15),
    )
    # The hand -- a small fist with one extended finger pointing LEFT.
    fist_r = 0.035 * GRID
    draw.ellipse(
        (
            shoulder_x - arm_len - fist_r,
            hand_y - fist_r,
            shoulder_x - arm_len + fist_r,
            hand_y + fist_r,
        ),
        fill=SKIN,
    )
    finger_len = 0.07 * GRID
    finger_w = max(3, int(0.018 * GRID))
    draw.line(
        (
            shoulder_x - arm_len - fist_r * 0.4,
            hand_y,
            shoulder_x - arm_len - fist_r - finger_len,
            hand_y,
        ),
        fill=SKIN,
        width=finger_w,
    )
    # A soft cuff where the sleeve meets the hand.
    cuff_x = shoulder_x - arm_len + fist_r * 1.4
    draw.line(
        (cuff_x, hand_y - fist_r, cuff_x, hand_y + fist_r),
        fill=COAT_DARK,
        width=max(3, int(0.02 * GRID)),
    )
    return _finish(design, height)


def draw_mouth_closed(height: int) -> Image.Image:
    """The shut mouth: one lip line, generous margins around it."""
    size = max(4, int(round(height * 0.10)))
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    w = max(3, size // 12)
    draw.line(
        (size * 0.22, size * 0.52, size * 0.78, size * 0.50),
        fill=MOUTH_LIP,
        width=w,
    )
    return image


def draw_mouth_open(height: int) -> Image.Image:
    """The open mouth: a dark rounded cavity with a lip outline."""
    size = max(4, int(round(height * 0.10)))
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (size * 0.16, size * 0.26, size * 0.84, size * 0.76), fill=MOUTH_CAVITY
    )
    draw.ellipse(
        (size * 0.16, size * 0.26, size * 0.84, size * 0.76),
        outline=MOUTH_LIP,
        width=max(2, size // 14),
    )
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="assets/narrator")
    parser.add_argument("--height", type=int, default=900,
                        help="rendered height of the figure in px")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    resting = draw_resting(args.height)
    frames = {
        "narrator_resting.png": resting,
        "narrator_point.png": draw_pointing(args.height),
        "narrator_mouth_closed.png": draw_mouth_closed(args.height),
        "narrator_mouth_open.png": draw_mouth_open(args.height),
    }
    for name, image in frames.items():
        path = out / name
        image.save(path, format="PNG", compress_level=6)
        print(f"{path}  {image.size[0]}x{image.size[1]}")

    # Where the mouth patch centres on the TRIMMED resting art, as fractions
    # of the content box -- exactly what the registry's mouth.anchor means.
    bbox = resting.getbbox()
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        head_x = (HEAD_CX + FIGURE_SHIFT) * args.height
        mouth_y = (HEAD_CY + 0.055) * args.height
        anchor_x = (head_x - x0) / (x1 - x0)
        anchor_y = (mouth_y - y0) / (y1 - y0)
        print(
            "mouth anchor (x, y) over the trimmed artwork: "
            f"({anchor_x:.4f}, {anchor_y:.4f})"
        )


if __name__ == "__main__":
    main()
