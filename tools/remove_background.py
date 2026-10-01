#!/usr/bin/env python3
"""
Cut characters out of white-background artwork, the way the pipeline needs.

The pipeline composites characters as transparent sprites, so artwork with a
flattened background has to be cut out before it can be used.  This tool
runs rembg over a file or a folder and writes RGBA PNGs next to the pipeline
expectations:

    python3 tools/remove_background.py assets/characters/my_host_05.png
    python3 tools/remove_background.py assets/characters --glob "my_host_*.png" \
        --suffix _cut

Outputs are written next to the input by default (same name + `--suffix`),
so `my_host_05.png` becomes `my_host_05_cut.png`.  `--in-place` overwrites
the source file instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PIL import Image  # noqa: E402

from autovid.infrastructure.video.sprites import sprite_transparency  # noqa: E402


def collect_images(root: Path, pattern: str) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.glob(pattern) if p.is_file())


def remove_background(
    path: Path, destination: Path, *, alpha_matting: bool = False
) -> tuple[bool, float]:
    """Cut one image out; returns (has_alpha, empty share) of the result."""
    from rembg import remove, new_session

    session = new_session("isnet-general-use")
    raw = Image.open(path)
    # Alpha matting refines hair-thin edges but is minutes-per-image slow on
    # large art; cartoon cutouts on a clean background do not need it, so it
    # stays behind a flag.
    kwargs: dict = {"session": session}
    if alpha_matting:
        kwargs.update(
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=8,
        )
    cut = remove(raw, **kwargs)
    cut = cut.convert("RGBA")
    destination.parent.mkdir(parents=True, exist_ok=True)
    cut.save(destination, format="PNG", compress_level=6)
    return sprite_transparency(destination)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove the background from character artwork with rembg, "
            "writing RGBA cutouts the sprite pipeline can composite."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="image file(s) or folder(s) to process",
    )
    parser.add_argument(
        "--glob",
        default="*.png",
        help="pattern inside a folder input (default: *.png)",
    )
    parser.add_argument(
        "--suffix",
        default="",
        help="suffix for the output filename (default: overwrite-safe sibling)",
    )
    with_suffix_help = (
        "write next to the input with --suffix instead of overwriting"
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=with_suffix_help,
    )
    parser.add_argument(
        "--alpha-matting",
        action="store_true",
        help="refine edges (much slower; needed only for hair/fur detail)",
    )
    args = parser.parse_args()

    try:
        import rembg  # noqa: F401
    except ImportError:
        print(
            "rembg is not installed. Run: pip install rembg",
            file=sys.stderr,
        )
        return 2

    paths: list[Path] = []
    for item in args.inputs:
        full = item if item.is_absolute() else PROJECT_ROOT / item
        if not full.exists():
            print(f"SKIP {item}: not found", file=sys.stderr)
            continue
        paths.extend(collect_images(full, args.glob))

    if not paths:
        print("nothing to process", file=sys.stderr)
        return 1

    ok = 0
    for path in paths:
        destination = (
            path
            if args.in_place
            else path.with_name(f"{path.stem}{args.suffix}{path.suffix}")
        )
        try:
            has_alpha, share = remove_background(
                path, destination, alpha_matting=args.alpha_matting
            )
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {path.name}: {error}", file=sys.stderr)
            continue
        verb = "OK" if has_alpha and share > 0.05 else "WARN"
        print(f"{verb} {path.name} -> {destination.name} (empty {share:.0%})")
        ok += 1

    print(f"\n{ok}/{len(paths)} processed")
    return 0 if ok == len(paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
