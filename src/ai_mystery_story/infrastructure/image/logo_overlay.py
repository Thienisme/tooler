"""
Logo Overlay Utility - Overlays channel logo (projects/logo/logo.png)
over the AI watermark at the bottom-right corner of thumbnail images.

Supported watermark modes:
- "gemini": logo over the Gemini ✦ icon (legacy margins tuned for ✦).
- "dola":   logo stamped at the true bottom-right corner, sized (default
            22% of image width, tunable via --scale) so it covers the
            "Dola AI" text. No blur/erase step — the logo covers it all.
"""

from pathlib import Path

import numpy as np
from PIL import Image


class LogoOverlay:
    """
    Cover up the AI watermark (Gemini ✦ or Dola AI text) in the
    bottom-right corner of images with the channel logo.
    """

    def __init__(
        self,
        logo_path: Path | None = None,
        scale_ratio: float = 0.09,
        min_logo_width: int = 90,
        margin_ratio: float = 0.015,
        watermark: str = "gemini",
    ):
        if watermark not in ("gemini", "dola"):
            raise ValueError(f"Unknown watermark mode: {watermark!r}")
        self.logo_path = logo_path
        self.scale_ratio = scale_ratio
        self.min_logo_width = min_logo_width
        self.margin_ratio = margin_ratio
        self.watermark = watermark

    @staticmethod
    def _detect_corner_text(
        img: Image.Image,
    ) -> tuple[int, int, int, int] | None:
        """
        Detect bright text (e.g. "Dola AI") in the bottom-right corner.

        Returns (x0, y0, x1, y1) of the text bbox, or None if no clear
        text is found. Looks only at the bottom-right region (bottom 15%,
        right 28%) and percentile-trims outliers so bright artwork
        (clouds, glow, lightning) does not inflate the box.
        """
        w, h = img.size
        x0, y0 = int(w * 0.72), int(h * 0.85)
        a = np.asarray(img.convert("RGB"), dtype=int)
        region = a[y0:, x0:]
        bright = (region.min(axis=2) > 160) & (
            (region.max(axis=2) - region.min(axis=2)) < 60
        )
        ys, xs = np.where(bright)
        if len(xs) < 50:
            return None
        # Percentile trim: reject outlier bright pixels from artwork
        xa, xb = np.percentile(xs, [2, 98]).astype(int)
        ya, yb = np.percentile(ys, [2, 98]).astype(int)
        return int(xa + x0), int(ya + y0), int(xb + x0), int(yb + y0)

    def apply_to_thumbnail(
        self,
        thumbnail_path: Path,
        logo_path: Path | None = None,
        output_path: Path | None = None,
    ) -> Path:
        """
        Overlay logo over the bottom-right corner of the thumbnail image.

        Args:
            thumbnail_path: Path to thumbnail image.
            logo_path: Path to logo image (defaults to projects/logo/logo.png).
            output_path: Path to save result (overwrites thumbnail_path if None).

        Returns:
            Path to updated thumbnail image.
        """
        if not thumbnail_path.exists():
            raise FileNotFoundError(f"Thumbnail image not found: {thumbnail_path}")

        effective_logo_path = logo_path or self.logo_path
        if effective_logo_path is None or not effective_logo_path.exists():
            raise FileNotFoundError(f"Logo image not found: {effective_logo_path}")

        if output_path is None:
            output_path = thumbnail_path

        # Open thumbnail and logo
        thumb_img = Image.open(thumbnail_path).convert("RGBA")
        logo_img = Image.open(effective_logo_path).convert("RGBA")

        thumb_w, thumb_h = thumb_img.size

        # (text_box is no longer needed for positioning — the logo is
        # always flush to the true corner in dola mode. Kept for the
        # info printout below.)

        # Calculate proportional logo dimensions to cover the watermark.
        # Gemini needs the absolute 90px floor so the ✦ icon is always
        # covered even on small images; Dola default (22% width) is sized
        # so the logo covers the "Dola AI" text outright — no blur needed.
        min_w = self.min_logo_width if self.watermark == "gemini" else 24
        target_w = max(int(thumb_w * self.scale_ratio), min_w)
        aspect = logo_img.height / logo_img.width
        target_h = int(target_w * aspect)

        # Resize logo with high quality Lanczos filter
        logo_resized = logo_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # Position at bottom-right corner with small margin from edge
        if self.watermark == "dola":
            # --scale is a HARD size: never grow the logo beyond it.
            # Always flush to the TRUE image corner (2px pad): the Dola
            # watermark hugs the corner, so the logo must sit over it —
            # shifted right+down, never inward.
            pos_x = max(thumb_w - target_w - 2, 0)
            pos_y = max(thumb_h - target_h - 2, 0)
        else:
            margin_x = max(int(thumb_w * self.margin_ratio), 12) + 45 # Tăng 50px lề phải -> Logo sẽ dịch
            margin_y = max(int(thumb_h * self.margin_ratio), 12) + 65 # Tăng 65px lề dưới -> Logo sẽ dịch
            pos_x = thumb_w - target_w - margin_x
            pos_y = thumb_h - target_h - margin_y

        # Paste resized logo with transparency alpha mask
        thumb_img.paste(logo_resized, (pos_x, pos_y), logo_resized)

        # Save image preserving original format (JPEG or PNG)
        if output_path.suffix.lower() in [".jpg", ".jpeg"]:
            final_img = thumb_img.convert("RGB")
            final_img.save(output_path, quality=95)
        else:
            thumb_img.save(output_path)

        wm_label = "Dola AI" if self.watermark == "dola" else "✦"
        print(
            f"  ✅ Logo overlaid on thumbnail: {output_path.name} "
            f"(Covered {wm_label} at pos [{pos_x}, {pos_y}], logo size: {target_w}x{target_h})"
        )

        return output_path


def apply_logo_to_topic_thumbnail(
    topic_id: str,
    projects_dir: Path,
    logo_path: Path | None = None,
    watermark: str = "gemini",
    scale_ratio: float | None = None,
) -> Path:
    """
    Find the first thumbnail in projects/{topic_id}/thumbnail/
    and cover up the watermark with the channel logo.
    """
    topic_dir = projects_dir / topic_id
    thumb_dir = topic_dir / "thumbnail"

    if not thumb_dir.exists():
        raise FileNotFoundError(f"Thumbnail directory not found: {thumb_dir}")

    # Find the first image in thumbnail directory
    image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
    thumb_files = sorted([
        f for f in thumb_dir.iterdir()
        if f.suffix.lower() in image_extensions
    ])

    if not thumb_files:
        raise RuntimeError(f"No thumbnail image found in: {thumb_dir}")

    first_thumb = thumb_files[0]
    effective_logo = logo_path or (projects_dir / "logo" / "logo.png")

    overlay_kwargs = {"logo_path": effective_logo, "watermark": watermark}
    if scale_ratio is not None:
        overlay_kwargs["scale_ratio"] = scale_ratio
    overlay = LogoOverlay(**overlay_kwargs)
    return overlay.apply_to_thumbnail(
        thumbnail_path=first_thumb,
        logo_path=effective_logo,
    )


def apply_logo_to_topic_images(
    topic_id: str,
    projects_dir: Path,
    logo_path: Path | None = None,
    watermark: str = "gemini",
    scale_ratio: float | None = None,
) -> list[Path]:
    """
    Apply logo overlay to every image in projects/{topic_id}/images/.

    Returns a list of processed image paths. Skips the directory silently
    if it does not exist or contains no images.
    """
    topic_dir = projects_dir / topic_id
    images_dir = topic_dir / "images"

    if not images_dir.exists():
        return []

    image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
    image_files = sorted([
        f for f in images_dir.iterdir()
        if f.suffix.lower() in image_extensions
    ])

    if not image_files:
        return []

    effective_logo = logo_path or (projects_dir / "logo" / "logo.png")
    overlay_kwargs = {"logo_path": effective_logo, "watermark": watermark}
    if scale_ratio is not None:
        overlay_kwargs["scale_ratio"] = scale_ratio
    overlay = LogoOverlay(**overlay_kwargs)

    results: list[Path] = []
    for img_path in image_files:
        results.append(
            overlay.apply_to_thumbnail(
                thumbnail_path=img_path,
                logo_path=effective_logo,
            )
        )

    return results
