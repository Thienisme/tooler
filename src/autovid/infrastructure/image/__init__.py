"""Image preparation, quality analysis and text-overlay fitting."""

from autovid.infrastructure.image.fonts import (
    measure_text,
    resolve_font,
    largest_fitting_size,
)
from autovid.infrastructure.image.prepare import (
    Geometry,
    compute_geometry,
    load_image,
    prepare_image,
    save_image,
)
from autovid.infrastructure.image.quality import (
    BlockinessReport,
    ResolutionReport,
    SharpnessReport,
    analyse_blockiness,
    analyse_resolution,
    analyse_sharpness,
    mean_colour,
)

__all__ = [
    "BlockinessReport",
    "Geometry",
    "ResolutionReport",
    "SharpnessReport",
    "analyse_blockiness",
    "analyse_resolution",
    "analyse_sharpness",
    "compute_geometry",
    "largest_fitting_size",
    "load_image",
    "mean_colour",
    "measure_text",
    "prepare_image",
    "resolve_font",
    "save_image",
]
