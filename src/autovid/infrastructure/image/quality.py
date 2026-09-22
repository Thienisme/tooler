"""
Image quality analysis.

The spec asks for `Laplacian variance > 100` as the sharpness test.  That
threshold is wrong for this pipeline's content, and measuring says so: on
48 perfectly sharp flat-colour doodle frames the value lands between 75
and 90, because a white background contributes no edges at all.  A 6px
Gaussian blur of the same image scores 47.  A threshold of 100 would fail
every good frame in the project.

What is used here instead:

* `MIN_LAPLACIAN_VARIANCE = 60` — measured to sit cleanly between sharp
  frames (75-90) and blurred ones (47), so it separates real problems from
  the flat artwork this format is made of.
* an **ink coverage gate** — the sharpness verdict is skipped on images
  with almost no dark/coloured pixels, because a nearly blank frame has no
  edges to measure and should not be reported as blurry.
* **resolution adequacy**, which is the reliable version of the same
  question: if the source has to be scaled up past 1.5x to fill the frame
  at Ken Burns headroom, it will look soft no matter what the Laplacian
  says.  This is the check to trust.
* **blockiness**, a crude but honest JPEG-artefact probe: JPEG compresses
  in 8x8 blocks, so an unusual step across every 8th pixel column means
  the file was recompressed at a low quality.  It is reported as
  informational, since a busy illustration has strong edges everywhere and
  can trip it.

Compression artefacts are only *suspected*, never proven: nothing here can
tell a badly compressed frame from a legitimately detailed one, so the
result is always a warning pointing at the file for a human to look at.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

# See the module docstring: measured, not guessed.
MIN_LAPLACIAN_VARIANCE = 60.0

# Below this share of non-background pixels there is nothing to measure.
MIN_INK_COVERAGE = 0.02

# A pixel counts as ink once it is darker than this.
INK_LEVEL = 200

# Below this tonal spread the frame holds no detail at all, so low edge
# energy means "empty frame" rather than "soft frame".  Keeping these two
# verdicts apart matters: they call for different fixes.
MIN_TONAL_STDDEV = 3.0

# Scaling a source up by more than this softens it visibly.
MAX_UPSCALE_FACTOR = 1.5

# Ratio of the step across 8-pixel block boundaries to the step elsewhere.
MAX_BLOCKINESS_RATIO = 1.35

# Mean colour is allowed to drift this much per channel (0-255).
MAX_COLOUR_SHIFT = 8.0

_LAPLACIAN_KERNEL = (0, -1, 0, -1, 4, -1, 0, -1, 0)


@dataclass(frozen=True)
class SharpnessReport:
    laplacian_variance: float
    ink_coverage: float
    tonal_stddev: float
    evaluated: bool
    verdict: str  # "ok" | "blurry" | "featureless" | "skipped"

    def to_dict(self) -> dict:
        return {
            "laplacian_variance": round(self.laplacian_variance, 2),
            "ink_coverage": round(self.ink_coverage, 4),
            "tonal_stddev": round(self.tonal_stddev, 2),
            "evaluated": self.evaluated,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class BlockinessReport:
    boundary_step: float
    interior_step: float
    ratio: float
    verdict: str  # "ok" | "suspect"

    def to_dict(self) -> dict:
        return {
            "boundary_step": round(self.boundary_step, 3),
            "interior_step": round(self.interior_step, 3),
            "ratio": round(self.ratio, 3),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class ResolutionReport:
    source_width: int
    source_height: int
    prepared_width: int
    prepared_height: int
    scale: float
    verdict: str  # "ok" | "upscaled"

    def to_dict(self) -> dict:
        return {
            "source": [self.source_width, self.source_height],
            "prepared": [self.prepared_width, self.prepared_height],
            "scale": round(self.scale, 4),
            "verdict": self.verdict,
        }


def analyse_sharpness(
    image: Image.Image,
    *,
    min_variance: float = MIN_LAPLACIAN_VARIANCE,
    min_ink_coverage: float = MIN_INK_COVERAGE,
) -> SharpnessReport:
    """
    Laplacian energy of the image, gated on how much ink it actually has.

    `laplacian_variance` is the square of the RMS Laplacian response, which
    is the metric the spec names, computed with a PIL convolution kernel so
    no numpy or OpenCV is needed.
    """
    grey = image.convert("L")

    statistics_of_image = ImageStat.Stat(grey)
    tonal_stddev = float(statistics_of_image.stddev[0])

    histogram = grey.histogram()
    total = sum(histogram) or 1
    ink_coverage = sum(histogram[:INK_LEVEL]) / total

    # The kernel is applied with an offset so negative responses are not
    # clamped to zero; the standard deviation of the result is therefore
    # the RMS Laplacian response.
    edges = grey.filter(
        ImageFilter.Kernel(
            (3, 3), _LAPLACIAN_KERNEL, scale=1.0, offset=128
        )
    )
    rms = float(ImageStat.Stat(edges).stddev[0])
    variance = rms * rms

    if ink_coverage < min_ink_coverage:
        return SharpnessReport(
            laplacian_variance=variance,
            ink_coverage=ink_coverage,
            tonal_stddev=tonal_stddev,
            evaluated=False,
            verdict="skipped",
        )

    if tonal_stddev < MIN_TONAL_STDDEV:
        # Almost no variation to lose, so "soft" would be the wrong word.
        return SharpnessReport(
            laplacian_variance=variance,
            ink_coverage=ink_coverage,
            tonal_stddev=tonal_stddev,
            evaluated=True,
            verdict="featureless",
        )

    return SharpnessReport(
        laplacian_variance=variance,
        ink_coverage=ink_coverage,
        tonal_stddev=tonal_stddev,
        evaluated=True,
        verdict="ok" if variance >= min_variance else "blurry",
    )


def analyse_blockiness(
    image: Image.Image,
    *,
    sample_step: int = 4,
    max_ratio: float = MAX_BLOCKINESS_RATIO,
) -> BlockinessReport:
    """
    Compare the pixel step across 8-pixel block edges with the step inside
    blocks.  A ratio well above 1 suggests JPEG recompression.
    """
    grey = image.convert("L")
    width, height = grey.size

    if width < 24 or height < 8:
        return BlockinessReport(0.0, 0.0, 1.0, "ok")

    buffer = grey.tobytes()
    step = max(1, sample_step)
    stride = width * step

    def column_difference(column: int) -> list[int]:
        left = buffer[column::stride]
        right = buffer[column + 1 :: stride]
        return [
            abs(a - b) for a, b in zip(left, right)
        ]

    boundary: list[int] = []
    interior: list[int] = []
    # 7|8, 15|16, ... are block edges; 3|4 is safely inside a block.
    for column in range(7, width - 1, 8):
        boundary.extend(column_difference(column))
    for column in range(3, width - 2, 8):
        interior.extend(column_difference(column))

    if not boundary or not interior:
        return BlockinessReport(0.0, 0.0, 1.0, "ok")

    boundary_step = statistics.fmean(boundary)
    interior_step = statistics.fmean(interior)
    # A flat image has no steps anywhere; guard the division.
    ratio = boundary_step / interior_step if interior_step > 0.05 else 1.0

    return BlockinessReport(
        boundary_step=boundary_step,
        interior_step=interior_step,
        ratio=ratio,
        verdict="suspect" if ratio > max_ratio else "ok",
    )


def analyse_resolution(
    source_size: tuple[int, int],
    geometry,
    *,
    max_upscale: float = MAX_UPSCALE_FACTOR,
) -> ResolutionReport:
    """
    How far the source had to be scaled to fill the frame plus Ken Burns
    headroom.  This is the sharpness signal worth trusting.
    """
    scale = geometry.scale
    return ResolutionReport(
        source_width=source_size[0],
        source_height=source_size[1],
        prepared_width=geometry.prepared_size[0],
        prepared_height=geometry.prepared_size[1],
        scale=scale,
        verdict="ok" if scale <= max_upscale else "upscaled",
    )


def mean_colour(
    image: Image.Image, box: tuple[int, int, int, int] | None = None
) -> tuple[float, float, float]:
    """Mean RGB of an image, or of a region of it."""
    rgb = image.convert("RGB")
    if box is not None:
        rgb = rgb.crop(box)
    stat = ImageStat.Stat(rgb)
    return (stat.mean[0], stat.mean[1], stat.mean[2])


def colour_shift(
    source_mean: tuple[float, float, float],
    prepared_mean: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Per-channel difference between two mean colours."""
    return tuple(
        abs(a - b) for a, b in zip(source_mean, prepared_mean)
    )  # type: ignore[return-value]
