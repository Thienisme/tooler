"""
Stage 3 — image preparation.

Every scene's artwork is scaled once, at the size the render stage needs,
and measured while it is open so the report can say *why* a frame might
look wrong rather than just that something is off.

What is checked
---------------
* **resolution adequacy** — how far the source had to be scaled up.  This
  is the sharpness signal worth trusting, because it is a fact about the
  file rather than a heuristic about its content.
* **Laplacian energy**, gated on how much ink the frame actually contains,
  with a threshold measured on this project's own artwork (see
  `quality.py` for the numbers).
* **blockiness** — a crude probe for JPEG recompression, reported as
  informational.
* **colour fidelity** — mean colour of the source against the mean colour
  of the prepared region, which catches an accidental colour-space
  conversion during scaling.
* **fonts and overlay fit** — resolves each font with a system fallback,
  measures the text, and reports the largest size that *would* fit.
* **overlay timing against measured narration**, when stage 2 has already
  produced `timeline.json`.  This upgrades stage 1's estimate-based check
  to an exact one.

Outputs
-------
    output/prepared_images/scene_001.png ...      frames ready to render
    output/image_report.json                      measurements and verdicts
    output/state.json                             resume manifest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.domain.script import Script, Scene
from autovid.infrastructure.image.fonts import (
    FontResolution,
    largest_fitting_size,
    measure_text,
    overlay_allowed_area,
    resolve_font,
)
from autovid.infrastructure.image.prepare import (
    DEFAULT_PAD_COLOUR,
    FIT_MODES,
    Geometry,
    compute_geometry,
    load_image,
    prepare_image,
    save_image,
)
from autovid.infrastructure.image.quality import (
    MAX_COLOUR_SHIFT,
    BlockinessReport,
    ResolutionReport,
    SharpnessReport,
    analyse_blockiness,
    analyse_resolution,
    analyse_sharpness,
    colour_shift,
    mean_colour,
)
from autovid.paths import Paths, read_json, resolve_asset, write_json

# Overlay timing is only flagged past this much overshoot.
OVERLAY_TIMING_TOLERANCE_MS = 250


@dataclass
class OverlayCheck:
    """What was found about one text overlay."""

    index: int
    text: str
    font: FontResolution
    font_size: int
    stroke_width: int
    measured_width: int
    measured_height: int
    allowed_width: int
    allowed_height: int
    fits: bool
    recommended_font_size: int
    narration_end_ms: int | None = None
    timing_ok: bool | None = None

    def to_dict(self) -> dict:
        payload = {
            "index": self.index,
            "text": self.text,
            "font": self.font.to_dict(),
            "font_size": self.font_size,
            "stroke_width": self.stroke_width,
            "measured": [self.measured_width, self.measured_height],
            "allowed": [self.allowed_width, self.allowed_height],
            "fits": self.fits,
            "recommended_font_size": self.recommended_font_size,
        }
        if self.narration_end_ms is not None:
            payload["narration_end_ms"] = self.narration_end_ms
            payload["timing_ok"] = self.timing_ok
        return payload


@dataclass
class SceneImageResult:
    scene_id: int
    index: int
    source: Path
    prepared: Path
    geometry: Geometry
    sharpness: SharpnessReport
    blockiness: BlockinessReport
    resolution: ResolutionReport
    colour_shift: tuple[float, float, float]
    overlays: list[OverlayCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.scene_id,
            "index": self.index,
            "source": str(self.source),
            "prepared": str(self.prepared),
            "geometry": self.geometry.to_dict(),
            "sharpness": self.sharpness.to_dict(),
            "blockiness": self.blockiness.to_dict(),
            "resolution": self.resolution.to_dict(),
            "colour_shift": [round(value, 2) for value in self.colour_shift],
            "overlays": [overlay.to_dict() for overlay in self.overlays],
        }


@dataclass
class ImagesStageResult:
    status: str
    scenes: list[SceneImageResult]
    report: dict
    issues: list[Issue] = field(default_factory=list)

    @property
    def prepared_paths(self) -> dict[int, Path]:
        return {scene.scene_id: scene.prepared for scene in self.scenes}


class ImagesStage:
    """Prepares and quality-checks every scene image."""

    def __init__(
        self,
        script: Script,
        paths: Paths,
        *,
        fit: str = "pad",
        pad_colour: str = DEFAULT_PAD_COLOUR,
        progress=None,
    ) -> None:
        if fit not in FIT_MODES:
            raise ValueError(f"fit must be one of {FIT_MODES}, got '{fit}'")

        self.script = script
        self.paths = paths
        self.fit = fit
        self.pad_colour = pad_colour
        self.progress = progress or (lambda message: None)
        self.issues = IssueCollector()
        self._narration_ms = self._load_narration_ms()

    # -- entry point ------------------------------------------------------

    def run(self) -> ImagesStageResult:
        self.paths.create()

        results: list[SceneImageResult] = []
        for index, scene in enumerate(self.script.scenes):
            result = self._process_scene(scene, index=index)
            if result is not None:
                results.append(result)
                self.progress(
                    f"scene {scene.id}/{len(self.script.scenes)} "
                    f"{result.geometry.prepared_size[0]}x"
                    f"{result.geometry.prepared_size[1]}"
                )

        report = self._build_report(results)
        write_json(self.paths.output_dir / "image_report.json", report)

        # No later stage can invent frames that were never produced.
        if len(results) != len(self.script.scenes):
            self.issues.error(
                "images_incomplete",
                f"prepared {len(results)} of {len(self.script.scenes)} frames; "
                "the render stage would be missing images",
            )

        return ImagesStageResult(
            status=self.issues.status,
            scenes=results,
            report=report,
            issues=self.issues.issues,
        )

    # -- per scene --------------------------------------------------------

    def _process_scene(
        self, scene: Scene, *, index: int
    ) -> SceneImageResult | None:
        source = self._resolve_source(scene)
        if source is None:
            return None

        try:
            image = load_image(source)
        except Exception as error:  # noqa: BLE001 - reported as a stage error
            self.issues.error(
                "image_unreadable",
                f"could not read {scene.image_file}: {error}",
                scene_id=scene.id,
            )
            return None

        metadata = self.script.video_metadata
        frame_size = (metadata.width, metadata.height)

        geometry = compute_geometry(
            image.size,
            frame_size,
            self._ken_burns_scale(scene),
            fit=self.fit,
        )

        sharpness = analyse_sharpness(image)
        blockiness = analyse_blockiness(image)
        resolution = analyse_resolution(image.size, geometry)

        self._report_quality(scene, sharpness, blockiness, resolution)

        prepared_image = prepare_image(
            image, geometry, pad_colour=self.pad_colour
        )
        destination = (
            self.paths.prepared_images_dir / f"scene_{scene.id:03d}.png"
        )
        save_image(prepared_image, destination)

        # Compare the prepared frame against the part of the source that was
        # actually mapped onto it.  In `cover` mode only the centre survives,
        # so the whole artwork's mean colour is the wrong reference and every
        # crop would look like a colour-space accident.
        reference = image
        if geometry.crop_box is not None:
            scale = geometry.scale or 1.0
            left, top, right, bottom = geometry.crop_box
            reference = image.crop(
                (
                    int(round(left / scale)),
                    int(round(top / scale)),
                    int(round(right / scale)),
                    int(round(bottom / scale)),
                )
            )
        shift = colour_shift(
            mean_colour(reference),
            mean_colour(prepared_image, geometry.content_box),
        )
        if max(shift) > MAX_COLOUR_SHIFT:
            self.issues.warn(
                "colour_shift",
                f"mean colour moved by R{shift[0]:.1f} G{shift[1]:.1f} "
                f"B{shift[2]:.1f} while scaling; check for an unintended "
                "colour-space conversion",
                scene_id=scene.id,
            )

        overlays = self._check_overlays(scene, frame_size)

        return SceneImageResult(
            scene_id=scene.id,
            index=index,
            source=source,
            prepared=destination,
            geometry=geometry,
            sharpness=sharpness,
            blockiness=blockiness,
            resolution=resolution,
            colour_shift=shift,
            overlays=overlays,
        )

    def _resolve_source(self, scene: Scene) -> Path | None:
        if not scene.image_file:
            self.issues.error(
                "image_not_provided",
                "scene has an image_prompt but no image_file, and this stage "
                "only prepares existing files; generate the image first",
                scene_id=scene.id,
            )
            return None

        resolved = resolve_asset(scene.image_file, self.paths.workspace)
        if resolved is None:
            self.issues.error(
                "image_missing",
                f"image not found: {scene.image_file}",
                scene_id=scene.id,
            )
            return None
        return resolved

    @staticmethod
    def _ken_burns_scale(scene: Scene) -> float:
        """
        The largest scale this scene's motion needs.

        A disabled effect, or a pan/zoom that never scales, still needs the
        frame at 1:1, so the floor is 1.0.
        """
        motion = scene.ken_burns
        if not motion.enabled or motion.type == "none":
            return 1.0
        return max(motion.start_scale, motion.end_scale)

    def _report_quality(
        self,
        scene: Scene,
        sharpness: SharpnessReport,
        blockiness: BlockinessReport,
        resolution: ResolutionReport,
    ) -> None:
        if resolution.verdict == "upscaled":
            self.issues.warn(
                "image_upscaled",
                f"source is {resolution.source_width}x"
                f"{resolution.source_height} but the frame needs "
                f"{resolution.prepared_width}x{resolution.prepared_height}; "
                f"scaled {resolution.scale:.2f}x, which will look soft",
                scene_id=scene.id,
            )

        if sharpness.verdict == "blurry":
            self.issues.warn(
                "image_blurry",
                f"Laplacian variance {sharpness.laplacian_variance:.0f} is "
                "below the measured floor; sharp frames in this project "
                "score 75-90, so this frame looks soft",
                scene_id=scene.id,
            )
        elif sharpness.verdict == "featureless":
            self.issues.warn(
                "image_featureless",
                f"tonal spread is only {sharpness.tonal_stddev:.1f}, so the "
                "frame holds almost no detail to animate; check that this is "
                "the image the scene was meant to use",
                scene_id=scene.id,
            )

        if blockiness.verdict == "suspect":
            self.issues.warn(
                "image_blocky",
                f"pixel steps across 8-pixel block edges are "
                f"{blockiness.ratio:.2f}x the steps elsewhere, which suggests "
                "JPEG recompression; worth eyeballing the frame",
                scene_id=scene.id,
            )

    # -- overlays ---------------------------------------------------------

    def _check_overlays(
        self, scene: Scene, frame_size: tuple[int, int]
    ) -> list[OverlayCheck]:
        checks: list[OverlayCheck] = []
        narration_ms = self._narration_ms.get(scene.id)

        for position, overlay in enumerate(scene.text_overlays):
            font = resolve_font(overlay.font, self.paths.workspace)

            if font.path is None:
                self.issues.error(
                    "font_unavailable",
                    f"overlay {position} needs font '{overlay.font}' and no "
                    "system font could be found to fall back to",
                    scene_id=scene.id,
                    unit_index=position,
                )
                continue

            if font.used_fallback:
                self.issues.warn(
                    "font_fallback",
                    f"overlay {position} requested '{overlay.font}', which does "
                    f"not exist; using {font.path.name} instead",
                    scene_id=scene.id,
                    unit_index=position,
                )

            allowed_width, allowed_height = overlay_allowed_area(
                overlay.position, frame_size
            )

            width, height = measure_text(
                overlay.text,
                font.path,
                overlay.font_size,
                stroke_width=overlay.stroke_width,
            )
            fits = width <= allowed_width and height <= allowed_height

            recommended = overlay.font_size
            if not fits:
                recommended = largest_fitting_size(
                    overlay.text,
                    font.path,
                    max_width=allowed_width,
                    max_height=allowed_height,
                    start_size=overlay.font_size,
                    stroke_width=overlay.stroke_width,
                )
                self.issues.warn(
                    "overlay_overflow",
                    f"overlay {position} '{overlay.text}' measures "
                    f"{width}x{height}px at size {overlay.font_size} but the "
                    f"{overlay.position} area allows {allowed_width}x"
                    f"{allowed_height}px; size {recommended} would fit",
                    scene_id=scene.id,
                    unit_index=position,
                )

            timing_ok = None
            if narration_ms is not None:
                timing_ok = (
                    overlay.end_offset_ms
                    <= narration_ms + OVERLAY_TIMING_TOLERANCE_MS
                )
                if not timing_ok:
                    self.issues.warn(
                        "overlay_past_narration",
                        f"overlay {position} is visible until "
                        f"{overlay.end_offset_ms}ms but the scene's measured "
                        f"narration is {narration_ms}ms, so it would sit on a "
                        "frozen frame",
                        scene_id=scene.id,
                        unit_index=position,
                    )

            checks.append(
                OverlayCheck(
                    index=position,
                    text=overlay.text,
                    font=font,
                    font_size=overlay.font_size,
                    stroke_width=overlay.stroke_width,
                    measured_width=width,
                    measured_height=height,
                    allowed_width=allowed_width,
                    allowed_height=allowed_height,
                    fits=fits,
                    recommended_font_size=recommended,
                    narration_end_ms=narration_ms,
                    timing_ok=timing_ok,
                )
            )

        return checks

    def _load_narration_ms(self) -> dict[int, int]:
        """
        Measured narration length per scene, if stage 2 has run.

        Falls back to an empty mapping so this stage stays usable on its
        own, in which case the timing cross-check is simply skipped.
        """
        payload = read_json(self.paths.output_dir / "timeline.json")
        if not isinstance(payload, dict):
            return {}

        durations: dict[int, int] = {}
        for entry in payload.get("scenes", []):
            try:
                durations[int(entry["id"])] = int(
                    round(float(entry["narration_s"]) * 1000)
                )
            except (KeyError, TypeError, ValueError):
                continue
        return durations

    # -- report -----------------------------------------------------------

    def _build_report(self, scenes: list[SceneImageResult]) -> dict:
        metadata = self.script.video_metadata

        # Each scene is prepared at its own Ken Burns scale, so sizes differ
        # slightly between scenes: zooming less means less upscaling, and
        # keeping it per-scene avoids softening frames that never zoom.
        # Every prepared size is a superset of the frame, which is all the
        # render stage needs to know.
        size_counts: dict[tuple[int, int], int] = {}
        for scene in scenes:
            key = scene.geometry.prepared_size
            size_counts[key] = size_counts.get(key, 0) + 1

        prepared_sizes = sorted(size_counts, key=lambda size: -size[0])
        if len(prepared_sizes) == 1:
            prepared_size = f"{prepared_sizes[0][0]}x{prepared_sizes[0][1]}"
        else:
            prepared_size = "mixed"

        disk_bytes = 0
        for scene in scenes:
            if scene.prepared.exists():
                disk_bytes += scene.prepared.stat().st_size

        sharpness_values = [
            scene.sharpness.laplacian_variance
            for scene in scenes
            if scene.sharpness.evaluated
        ]
        overlay_count = sum(len(scene.overlays) for scene in scenes)
        fallback_fonts = sum(
            1
            for scene in scenes
            for overlay in scene.overlays
            if overlay.font.used_fallback
        )

        return {
            "stage": "images",
            "status": self.issues.status,
            "settings": {
                "fit": self.fit,
                "pad_colour": self.pad_colour,
                "frame": metadata.resolution,
                "ken_burns_headroom": True,
                "pre_rendered_frames": False,
            },
            "totals": {
                "scenes": len(self.script.scenes),
                "prepared": len(scenes),
                "failed": len(self.script.scenes) - len(scenes),
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
                "prepared_size": prepared_size,
                "prepared_sizes": [
                    {
                        "size": f"{width}x{height}",
                        "scenes": size_counts[(width, height)],
                    }
                    for width, height in prepared_sizes
                ],
                "scale_min": (
                    round(min(scene.geometry.scale for scene in scenes), 3)
                    if scenes
                    else None
                ),
                "scale_max": (
                    round(max(scene.geometry.scale for scene in scenes), 3)
                    if scenes
                    else None
                ),
                "prepared_disk_mb": round(disk_bytes / (1024 * 1024), 1),
                "overlays": overlay_count,
                "fallback_fonts": fallback_fonts,
                "sharpness_evaluated": len(sharpness_values),
                "sharpness_min": (
                    round(min(sharpness_values), 1) if sharpness_values else None
                ),
                "sharpness_avg": (
                    round(sum(sharpness_values) / len(sharpness_values), 1)
                    if sharpness_values
                    else None
                ),
            },
            "scenes": [scene.to_dict() for scene in scenes],
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }


def prepared_image_path(paths: Paths, scene_id: int) -> Path:
    """Where stage 3 writes, and stage 4 reads, a scene's prepared frame."""
    return paths.prepared_images_dir / f"scene_{scene_id:03d}.png"
