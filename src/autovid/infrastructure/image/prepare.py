"""
Image preparation for rendering.

The important idea here is **Ken Burns headroom**.  A zoom or pan moves a
crop window inside the image, so the prepared image must be larger than
the output frame; preparing art at exactly 1920x1080 would force the
render stage to zoom into pixels that are not there, and every move would
come out soft.  Each frame is therefore prepared at
`frame_size x max_scale` (the largest scale that scene's Ken Burns asks
for), which makes the zoom lossless and the pan possible at all.

Note the deliberate omission: the spec suggests pre-rendering Ken Burns
into frame sequences.  For a 13 minute video that is roughly 23,000 PNGs
and tens of gigabytes of scratch space to save a filter evaluation that
ffmpeg performs in-line and for free.  The zoom is applied at render time
instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from autovid.domain.script import KEN_BURNS_MAX_SCALE, KEN_BURNS_MIN_SCALE

DEFAULT_PAD_COLOUR = "#FFFFFF"

# LANCZOS is the only resampling filter that does not soften flat-colour
# artwork; everything else either blurs or rings.
RESAMPLE = Image.Resampling.LANCZOS

FIT_MODES = ("pad", "cover")


@dataclass(frozen=True)
class Geometry:
    """How one source image maps onto the prepared canvas."""

    source_size: tuple[int, int]
    prepared_size: tuple[int, int]
    scaled_size: tuple[int, int]
    crop_box: tuple[int, int, int, int] | None
    paste_offset: tuple[int, int]
    content_box: tuple[int, int, int, int]
    scale: float
    fit: str
    max_scale: float

    def to_dict(self) -> dict:
        return {
            "source_size": list(self.source_size),
            "prepared_size": list(self.prepared_size),
            "scaled_size": list(self.scaled_size),
            "scale": round(self.scale, 4),
            "fit": self.fit,
            "ken_burns_max_scale": round(self.max_scale, 4),
            "content_box": list(self.content_box),
        }


def _even(value: float) -> int:
    """Round to the nearest even integer; H.264 requires even dimensions."""
    result = int(round(value))
    return result - (result % 2)


def compute_geometry(
    source_size: tuple[int, int],
    frame_size: tuple[int, int],
    max_scale: float,
    *,
    fit: str = "pad",
) -> Geometry:
    """
    Work out the canvas, the scale and where the art lands on it.

    `max_scale` is the largest Ken Burns scale this scene uses, so the
    canvas is big enough that zooming never has to invent pixels.
    """
    if fit not in FIT_MODES:
        raise ValueError(f"fit must be one of {FIT_MODES}, got '{fit}'")

    source_width, source_height = source_size
    if source_width <= 0 or source_height <= 0:
        raise ValueError(f"invalid source size: {source_size}")

    scale = min(
        max(max_scale, KEN_BURNS_MIN_SCALE), KEN_BURNS_MAX_SCALE
    )
    frame_width, frame_height = frame_size

    prepared_width = _even(frame_width * scale)
    prepared_height = _even(frame_height * scale)

    if fit == "cover":
        image_scale = max(
            prepared_width / source_width, prepared_height / source_height
        )
    else:
        image_scale = min(
            prepared_width / source_width, prepared_height / source_height
        )

    scaled_width = max(1, int(round(source_width * image_scale)))
    scaled_height = max(1, int(round(source_height * image_scale)))

    if fit == "cover":
        # The scaled art covers the canvas, so it is centre-cropped.
        left = (scaled_width - prepared_width) // 2
        top = (scaled_height - prepared_height) // 2
        crop_box = (left, top, left + prepared_width, top + prepared_height)
        paste_offset = (0, 0)
        content_box = (0, 0, prepared_width, prepared_height)
    else:
        crop_box = None
        offset_x = (prepared_width - scaled_width) // 2
        offset_y = (prepared_height - scaled_height) // 2
        paste_offset = (offset_x, offset_y)
        content_box = (
            offset_x,
            offset_y,
            offset_x + scaled_width,
            offset_y + scaled_height,
        )

    return Geometry(
        source_size=(source_width, source_height),
        prepared_size=(prepared_width, prepared_height),
        scaled_size=(scaled_width, scaled_height),
        crop_box=crop_box,
        paste_offset=paste_offset,
        content_box=content_box,
        scale=image_scale,
        fit=fit,
        max_scale=scale,
    )


def load_image(path: Path) -> Image.Image:
    """
    Read an image as RGB.

    Palette, greyscale and alpha images are all normalised, and the file
    handle is released before returning so Windows can later rewrite the
    file if needed.
    """
    with Image.open(path) as raw:
        raw.load()
        return raw.convert("RGB")


def prepare_image(
    image: Image.Image,
    geometry: Geometry,
    *,
    pad_colour: str = DEFAULT_PAD_COLOUR,
) -> Image.Image:
    """Scale, fit and place one image onto its prepared canvas."""
    canvas = Image.new("RGB", geometry.prepared_size, pad_colour)

    scaled = image.resize(geometry.scaled_size, RESAMPLE)
    if geometry.crop_box is not None:
        scaled = scaled.crop(geometry.crop_box)

    canvas.paste(scaled, geometry.paste_offset)
    return canvas


def save_image(image: Image.Image, destination: Path) -> None:
    """Write a prepared frame as PNG."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", compress_level=6)
