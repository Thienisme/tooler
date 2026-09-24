"""
Font resolution and text measuring for overlays.

Two jobs:

* Resolve the font a script asks for, falling back to a system font when
  it is missing (the spec's required behaviour), so a typo in a script
  never stops a render.
* Measure how wide a string will actually be at a given size.  That is the
  only honest way to answer "will this text fit on screen?", and it lets
  the report suggest a font size that *does* fit instead of just failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from autovid.paths import resolve_asset

# Checked in order, then a directory scan as a last resort.
FALLBACK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

FALLBACK_FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
    "C:/Windows/Fonts",
)

# How much of the frame an overlay may occupy.  Shared by the images stage
# (which reports "size N would fit") and the assembly stage (which now
# actually renders at that size), so the promise and the picture agree.
OVERLAY_WIDTH_FRACTION = 0.9
OVERLAY_HEIGHT_FRACTION = 0.9

# Height budget by position: a banner at the top or bottom should not run
# through the middle of the artwork.
POSITION_HEIGHT_FRACTION: dict[str, float] = {
    "center": OVERLAY_HEIGHT_FRACTION,
    "top": 0.4,
    "bottom": 0.4,
    "top_left": 0.4,
    "top_right": 0.4,
    "bottom_left": 0.4,
    "bottom_right": 0.4,
}

# Corner placements share the width with the other half of the frame.
CORNER_POSITIONS = frozenset(
    {"top_left", "top_right", "bottom_left", "bottom_right"}
)


@dataclass(frozen=True)
class FontResolution:
    """Which font will actually be used, and why."""

    requested: str
    path: Path | None
    used_fallback: bool

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "used": str(self.path) if self.path else None,
            "fallback": self.used_fallback,
        }


def find_system_font() -> Path | None:
    """First usable font on this machine, or None."""
    for candidate in FALLBACK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path

    home_fonts = Path.home() / ".fonts"
    for directory in (*FALLBACK_FONT_DIRS, str(home_fonts)):
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in ("**/*.ttf", "**/*.otf"):
            for found in sorted(base.glob(pattern)):
                return found
    return None


def resolve_font(reference: str, workspace: Path) -> FontResolution:
    """
    Resolve an overlay's font, falling back to a system font if needed.

    A `FontResolution` with `path is None` means neither the requested font
    nor any fallback exists, which is the one case where overlays cannot be
    rendered at all.
    """
    resolved = resolve_asset(reference, workspace)
    if resolved is not None:
        return FontResolution(reference, resolved, used_fallback=False)

    fallback = find_system_font()
    return FontResolution(reference, fallback, used_fallback=True)


def measure_text(
    text: str,
    font_path: Path | None,
    font_size: int,
    *,
    stroke_width: int = 0,
    font=None,
) -> tuple[int, int]:
    """
    Pixel width and height the text will occupy, stroke included.

    An explicit `font` object can be passed to avoid rebuilding the font
    for every measurement in a loop.
    """
    if font is None:
        if font_path is None:
            raise ValueError("A font path or font object is required")
        font = ImageFont.truetype(str(font_path), font_size)

    left, top, right, bottom = font.getbbox(text)
    width = right - left
    height = bottom - top

    # A stroke is drawn on both sides of every glyph.
    return width + 2 * stroke_width, height + 2 * stroke_width


def overlay_allowed_area(
    position: str, frame_size: tuple[int, int]
) -> tuple[int, int]:
    """Pixels an overlay may use at a given placement."""
    frame_width, frame_height = frame_size
    allowed_width = int(frame_width * OVERLAY_WIDTH_FRACTION)
    if position in CORNER_POSITIONS:
        allowed_width //= 2
    allowed_height = int(
        frame_height
        * POSITION_HEIGHT_FRACTION.get(position, OVERLAY_HEIGHT_FRACTION)
    )
    return allowed_width, allowed_height


def fit_overlay_font_size(
    text: str,
    font_path: Path | None,
    *,
    requested_size: int,
    position: str,
    frame_size: tuple[int, int],
    stroke_width: int = 0,
    min_size: int = 12,
) -> int:
    """
    The size an overlay must be rendered at to stay inside its placement.

    Returns `requested_size` when the text already fits, so callers can
    compare and only report when the render had to shrink it.
    """
    if font_path is None:
        return requested_size

    allowed_width, allowed_height = overlay_allowed_area(position, frame_size)
    width, height = measure_text(
        text, font_path, requested_size, stroke_width=stroke_width
    )
    if width <= allowed_width and height <= allowed_height:
        return requested_size

    return largest_fitting_size(
        text,
        font_path,
        max_width=allowed_width,
        max_height=allowed_height,
        start_size=requested_size,
        stroke_width=stroke_width,
        min_size=min_size,
    )


def largest_fitting_size(
    text: str,
    font_path: Path | None,
    *,
    max_width: int,
    max_height: int,
    start_size: int,
    stroke_width: int = 0,
    min_size: int = 12,
) -> int:
    """
    Largest font size at or below `start_size` that keeps the text within
    the given box.  Returns `min_size` when even that does not fit, so the
    caller can still report a number and flag the overlay.
    """
    if font_path is None:
        return min_size

    size = start_size
    while size > min_size:
        font = ImageFont.truetype(str(font_path), size)
        width, height = measure_text(
            text, font_path, size, stroke_width=stroke_width, font=font
        )
        if width <= max_width and height <= max_height:
            return size
        # Step down in proportion to the overflow, but always progress.
        over = max(width / max_width, height / max_height, 1.01)
        size = max(min_size, int(size / over) - 1)

    return min_size
