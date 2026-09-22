"""
Stage 3 tests: image preparation and quality analysis.

The sharpness thresholds tested here are the ones measured on this
project's own artwork (sharp frames score 75-90, a 6px blur scores 47),
not the spec's `> 100` which would fail every good frame.

Run with:
    python -m unittest tests.test_autovid_stage3
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from autovid.application.images import ImagesStage  # noqa: E402
from autovid.infrastructure.image.fonts import (  # noqa: E402
    find_system_font,
    largest_fitting_size,
    measure_text,
    resolve_font,
)
from autovid.infrastructure.image.prepare import (  # noqa: E402
    compute_geometry,
    prepare_image,
)
from autovid.infrastructure.image.quality import (  # noqa: E402
    MIN_LAPLACIAN_VARIANCE,
    analyse_blockiness,
    analyse_resolution,
    analyse_sharpness,
    colour_shift,
    mean_colour,
)
from autovid.paths import Paths  # noqa: E402
from autovid.presentation.cli import main  # noqa: E402

FRAME = (1920, 1080)


# --------------------------------------------------------------------------
# Synthetic images
# --------------------------------------------------------------------------


def doodle(size: tuple[int, int] = FRAME) -> Image.Image:
    """Flat-colour artwork on white, like the real project's frames."""
    image = Image.new("RGB", size, "#FFFFFF")
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.ellipse(
        (width // 5, height // 5, width // 2, height * 4 // 5),
        fill="#E4572E",
        outline="#1B1B1B",
        width=6,
    )
    draw.rectangle(
        (width * 2 // 3, height // 3, width * 4 // 5, height * 2 // 3),
        fill="#3D5A80",
        outline="#1B1B1B",
        width=6,
    )
    return image


def save(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


class GeometryTest(unittest.TestCase):

    def test_prepared_canvas_covers_frame_times_scale(self):
        geometry = compute_geometry((1920, 1080), FRAME, 1.10)
        self.assertEqual(geometry.prepared_size, (2112, 1188))
        self.assertGreater(geometry.prepared_size[0], FRAME[0])
        self.assertGreater(geometry.prepared_size[1], FRAME[1])

    def test_scale_is_clamped_to_the_supported_range(self):
        low = compute_geometry((1920, 1080), FRAME, 0.5)
        high = compute_geometry((1920, 1080), FRAME, 9.0)
        self.assertEqual(low.max_scale, 1.0)
        self.assertEqual(high.max_scale, 1.5)

    def test_pad_keeps_the_whole_image_and_letterboxes(self):
        geometry = compute_geometry((1000, 1000), (1920, 1080), 1.0, fit="pad")
        prepared = prepare_image(
            Image.new("RGB", (1000, 1000), "#FF0000"),
            geometry,
            pad_colour="#FFFFFF",
        )
        left, top, right, bottom = geometry.content_box
        self.assertEqual(prepared.size, geometry.prepared_size)
        # Outside the content box is padding; inside it is the image.
        self.assertEqual(prepared.getpixel((2, 2)), (255, 255, 255))
        self.assertEqual(
            prepared.getpixel(((left + right) // 2, (top + bottom) // 2)),
            (255, 0, 0),
        )

    def test_cover_fills_the_frame_and_crops(self):
        geometry = compute_geometry((1000, 1000), (1920, 1080), 1.0, fit="cover")
        prepared = prepare_image(
            Image.new("RGB", (1000, 1000), "#FF0000"),
            geometry,
            pad_colour="#FFFFFF",
        )
        self.assertEqual(prepared.size, geometry.prepared_size)
        self.assertNotEqual(prepared.getpixel((2, 2)), (255, 255, 255))
        self.assertEqual(geometry.content_box, (0, 0, *geometry.prepared_size))

    def test_invalid_fit_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_geometry((100, 100), FRAME, 1.0, fit="stretch")


# --------------------------------------------------------------------------
# Quality metrics
# --------------------------------------------------------------------------


class QualityTest(unittest.TestCase):

    def test_sharp_doodle_passes(self):
        report = analyse_sharpness(doodle())
        self.assertEqual(report.verdict, "ok")
        self.assertGreater(report.laplacian_variance, MIN_LAPLACIAN_VARIANCE)

    def test_blurred_doodle_is_flagged(self):
        blurred = doodle().filter(ImageFilter.GaussianBlur(6))
        report = analyse_sharpness(blurred)
        self.assertEqual(report.verdict, "blurry")
        self.assertLess(report.laplacian_variance, MIN_LAPLACIAN_VARIANCE)

    def test_blank_frame_is_skipped_rather_than_called_blurry(self):
        report = analyse_sharpness(Image.new("RGB", FRAME, "#FFFFFF"))
        self.assertEqual(report.verdict, "skipped")
        self.assertFalse(report.evaluated)
        self.assertLess(report.ink_coverage, 0.02)

    def test_flat_coloured_frame_is_called_featureless_not_blurry(self):
        """A solid colour has no detail to lose, so 'blurry' is the wrong word."""
        report = analyse_sharpness(Image.new("RGB", FRAME, "#FF0000"))
        self.assertEqual(report.verdict, "featureless")
        self.assertTrue(report.evaluated)

    def test_blocks_are_detected(self):
        """A step across every 8th column is what JPEG blocking looks like."""
        width, height = 256, 64
        image = Image.new("L", (width, height))
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                base = 100 + (x * 7 + y * 13) % 11
                if x % 8 == 7:
                    base += 60
                pixels[x, y] = min(255, base)

        report = analyse_blockiness(image.convert("RGB"))
        self.assertEqual(report.verdict, "suspect")
        self.assertGreater(report.ratio, 1.35)

    def test_smooth_gradient_is_not_flagged(self):
        width, height = 256, 64
        image = Image.new("L", (width, height))
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                pixels[x, y] = min(255, x)
        report = analyse_blockiness(image.convert("RGB"))
        self.assertEqual(report.verdict, "ok")

    def test_resolution_verdicts(self):
        small = compute_geometry((320, 180), FRAME, 1.0)
        big = compute_geometry((4000, 2250), FRAME, 1.0)
        self.assertEqual(
            analyse_resolution((320, 180), small).verdict, "upscaled"
        )
        self.assertEqual(analyse_resolution((4000, 2250), big).verdict, "ok")

    def test_colour_shift_is_per_channel(self):
        source = Image.new("RGB", (100, 100), "#336699")
        self.assertEqual(mean_colour(source), (51.0, 102.0, 153.0))
        shift = colour_shift((51.0, 102.0, 153.0), (61.0, 92.0, 153.0))
        self.assertAlmostEqual(shift[0], 10.0)
        self.assertAlmostEqual(shift[1], 10.0)
        self.assertAlmostEqual(shift[2], 0.0)


class FontTest(unittest.TestCase):

    def setUp(self) -> None:
        self.font = find_system_font()
        if self.font is None:
            self.skipTest("no system font available on this machine")

    def test_measure_grows_with_font_size(self):
        small = measure_text("Xin chào", self.font, 24)
        large = measure_text("Xin chào", self.font, 96)
        self.assertGreater(large[0], small[0])
        self.assertGreater(large[1], small[1])

    def test_stroke_widens_the_measurement(self):
        plain = measure_text("Xin chào", self.font, 64)
        stroked = measure_text("Xin chào", self.font, 64, stroke_width=4)
        self.assertGreater(stroked[0], plain[0])

    def test_largest_fitting_size_actually_fits(self):
        text = "MỘT DÒNG TIÊU ĐỀ RẤT DÀI CHO KHUNG HÌNH"
        size = largest_fitting_size(
            text, self.font, max_width=600, max_height=200, start_size=200
        )
        self.assertLessEqual(size, 200)
        width, height = measure_text(text, self.font, size)
        self.assertLessEqual(width, 600)
        self.assertLessEqual(height, 200)

    def test_missing_font_falls_back_to_a_system_font(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolution = resolve_font("assets/fonts/nope.ttf", Path(tmp))
        self.assertTrue(resolution.used_fallback)
        self.assertIsNotNone(resolution.path)


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------


def stage_script(
    scenes: list[dict], *, ken_burns: dict | None = None
) -> dict:
    motion = ken_burns or {
        "enabled": True,
        "type": "zoom_in",
        "start_scale": 1.0,
        "end_scale": 1.08,
    }
    built = []
    for index, extra in enumerate(scenes):
        scene = {
            "id": index + 1,
            "text": extra.get("text", "Câu một ngắn."),
            "image_file": extra.get("image_file", f"images/scene_{index + 1:03d}.png"),
            "ken_burns": dict(extra.get("ken_burns", motion)),
            "text_overlays": extra.get("text_overlays", []),
        }
        if "image_prompt" in extra:
            scene["image_prompt"] = extra["image_prompt"]
        if scene["image_file"] is None:
            del scene["image_file"]
        built.append(scene)

    return {
        "video_metadata": {
            "title": "Test",
            "author": "tester",
            "resolution": "1920x1080",
            "fps": 30,
            "language": "vi",
        },
        "tts_config": {"engine": "vieneu", "voice": "Thái Sơn", "speed": 1.0},
        "audio_config": {"background_music": None, "background_volume": 0.0},
        "pacing": {"auto_pause": {"enabled": True}},
        "scenes": built,
    }


class ImagesStageTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage3_")
        self.workspace = Path(self._tmp.name)
        self.font = find_system_font()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_script(self, data: dict) -> Path:
        path = self.workspace / "script.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def make_font(self, relative: str = "assets/fonts/handwriting.ttf") -> None:
        if self.font is None:
            self.skipTest("no system font available")
        target = self.workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.font.read_bytes())

    def run_stage(self, data: dict, **kwargs):
        self.write_script(data)
        from autovid.domain.script import load_script

        script = load_script(self.workspace / "script.json")
        paths = Paths.from_workspace(self.workspace)
        return ImagesStage(script, paths, **kwargs).run()

    def test_happy_path_prepares_every_frame(self):
        data = stage_script([{}, {}])
        for index in (1, 2):
            save(doodle(), self.workspace / f"images/scene_{index:03d}.png")

        result = self.run_stage(data)

        self.assertEqual(result.status, "pass")
        self.assertEqual(len(result.scenes), 2)
        for scene in result.scenes:
            self.assertTrue(scene.prepared.exists())
            with Image.open(scene.prepared) as prepared:
                self.assertEqual(
                    prepared.size, scene.geometry.prepared_size
                )
        report = json.loads(
            (self.workspace / "output/image_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["totals"]["prepared"], 2)

    def test_ken_burns_headroom_is_applied(self):
        data = stage_script(
            [{}],
            ken_burns={
                "enabled": True,
                "type": "pan_right",
                "start_scale": 1.10,
                "end_scale": 1.10,
            },
        )
        save(doodle(), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)
        geometry = result.scenes[0].geometry
        self.assertEqual(geometry.prepared_size, (2112, 1188))
        self.assertGreater(geometry.prepared_size[0], FRAME[0])

    def test_missing_image_is_an_error(self):
        data = stage_script([{}, {}])
        save(doodle(), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)

        self.assertEqual(result.status, "fail")
        codes = {issue.code for issue in result.issues}
        self.assertIn("image_missing", codes)
        self.assertIn("images_incomplete", codes)

    def test_scene_without_image_file_is_an_error(self):
        data = stage_script([{"image_file": None, "image_prompt": "a doodle"}])
        result = self.run_stage(data)
        codes = {issue.code for issue in result.issues}
        self.assertIn("image_not_provided", codes)
        self.assertEqual(result.status, "fail")

    def test_blurred_source_is_flagged(self):
        data = stage_script([{}])
        save(
            doodle().filter(ImageFilter.GaussianBlur(6)),
            self.workspace / "images/scene_001.png",
        )
        result = self.run_stage(data)
        codes = {issue.code for issue in result.issues}
        self.assertIn("image_blurry", codes)
        # Blur is a warning, not a blocker: the frame still gets prepared.
        self.assertEqual(result.status, "pass")

    def test_small_source_is_flagged_as_upscaled(self):
        data = stage_script([{}])
        save(doodle((320, 180)), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)
        codes = {issue.code for issue in result.issues}
        self.assertIn("image_upscaled", codes)
        self.assertGreater(result.scenes[0].resolution.scale, 1.5)

    def test_missing_font_falls_back_with_a_warning(self):
        self.make_font()
        data = stage_script(
            [
                {
                    "text_overlays": [
                        {
                            "text": "TIÊU ĐỀ",
                            "font": "assets/fonts/does_not_exist.ttf",
                            "font_size": 72,
                        }
                    ]
                }
            ]
        )
        save(doodle(), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)

        codes = {issue.code for issue in result.issues}
        self.assertIn("font_fallback", codes)
        overlay = result.scenes[0].overlays[0]
        self.assertTrue(overlay.font.used_fallback)
        # The measurement still happened, with the fallback font.
        self.assertGreater(overlay.measured_width, 0)

    def test_oversized_overlay_reports_a_size_that_fits(self):
        self.make_font()
        data = stage_script(
            [
                {
                    "text_overlays": [
                        {
                            "text": (
                                "MỘT TIÊU ĐỀ CỰC KỲ DÀI KHÔNG THỂ NÀO "
                                "VỪA VỚI KHUNG HÌNH NÀY"
                            ),
                            "font": "assets/fonts/handwriting.ttf",
                            "font_size": 400,
                        }
                    ]
                }
            ]
        )
        save(doodle(), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)

        codes = {issue.code for issue in result.issues}
        self.assertIn("overlay_overflow", codes)

        overlay = result.scenes[0].overlays[0]
        self.assertFalse(overlay.fits)
        self.assertLess(overlay.recommended_font_size, overlay.font_size)
        width, height = measure_text(
            overlay.text,
            overlay.font.path,
            overlay.recommended_font_size,
            stroke_width=overlay.stroke_width,
        )
        self.assertLessEqual(width, overlay.allowed_width)
        self.assertLessEqual(height, overlay.allowed_height)

    def test_overlay_is_checked_against_measured_narration(self):
        self.make_font()
        data = stage_script(
            [
                {
                    "text_overlays": [
                        {
                            "text": "CHÚ Ý",
                            "font": "assets/fonts/handwriting.ttf",
                            "font_size": 72,
                            "start_offset_ms": 0,
                            "end_offset_ms": 9000,
                        }
                    ]
                }
            ]
        )
        save(doodle(), self.workspace / "images/scene_001.png")

        output = self.workspace / "output"
        output.mkdir(parents=True, exist_ok=True)
        (output / "timeline.json").write_text(
            json.dumps({"scenes": [{"id": 1, "narration_s": 4.0}]}),
            encoding="utf-8",
        )

        result = self.run_stage(data)
        codes = {issue.code for issue in result.issues}
        self.assertIn("overlay_past_narration", codes)
        self.assertFalse(result.scenes[0].overlays[0].timing_ok)

    def test_timing_check_is_skipped_without_a_timeline(self):
        self.make_font()
        data = stage_script(
            [
                {
                    "text_overlays": [
                        {
                            "text": "CHÚ Ý",
                            "font": "assets/fonts/handwriting.ttf",
                            "font_size": 72,
                        }
                    ]
                }
            ]
        )
        save(doodle(), self.workspace / "images/scene_001.png")
        result = self.run_stage(data)
        self.assertIsNone(result.scenes[0].overlays[0].timing_ok)


class ImagesCliTest(ImagesStageTest):

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_cli_reports_success(self):
        self.make_font()
        data = stage_script(
            [
                {
                    "text_overlays": [
                        {
                            "text": "TIÊU ĐỀ",
                            "font": "assets/fonts/handwriting.ttf",
                            "font_size": 72,
                        }
                    ]
                }
            ]
        )
        self.write_script(data)
        save(doodle(), self.workspace / "images/scene_001.png")

        code, output = self.run_cli(
            ["images", str(self.workspace / "script.json"), "--quiet"]
        )
        self.assertEqual(code, 0, output)
        self.assertIn("frames ready", output)

        state = json.loads(
            (self.workspace / "output/state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["stages"]["images"]["status"], "pass")

    def test_cli_strict_promotes_warnings(self):
        data = stage_script([{}])
        self.write_script(data)
        save(doodle((320, 180)), self.workspace / "images/scene_001.png")

        lenient, _ = self.run_cli(
            ["images", str(self.workspace / "script.json"), "--quiet"]
        )
        strict, output = self.run_cli(
            ["images", str(self.workspace / "script.json"), "--quiet", "--strict"]
        )
        self.assertEqual(lenient, 0)
        self.assertEqual(strict, 1)
        self.assertIn("--strict is on", output)

    def test_cli_fails_when_an_image_is_missing(self):
        data = stage_script([{}])
        self.write_script(data)
        code, output = self.run_cli(
            ["images", str(self.workspace / "script.json"), "--quiet"]
        )
        self.assertEqual(code, 1)
        self.assertIn("image_missing", output)


if __name__ == "__main__":
    unittest.main()
