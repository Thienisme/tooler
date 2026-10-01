#!/usr/bin/env python3
"""
Cut the open/closed mouth patches out of one resting character frame.

The mouth cavity sits below the red nose.  This tool locates it (or takes
an explicit `--box`), lifts the crop as the *open* patch, fills the cavity
with the face's own skin tone for the *closed* patch, draws a thin smile
arc, and reports the anchor/size fractions the registry needs.  With
`--registry NAME` it also writes the fractions straight into
`assets/characters/characters.json`.

Usage:
  python3 tools/cut_mouth_patch.py \
      --frame assets/characters/Part/ke_02_laugh.png \
      --prefix assets/characters/Part/ke02 \
      --registry ke_cuoi --check tmp/ke02_mouth_pair.png
  (add --box x0,y0,x1,y1 when auto-locate misses the mouth)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

REGISTRY = Path("assets/characters/characters.json")
MOUTH_PERIOD_S = 0.26


def _content_box(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.getbbox()
    return box if box is not None else (0, 0, image.width, image.height)


def _find_nose(arr: np.ndarray) -> tuple[int, int] | None:
    """The red ball nose: the TOPMOST large saturated-red cluster.

    Not the largest -- on a laughing face the open mouth is a bigger red
    blob than the nose, and picking it would point the mouth search at
    the beard.  The nose always sits above the mouth, so topmost wins.
    """
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    red = (a > 200) & (r > 200) & (g > 70) & (g < 170) & (b > 60) & (b < 160)
    if red.sum() < 40:
        return None
    labelled, _ = ndimage.label(red)
    sizes = np.bincount(labelled.ravel())
    best_id, best_y = 0, None
    for label_id in range(1, len(sizes)):
        if sizes[label_id] < 200:
            continue
        ys, xs = np.where(labelled == label_id)
        mean_y = ys.mean()
        if best_y is None or mean_y < best_y:
            best_id, best_y = label_id, mean_y
    if best_id == 0:
        return None
    ys, xs = np.where(labelled == best_id)
    return int(xs.mean()), int(ys.mean())


def _skin_colour(arr: np.ndarray, nose: tuple[int, int]) -> tuple[int, int, int]:
    """Median face colour sampled in a ring above the nose (forehead/cheeks)."""
    nx, ny = nose
    y0, y1 = max(ny - 90, 0), max(ny - 12, 1)
    x0, x1 = max(nx - 90, 0), min(nx + 90, arr.shape[1])
    region = arr[y0:y1, x0:x1]
    flat = region.reshape(-1, 4)
    opaque = flat[flat[:, 3] > 200]
    return tuple(int(v) for v in np.median(opaque, axis=0)[:3])


def _cavity_core(region: np.ndarray) -> np.ndarray:
    """Mouth-interior colours only: saturated dark red plus tooth white.

    Deliberately narrow.  The mustache/beard strokes are dark *brown*
    (green channel sits near half the red), the robe is blue, the skin is
    pink-bright -- none of those may match, or the patch box runs away
    down the body and the closed variant paints the outfit skin-coloured.
    """
    r, g, b = region[..., 0], region[..., 1], region[..., 2]
    strict_red = (
        (r > 100) & (r < 215) & (g < r * 0.55) & (b < 115) & (b < r * 0.7)
    )
    teeth = (r > 215) & (g > 215) & (b > 205)
    # The tongue: bright pink-red, clearly redder than green.  It must be
    # part of the core or the closed variant keeps a tongue sticking out
    # of a closed mouth.
    tongue = (
        (r > 195) & (g > 85) & (g < 180) & (b > 75) & (b < 170)
        & (r > g + 55)
    )
    return strict_red | teeth | tongue


def _locate_cavity(
    arr: np.ndarray, nose: tuple[int, int]
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Mouth cavity below the nose: core colours, box, dilated mask.

    No flood fill on purpose.  A flood starts in the mouth and swims out
    through every dark stroke that touches the outline -- the beard, the
    robe seams -- and one run of this tool on the laughing frame painted
    the whole robe skin-coloured before the flood was retired.
    """
    nx, ny = nose
    height, width = arr.shape[:2]
    y0 = min(ny + 8, height - 2)
    y1 = min(ny + 230, height)
    x0, x1 = max(nx - 190, 0), min(nx + 190, width)
    core = _cavity_core(arr[y0:y1, x0:x1])
    if core.sum() < 12:
        raise SystemExit("no mouth interior found below the nose; pass --box")

    # The core also catches stray dark-brown outline pixels (hands, beard)
    # far from the mouth, so cluster it and keep the largest blob that sits
    # below the nose -- the mouth interior is by far the biggest one.
    bridged = ndimage.binary_dilation(core, iterations=2)
    labelled, _ = ndimage.label(bridged)
    sizes = np.bincount(labelled.ravel())
    sizes[0] = 0
    cluster = None
    for label_id in np.argsort(sizes)[::-1][:6]:
        if sizes[label_id] < 60:
            break
        cys, cxs = np.where(labelled == label_id)
        if y0 + cys.mean() > ny:
            cluster = labelled == label_id
            break
    if cluster is None:
        raise SystemExit("no mouth-sized interior cluster below the nose")

    tight = cluster & core
    cys, cxs = np.where(tight)
    box = (
        max(x0 + int(cxs.min()) - 4, 0),
        max(y0 + int(cys.min()) - 4, 0),
        min(x0 + int(cxs.max()) + 5, width),
        min(y0 + int(cys.max()) + 5, height),
    )
    # Map the core pixels into the box (the box may be clamped at the
    # image edges, so plain slicing of the zone is not safe here either).
    core_local = np.zeros((box[3] - box[1], box[2] - box[0]), dtype=bool)
    core_local[(cys + y0) - box[1], (cxs + x0) - box[0]] = True
    # Two dilation pixels pull the AA edge of the interior in, while the
    # dark outline ring itself stays untouched for the closed variant.
    mask = ndimage.binary_dilation(core_local, iterations=2)
    return mask, box


def _closed_variant(open_patch: Image.Image, core_mask: np.ndarray) -> Image.Image:
    """The closed-mouth patch: cavity filled with the face's skin tone.

    The fill is every pixel *connected to the core* inside the patch --
    the cavity's dark maroon shading matches too, so the closed mouth has
    no leftover blotches.  The near-black outline is a barrier and stays:
    it reads as the closed lip line.  A thin arc across the fill is the
    smile.
    """
    arr = np.array(open_patch).astype(int)
    # The fill colour is sampled from the skin AROUND the cavity -- a ring
    # just outside the core.  Sampling higher up (forehead) can catch hat,
    # hair and eyeballs, and the median of that mess is a muddy grey.
    ring = ndimage.binary_dilation(core_mask, iterations=12) & ~core_mask
    ring_pixels = arr[ring & (arr[..., 3] > 200)]
    loose_skin = (
        (ring_pixels[:, 0] > 195) & (ring_pixels[:, 1] > 135)
        & (ring_pixels[:, 2] > 95) & (ring_pixels[:, 0] > ring_pixels[:, 1])
        & (ring_pixels[:, 1] > ring_pixels[:, 2])
    )
    if loose_skin.sum() >= 50:
        skin = tuple(int(v) for v in np.median(ring_pixels[loose_skin], axis=0))
    else:
        skin = (252, 192, 147)
    dist = np.sqrt(
        (arr[..., 0] - skin[0]) ** 2
        + (arr[..., 1] - skin[1]) ** 2
        + (arr[..., 2] - skin[2]) ** 2
    )
    near_black = (
        (arr[..., 0] < 70) & (arr[..., 1] < 65) & (arr[..., 2] < 65)
    )
    # Fill everything mouth-interior-ish except three protected zones:
    # a 3px rim along the patch border (the outer lip line and whatever
    # face art the box overlaps survive untouched), the red nose when it
    # dips into the box, and the skin itself.  Tooth outlines and cavity
    # shading are interior -- they fill, or they haunt the closed mouth
    # as ghost rectangles.
    rim = np.zeros(arr.shape[:2], dtype=bool)
    rim[:3, :] = rim[-3:, :] = True
    rim[:, :3] = rim[:, -3:] = True
    # The nose may dip into the box top; it is red like the tongue, so it
    # is only protected in the upper part of the patch -- the tongue sits
    # low and must fill.
    nose_red = (
        (arr[..., 0] > 215) & (arr[..., 0] > arr[..., 1] + 60)
        & (np.arange(arr.shape[0])[:, None] < arr.shape[0] * 0.45)
    )
    teeth = (arr[..., 0] > 215) & (arr[..., 1] > 215) & (arr[..., 2] > 205)
    tongue = (
        (arr[..., 0] > 195) & (arr[..., 1] > 85) & (arr[..., 1] < 180)
        & (arr[..., 2] > 75) & (arr[..., 2] < 170)
        & (arr[..., 0] > arr[..., 1] + 55)
    )
    target = ((dist > 55) | near_black | teeth) & (~rim) & (~nose_red)
    target &= arr[..., 3] > 150
    labelled, _ = ndimage.label(target)
    keep = np.unique(labelled[core_mask])
    keep = keep[keep > 0]
    fill = np.isin(labelled, keep)
    # A tongue sliver can sit in its own component (cut off from the core
    # by its outline); it still has to go -- a closed mouth with a tongue
    # hanging out is a different joke entirely.
    fill |= tongue & (~rim) & (~nose_red) & (arr[..., 3] > 150)

    closed_arr = np.array(open_patch)
    closed_arr[fill, 0] = skin[0]
    closed_arr[fill, 1] = skin[1]
    closed_arr[fill, 2] = skin[2]
    closed_arr[fill, 3] = 255
    closed = Image.fromarray(closed_arr)

    ys, xs = np.where(fill)
    x0, x1 = int(xs.min()), int(xs.max())
    ytop, ybot = int(ys.min()), int(ys.max())
    draw = ImageDraw.Draw(closed)
    steps = 40
    y_corner = ytop + int((ybot - ytop) * 0.28)
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        y = y_corner + (ybot - y_corner - 2) * (4 * t * (1 - t))
        pts.append((x, y))
    draw.line(pts, fill=(70, 45, 40, 255), width=3, joint="curve")
    return closed


def _pair_sheet(open_patch: Image.Image, closed: Image.Image, path: Path) -> None:
    sheet = Image.new("RGBA", (open_patch.width * 2 + 8, open_patch.height),
                      (40, 44, 54, 255))
    sheet.paste(closed, (0, 0), closed)
    sheet.paste(open_patch, (open_patch.width + 8, 0), open_patch)
    scale = max(1, 320 // max(open_patch.width, 1))
    sheet = sheet.resize((sheet.width * scale, sheet.height * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", required=True, help="resting character frame")
    parser.add_argument("--prefix", required=True,
                        help="output paths become <prefix>_mouth_open/_closed.png")
    parser.add_argument("--box", default=None,
                        help="mouth box x0,y0,x1,y1 (skips auto-locate)")
    parser.add_argument("--registry", default=None,
                        help="template name to write mouth spec into")
    parser.add_argument("--check", default=None,
                        help="optional path for a close-up pair sheet")
    args = parser.parse_args()

    source = Image.open(args.frame).convert("RGBA")
    arr = np.array(source).astype(int)
    content = _content_box(source)
    content_w, content_h = content[2] - content[0], content[3] - content[1]

    nose = _find_nose(arr)
    if nose is None and args.box is None:
        raise SystemExit("no red nose found; pass --box x0,y0,x1,y1")

    skin = _skin_colour(arr, nose) if nose else (252, 192, 147)

    if args.box:
        bx0, by0, bx1, by1 = (int(v) for v in args.box.split(","))
        crop = source.crop((bx0, by0, bx1, by1))
        core = _cavity_core(np.array(crop).astype(int))
        if core.sum() < 12:
            raise SystemExit("no mouth interior colours inside --box")
        mask = ndimage.binary_dilation(core, iterations=2)
        mask_size = int(mask.sum())
        box = (bx0, by0, bx1, by1)
    else:
        assert nose is not None
        mask, box = _locate_cavity(arr, nose)
        mask_size = int(mask.sum())

    open_patch = source.crop(box)
    closed = _closed_variant(open_patch, mask)

    open_path = Path(f"{args.prefix}_mouth_open.png")
    closed_path = Path(f"{args.prefix}_mouth_closed.png")
    open_patch.save(open_path)
    closed.save(closed_path)

    anchor_x = ((box[0] + box[2]) / 2 - content[0]) / content_w
    anchor_y = ((box[1] + box[3]) / 2 - content[1]) / content_h
    size_w = (box[2] - box[0]) / content_w
    size_h = (box[3] - box[1]) / content_h

    if args.check:
        _pair_sheet(open_patch, closed, Path(args.check))

    spec = {
        "images": [str(closed_path), str(open_path)],
        "x": round(anchor_x, 4),
        "y": round(anchor_y, 4),
        "size": [round(size_w, 4), round(size_h, 4)],
        "period_s": MOUTH_PERIOD_S,
    }
    if args.registry:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        entry = registry[args.registry]
        entry["mouth"] = spec
        REGISTRY.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"registry '{args.registry}' mouth updated")

    print(f"open   : {open_path} ({open_patch.size[0]}x{open_patch.size[1]})")
    print(f"closed : {closed_path}")
    print(f"box    : {box}  cavity px: {mask_size}")
    print("mouth spec:")
    print(json.dumps(spec, indent=2))


if __name__ == "__main__":
    main()
