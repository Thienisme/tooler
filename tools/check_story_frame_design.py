#!/usr/bin/env python3
"""
Verify a hand-drawn story-frame PNG fits the storytelling layout.

A designed frame is transparent where the scene artwork must show through
(the window), and opaque where the frame's own art lives.  This tool
measures the transparent pixels inside the panel box and reports:

  * the transparent window's bounding box, in panel fractions (t/b/l/r);
  * whether the narrator's strip (x >= 0.70 of the FULL frame) stays clean;
  * whether the window is a single solid block (no stray holes/dots).

Usage:
  python3 tools/check_story_frame_design.py assets/frames/my_frame.png
  python3 tools/check_story_frame_design.py my_frame.png --full 1920x1080
  python3 tools/check_story_frame_design.py my_frame.png --json

Then put the numbers it prints into script.json:

  "story_frame": {
      "use": "ke_cuoi",
      "style": "custom",
      "image_file_png": "projects/my_design/my_frame.png",
      "art_inset": {"t": 0.10, "b": 0.12, "l": 0.07, "r": 0.30}
  }

`art_inset` must match the measured window -- the checker prints the exact
block to paste.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

# The narrator's reserved strip on the right of the FULL video frame, as
# declared by the layout defaults (frame ends at x=0.70 of the width).
NARRATOR_STRIP_X = 0.70


def measure(path: Path) -> dict:
    """Measure the transparent window of a story-frame PNG."""
    image = Image.open(path)
    if image.mode != "RGBA":
        raise SystemExit(f"{path}: expected RGBA PNG, got mode {image.mode}")
    alpha = np.asarray(image)[:, :, 3]
    w, h = image.size

    transparent = alpha < 16
    coverage = transparent.mean()
    if coverage == 0.0:
        raise SystemExit(
            f"{path}: no transparent pixels at all -- the window must be "
            "transparent (alpha < 16) so the scene artwork can show through"
        )

    # The window is the LARGEST connected transparent component.  Rounded
    # corners and decorative see-through gaps form small extra components
    # that must not stretch the window's bounding box.
    labels, count = ndimage.label(transparent)
    if count == 0:
        raise SystemExit(f"{path}: no usable transparent window found")
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0  # background is opaque pixels, not a component
    window_label = int(sizes.argmax())
    window = labels == window_label

    ys, xs = np.nonzero(window)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1

    # A usable window is one solid block: the largest component must fill
    # its own bounding box, and nothing transparent may leak into it.
    inside = transparent[y0:y1, x0:x1]
    solid = float(window[y0:y1, x0:x1].mean())
    outliers = int(transparent.sum() - inside.sum())

    left = x0 / w
    top = y0 / h
    right = (w - x1) / w
    bottom = (h - y1) / h

    return {
        "png": str(path),
        "size": [w, h],
        "window_px": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        "inset_fractions": {
            "t": round(top, 4),
            "b": round(bottom, 4),
            "l": round(left, 4),
            "r": round(right, 4),
        },
        "window_solidity": round(solid, 4),
        "transparent_outside_window": int(outliers),
        "transparent_coverage": round(float(coverage), 4),
    }


def punch_window(
    source: Path, destination: Path, rect: tuple[int, int, int, int],
    from_full: bool,
) -> None:
    """Crop the panel box (optional), then punch the window transparent.

    AI generators rarely produce a pixel-exact transparent hole, so the
    workflow is: generate the design with a SOLID screen, then let this
    tool cut the window out at exact coordinates.  `from_full` crops a
    1920x1080 full-frame design down to the panel box first.
    """
    image = Image.open(source)
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    if from_full:
        panel = (77, 65, 1344, 1015)  # default story_frame box at 1080p
        if image.size != (1920, 1080):
            raise SystemExit(
                f"--from-full expects a 1920x1080 design, got {image.size}"
            )
        image = image.crop(panel)
        # Coordinates were read on the 1920x1080 design canvas: translate
        # them into panel space so what the designer measured is what gets
        # cut.
        rect = (rect[0] - panel[0], rect[1] - panel[1],
                rect[2] - panel[0], rect[3] - panel[1])
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
        raise SystemExit(
            f"--punch {rect} is outside the {image.size} canvas"
        )
    image.paste((0, 0, 0, 0), (x0, y0, x1, y1))  # hard transparent hole
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")
    print(f"punched   : {destination} window=({x0},{y0})-({x1},{y1})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png", type=Path, help="the designed frame PNG")
    parser.add_argument(
        "--full", default="1920x1080",
        help="the video resolution the panel is placed on (default 1920x1080)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument(
        "--punch", metavar="X0,Y0,X1,Y1", default=None,
        help="cut a transparent window at these coordinates, read on the "
        "design's own canvas (1920x1080 with --from-full, panel-size "
        "otherwise) -- for designs whose screen is still solid",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="where to write the punched, ready-to-use frame PNG "
        "(required with --punch)",
    )
    parser.add_argument(
        "--from-full", action="store_true",
        help="the design is a full 1920x1080 frame; crop the panel box "
        "(77,65)-(1344,1069) out of it first",
    )
    args = parser.parse_args()

    if args.punch:
        if not args.out:
            parser.error("--punch needs --out for the ready-to-use PNG")
        rect = tuple(int(v) for v in args.punch.split(","))
        punch_window(args.png, args.out, rect, args.from_full)
        target = args.out
    else:
        target = args.png

    result = measure(target)
    fw = int(args.full.lower().split("x")[0])
    px_w = result["size"][0]
    px_x = 0  # fractions-based check; assumes the default anchor x=0.04

    problems: list[str] = []
    inset = result["inset_fractions"]
    if min(inset.values()) <= 0.0:
        problems.append(
            "a window touching the panel edge leaves no mat -- keep at "
            "least ~4% mat on every side so the picture reads as framed"
        )
    if max(inset["l"], inset["r"]) > 0.45 or max(inset["t"], inset["b"]) > 0.45:
        problems.append(
            "one side eats more than 45% of the panel -- the picture gets "
            "too small to read"
        )
    if result["window_solidity"] < 0.985:
        problems.append(
            "the window has holes or stray transparent pixels "
            f"(solidity {result['window_solidity']:.3f}) -- make the window "
            "one clean block; small see-through gaps in the art also count "
            "(the checker uses the largest connected transparent region)"
        )

    # Narrator strip check: panel anchored at its default x=0.04; the
    # panel's right edge lands at 0.04 + panel_width_fraction.  With the
    # default 0.66 width the edge is at 70% of the video width.
    strip_start_px = int(fw * NARRATOR_STRIP_X)
    panel_right_default = int(fw * 0.70)
    full_frame_canvas = px_w >= int(fw * 0.95)
    if not full_frame_canvas and px_w + px_x > panel_right_default:
        problems.append(
            f"panel width {px_w}px exceeds the default panel box "
            f"({panel_right_default}px at x=0.04) -- shrink the design to "
            f"{panel_right_default}px wide or move/resize story_frame.x/width"
        )
    # The measured window's right edge, in full-frame pixels, must not
    # reach the narrator's strip either.
    window_right_full = px_x + result["window_px"]["x"] + result["window_px"]["w"]
    if window_right_full > strip_start_px:
        problems.append(
            f"the transparent window reaches x={window_right_full}px, "
            f"into the narrator's strip (starts {strip_start_px}px) -- "
            "keep the window clear of the right 30% of the video"
        )

    if args.json:
        print(json.dumps({**result, "problems": problems}, indent=2))
        return

    print(f"PNG          : {result['png']} ({result['size'][0]}x{result['size'][1]})")
    print(f"window (px)  : x={result['window_px']['x']} y={result['window_px']['y']} "
          f"w={result['window_px']['w']} h={result['window_px']['h']}")
    print(f"inset (frac) : t={inset['t']} b={inset['b']} l={inset['l']} r={inset['r']}")
    print(f"solidity     : {result['window_solidity']:.4f} "
          f"(stray transparent px outside the window: "
          f"{result['transparent_outside_window']})")
    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("\n[ok] design fits the storytelling layout.  Paste this block:")
    print(json.dumps({
        "style": "custom",
        "art_inset": inset,
    }, indent=2))


if __name__ == "__main__":
    main()
