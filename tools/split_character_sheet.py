#!/usr/bin/env python3
"""Split a 3:2 character sheet into six fixed, evenly spaced cells."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SIZE = (1536, 1024)
CELL_SIZE = 480
CELL_ORIGINS = (
    (16, 16),
    (528, 16),
    (1040, 16),
    (16, 528),
    (528, 528),
    (1040, 528),
)
DEFAULT_NAMES = tuple(f"character_{index:02d}" for index in range(1, 7))


def split_sheet(
    source: Path, output_dir: Path, names: tuple[str, ...] = DEFAULT_NAMES
) -> list[Path]:
    """Crop the six fixed cells, scaling reference coordinates to the input."""
    if len(names) != len(CELL_ORIGINS):
        raise ValueError("exactly six output names are required")
    if len(set(names)) != len(names) or any(
        not name
        or Path(name).name != name
        or Path(name).suffix
        or name in {".", ".."}
        for name in names
    ):
        raise ValueError("names must be unique filename stems without extensions")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with Image.open(source) as opened:
        width, height = opened.size
        ratio = width / height
        if abs(ratio - 1.5) > 0.01:
            raise ValueError(
                f"sheet must have a 3:2 aspect ratio; got {width}x{height}"
            )

        image = opened.convert("RGBA")
        scale_x = width / REFERENCE_SIZE[0]
        scale_y = height / REFERENCE_SIZE[1]
        for name, (left, top) in zip(names, CELL_ORIGINS):
            box = (
                round(left * scale_x),
                round(top * scale_y),
                round((left + CELL_SIZE) * scale_x),
                round((top + CELL_SIZE) * scale_y),
            )
            destination = output_dir / f"{name}.png"
            image.crop(box).save(destination, format="PNG", compress_level=6)
            written.append(destination)
    return written


def resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Split a 3:2 sheet with six characters arranged in a fixed 3x2 grid."
        )
    )
    parser.add_argument("sheet", type=Path, help="input sheet image")
    parser.add_argument(
        "--out",
        type=Path,
        help="output folder (default: <sheet-name>_cutouts beside the sheet)",
    )
    parser.add_argument(
        "--names",
        nargs=6,
        metavar=("TOP_LEFT", "TOP_MIDDLE", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_MIDDLE", "BOTTOM_RIGHT"),
        default=DEFAULT_NAMES,
        help="six output filename stems in reading order",
    )
    args = parser.parse_args()

    source = resolve_project_path(args.sheet)
    if not source.is_file():
        parser.error(f"sheet not found: {source}")
    output_dir = (
        resolve_project_path(args.out)
        if args.out
        else source.with_name(f"{source.stem}_cutouts")
    )

    try:
        written = split_sheet(source, output_dir, tuple(args.names))
    except (OSError, ValueError) as error:
        parser.error(str(error))

    for path in written:
        print(f"OK {path}")
    print(f"\n{len(written)}/{len(CELL_ORIGINS)} cells cropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())