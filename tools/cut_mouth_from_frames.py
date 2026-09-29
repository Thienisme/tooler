#!/usr/bin/env python3
"""
Cut a mouth patch (closed + open) out of a character sprite.

The pipeline composites a small mouth patch over the character's face so
the mouth flaps while the body stays perfectly still -- see the `mouth`
block in script.json.  This tool produces the two patch images and the
anchor/size numbers the script needs:

    python3 tools/cut_mouth_from_frames.py \
        --resting assets/characters/my_host_01.png \
        --out-dir assets/characters

How the mouth is found (no AI, plain pixel maths):

1. the face is the largest blob of the sprite's dominant light colour
   (cartoon skin) in the upper half of the artwork;
2. inside the face, dark components (black outlines) are labelled;
3. the mouth is the largest dark component in the lower half of the face,
   horizontally near its centre -- eyes and hair fail one of those tests.

`--frame-b` (an alternate full-body frame, e.g. the existing talk frame)
restricts the search to where that frame actually differs, which pins the
mouth even when several dark blobs compete.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# The face search only looks at the upper half: a standing host's head is
# there, and hands/props lower down share the skin colour.
FACE_SEARCH_LIMIT = 0.55
# A dark component smaller than this is a speck, not a feature.
MIN_MOUTH_COMPONENT_PX = 300
# Default padding around the detected mouth box, in source pixels.
DEFAULT_PAD = 14


def dominant_light_color(image: np.ndarray) -> tuple[int, int, int]:
    """The most common non-black, non-white opaque colour, quantized."""
    opaque = image[image[..., 3] > 128][:, :3]
    quantized = (opaque // 32) * 32
    counts: dict[tuple[int, int, int], int] = {}
    for pixel in map(tuple, quantized):
        if pixel[0] < 96 and pixel[1] < 96 and pixel[2] < 96:
            continue  # outline black
        if pixel[0] > 200 and pixel[1] > 200 and pixel[2] > 200:
            continue  # eye white / paper
        counts[pixel] = counts.get(pixel, 0) + 1
    if not counts:
        raise SystemExit("could not find a dominant light colour (is the sprite empty?)")
    return max(counts, key=counts.get)


def face_box(
    image: np.ndarray, skin: tuple[int, int, int]
) -> tuple[int, int, int, int]:
    """The largest blob of `skin` in the upper half, as (x0, y0, x1, y1)."""
    height = image.shape[0]
    r, g, b = image[..., 0], image[..., 1], image[..., 2]
    dr, dg, db = skin
    mask = (
        (image[..., 3] > 128)
        & (np.abs(r - dr) < 48)
        & (np.abs(g - dg) < 48)
        & (np.abs(b - db) < 48)
    )
    mask[int(height * FACE_SEARCH_LIMIT) :, :] = False

    rows = mask.sum(axis=1)
    strong = np.where(rows > max(20, 0.05 * rows.max()))[0]
    if not len(strong):
        raise SystemExit("could not find a face blob in the upper half")
    groups = np.split(strong, np.where(np.diff(strong) > 12)[0] + 1)
    biggest = max(groups, key=len)
    y0, y1 = int(biggest.min()), int(biggest.max())
    cols = mask[y0 : y1 + 1].sum(axis=0)
    strong_cols = np.where(cols > max(10, 0.05 * cols.max()))[0]
    return int(strong_cols.min()), y0, int(strong_cols.max()), y1


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    """Connected components as (y0, y1, x0, x1, npix, points), scipy or BFS."""
    try:
        from scipy import ndimage

        labels, count = ndimage.label(mask)
        out = []
        for index in range(1, count + 1):
            ys, xs = np.where(labels == index)
            out.append(
                (int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max()),
                 int(len(ys)), (labels, index))
            )
        return out
    except ImportError:
        pass

    from collections import deque

    visited = np.zeros_like(mask, dtype=bool)
    out = []
    height, width = mask.shape
    for start_y, start_x in zip(*np.where(mask)):
        if visited[start_y, start_x]:
            continue
        queue = deque([(int(start_y), int(start_x))])
        visited[start_y, start_x] = True
        ys, xs = [], []
        while queue:
            y, x = queue.popleft()
            ys.append(y)
            xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and mask[ny, nx]
                        and not visited[ny, nx]
                    ):
                        visited[ny, nx] = True
                        queue.append((ny, nx))
        out.append(
            (min(ys), max(ys), min(xs), max(xs), len(ys), (np.array(ys), np.array(xs)))
        )
    return out


def mouth_box(
    image: np.ndarray,
    face: tuple[int, int, int, int],
    restrict: np.ndarray | None = None,
    pad: int = DEFAULT_PAD,
) -> tuple[int, int, int, int]:
    """
    The mouth as (x0, y0, x1, y1), padded.

    `restrict` is an optional boolean mask (same shape) of where a second
    frame differs from the resting one -- the search prefers components
    inside it, which is how an alternate talk frame disambiguates a beard
    from the mouth.
    """
    fx0, fy0, fx1, fy1 = face
    face_w, face_h = fx1 - fx0, fy1 - fy0
    dark = (image[..., 3] > 128) & (image[..., :3].max(axis=2) < 90)

    # Shrink the search window so the face's own outline cannot merge
    # everything into one component.
    margin_x, margin_y = int(face_w * 0.15), int(face_h * 0.10)
    inner = np.zeros_like(dark)
    iy0, iy1 = fy0 + margin_y, fy1 - margin_y
    ix0, ix1 = fx0 + margin_x, fx1 - margin_x
    inner[iy0:iy1, ix0:ix1] = dark[iy0:iy1, ix0:ix1]

    candidates = []
    for y0, y1, x0, x1, count, payload in _components(inner):
        if count < MIN_MOUTH_COMPONENT_PX:
            continue
        cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
        # The mouth sits in the lower part of the face, near its centre.
        if cy < fy0 + face_h * 0.35:
            continue
        if not (fx0 + face_w * 0.2 <= cx <= fx1 - face_w * 0.2):
            continue
        # A component clipped by the inner window is the outline itself.
        if y0 <= iy0 or y1 >= iy1 - 1 or x0 <= ix0 or x1 >= ix1 - 1:
            continue
        if restrict is not None:
            if restrict[y0 : y1 + 1, x0 : x1 + 1].sum() == 0:
                continue
        candidates.append((count, (x0 - pad, y0 - pad, x1 + pad, y1 + pad)))

    if not candidates:
        raise SystemExit(
            "could not find a mouth: no dark component in the lower half of "
            "the face. Pass --frame-b or cut the patch by hand."
        )
    candidates.sort(reverse=True)
    return candidates[0][1]


def resting_mouth_is_open(image: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """
    Whether the resting artwork already draws the mouth open.

    The central area of the box is mostly dark pixels on an open mouth and
    mostly skin on a closed one.  Which of the two patches has to be
    synthesized depends on this: the artwork always provides one state, the
    tool draws the other.
    """
    x0, y0, x1, y1 = box
    region = image[y0:y1, x0:x1]
    if not region.size:
        return False
    height, width = region.shape[:2]
    cy0, cy1 = int(height * 0.3), int(height * 0.7)
    cx0, cx1 = int(width * 0.3), int(width * 0.7)
    centre = region[cy0:cy1, cx0:cx1]
    opaque = centre[..., 3] > 128
    if not opaque.any():
        return False
    dark = opaque & (centre[..., :3].max(axis=2) < 110)
    return float(dark.sum()) / max(float(opaque.sum()), 1.0) > 0.25


def _skin_color_around(
    image: np.ndarray, box: tuple[int, int, int, int], skin: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Median colour of skin-classified pixels in a ring around the box."""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    ring = 48
    rx0, ry0 = max(0, x0 - ring), max(0, y0 - ring)
    rx1, ry1 = min(width, x1 + ring), min(height, y1 + ring)
    region = image[ry0:ry1, rx0:rx1]
    dr, dg, db = skin
    near = (
        (region[..., 3] > 128)
        & (np.abs(region[..., 0] - dr) < 48)
        & (np.abs(region[..., 1] - dg) < 48)
        & (np.abs(region[..., 2] - db) < 48)
    )
    if near.sum() < 50:
        return (int(dr), int(dg), int(db))
    median = np.median(region[near][:, :3], axis=0)
    return tuple(int(round(value)) for value in median)


def build_patches(
    resting: Image.Image,
    box: tuple[int, int, int, int],
    skin: tuple[int, int, int],
    *,
    mouth_fill: tuple[int, int, int] = (40, 16, 14),
    lip_color: tuple[int, int, int] = (110, 50, 42),
    line_color: tuple[int, int, int] = (35, 24, 22),
) -> tuple[Image.Image, Image.Image, str, str]:
    """
    Both mouth states as opaque RGB patches, plus where each came from.

    One state is always the resting artwork itself; the other is drawn in
    the same palette: an open mouth is a dark ellipse ringed with a lip
    colour, a closed mouth is skin with a smile line across it.
    """
    original = resting.crop(box).convert("RGB")
    width, height = original.size

    open_patch = original.copy()
    draw = ImageDraw.Draw(open_patch)
    ew, eh = width * 0.72, height * 0.70
    ellipse = (
        width / 2 - ew / 2,
        height * 0.52 - eh / 2,
        width / 2 + ew / 2,
        height * 0.52 + eh / 2,
    )
    draw.ellipse(ellipse, fill=mouth_fill)
    draw.ellipse(
        (ellipse[0] - 4, ellipse[1] - 3, ellipse[2] + 4, ellipse[3] + 3),
        outline=lip_color,
        width=4,
    )

    closed_patch = Image.new("RGB", (width, height), skin)
    line = ImageDraw.Draw(closed_patch)
    sw, sh = width * 0.62, height * 0.42
    arc_box = (
        width / 2 - sw / 2,
        height * 0.42 - sh / 2,
        width / 2 + sw / 2,
        height * 0.42 + sh / 2,
    )
    line.arc(arc_box, start=15, end=165, fill=line_color, width=max(4, height // 14))

    if resting_mouth_is_open(np.asarray(resting.convert("RGBA"), int), box):
        return closed_patch, original, "synthesized", "original"
    return original, open_patch, "original", "synthesized"


def write_check_sheet(
    resting: Image.Image,
    box: tuple[int, int, int, int],
    open_patch: Image.Image,
    path: Path,
) -> None:
    """A side-by-side PNG so a human can confirm the box landed on the mouth."""
    width, height = resting.size
    sheet = Image.new("RGB", (width, height * 2), (24, 24, 28))
    marked = resting.copy()
    ImageDraw.Draw(marked).rectangle(box, outline=(255, 40, 40), width=6)
    sheet.paste(marked.convert("RGB"), (0, 0))

    composited = resting.copy()
    composited.alpha_composite(open_patch.convert("RGBA"), (box[0], box[1]))
    sheet.paste(composited.convert("RGB"), (0, height))

    zx0, zy0 = max(0, box[0] - 60), max(0, box[1] - 60)
    zx1, zy1 = min(width, box[2] + 60), min(height, box[3] + 60)
    zoom = (zx1 - zx0) * 3, (zy1 - zy0) * 3
    sheet.paste(
        composited.crop((zx0, zy0, zx1, zy1)).resize(zoom, Image.NEAREST),
        (20, height * 2 - zoom[1] - 12),
    )
    sheet.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cut a mouth patch (closed/open) from a character sprite and "
            "print the anchor/size for the script's `mouth` block."
        )
    )
    parser.add_argument(
        "--resting", required=True, type=Path,
        help="the character's resting full-body PNG (transparent background)",
    )
    parser.add_argument(
        "--frame-b", type=Path, default=None,
        help="optional alternate frame (e.g. an existing talk frame); its "
        "difference from the resting image pins the mouth search",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("assets/characters"),
        help="where mouth_closed.png / mouth_open.png are written",
    )
    parser.add_argument("--pad", type=int, default=DEFAULT_PAD)
    parser.add_argument(
        "--check", type=Path, default=None,
        help="write a contact-sheet PNG here to eyeball the result",
    )
    args = parser.parse_args()

    resting = Image.open(args.resting).convert("RGBA")
    width, height = resting.size
    array = np.asarray(resting, int)

    restrict = None
    if args.frame_b is not None:
        other = Image.open(args.frame_b).convert("RGBA")
        if other.size != resting.size:
            other = other.resize(resting.size, Image.LANCZOS)
        restrict = (
            np.abs(np.asarray(other, int) - array).max(axis=2) > 40
        )

    skin = dominant_light_color(array)
    face = face_box(array, skin)
    box = mouth_box(array, face, restrict=restrict, pad=args.pad)
    skin_fill = _skin_color_around(array, box, skin)

    closed, opened, closed_src, open_src = build_patches(resting, box, skin_fill)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    closed_path = args.out_dir / "mouth_closed.png"
    open_path = args.out_dir / "mouth_open.png"
    closed.save(closed_path)
    opened.save(open_path)
    if args.check:
        write_check_sheet(resting, box, opened, args.check)

    anchor = ((box[0] + box[2]) / 2 / width, (box[1] + box[3]) / 2 / height)
    size = ((box[2] - box[0]) / width, (box[3] - box[1]) / height)
    print(
        json.dumps(
            {
                "closed": str(closed_path),
                "open": str(open_path),
                "closed_source": closed_src,
                "open_source": open_src,
                "mouth_box": list(box),
                "anchor_frac": [round(anchor[0], 4), round(anchor[1], 4)],
                "size_frac": [round(size[0], 4), round(size[1], 4)],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
