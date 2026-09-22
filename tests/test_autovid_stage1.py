"""
Stage 1 tests: script schema, sentence helpers and input validation.

Run with:
    python -m unittest tests.test_autovid_stage1
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

from autovid.application.validate import (  # noqa: E402
    ScriptValidator,
    estimate_total_seconds,
)
from autovid.domain.sentences import (  # noqa: E402
    ELLIPSIS,
    EXCLAMATION,
    NONE,
    PERIOD,
    QUESTION,
    classify_ending,
    split_sentences,
)
from autovid.domain.script import (  # noqa: E402
    ScriptSchemaError,
    load_script,
    parse_script,
)
from autovid.paths import Paths  # noqa: E402
from autovid.presentation.cli import build_parser, main  # noqa: E402

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def valid_script_dict(scene_count: int = 2) -> dict:
    """A minimal script that passes every check except runtime_target."""
    scenes = []
    for index in range(scene_count):
        scenes.append(
            {
                "id": index + 1,
                "text": "Đây là câu thứ nhất. Và đây là câu thứ hai?",
                "image_file": f"images/scene_{index + 1:03d}.png",
                "ken_burns": {
                    "enabled": True,
                    "type": "zoom_in",
                    "start_scale": 1.0,
                    "end_scale": 1.08,
                },
                "text_overlays": [
                    {
                        "text": "TIÊU ĐỀ",
                        "font": "assets/fonts/handwriting.ttf",
                        "font_size": 96,
                        "start_offset_ms": 500,
                        "end_offset_ms": 3500,
                    }
                ],
                "sfx": [],
            }
        )

    return {
        "video_metadata": {
            "title": "Quả là gì?",
            "author": "tester",
            "resolution": "1920x1080",
            "fps": 30,
            "language": "vi",
        },
        "tts_config": {"engine": "vieneu", "voice": "Thái Sơn", "speed": 1.0},
        "audio_config": {
            "background_music": "assets/audio/bg.mp3",
            "background_volume": 0.12,
            "master_volume": -14.0,
            "fade_in_seconds": 2.0,
            "fade_out_seconds": 3.0,
        },
        "pacing": {"auto_pause": {"enabled": True, "per_sentence": True}},
        "scenes": scenes,
    }


class WorkspaceFixture(unittest.TestCase):
    """Base class providing a real on-disk workspace."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_test_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, content: str = "x") -> Path:
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def materialise_assets(self, script: dict, *, skip: tuple[str, ...] = ()) -> None:
        """Create every file the script references."""
        self.write("assets/fonts/handwriting.ttf")
        self.write("assets/audio/bg.mp3")
        for scene in script["scenes"]:
            image = scene.get("image_file")
            if image and image not in skip:
                self.write(image)
            for sfx in scene.get("sfx", []):
                if sfx["file"] not in skip:
                    self.write(sfx["file"])

    def validate(self, script: dict, *, strict: bool = False):
        script_path = self.workspace / "script.json"
        script_path.write_text(
            json.dumps(script, ensure_ascii=False), encoding="utf-8"
        )
        parsed = load_script(script_path)
        paths = Paths.from_workspace(self.workspace)
        return ScriptValidator(parsed, paths).run(
            strict=strict, skip_engine_check=True
        )

    @staticmethod
    def codes(report) -> set[str]:
        return {issue.code for issue in report.issues}


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class SchemaTest(unittest.TestCase):

    def test_valid_script_parses(self):
        script = parse_script(valid_script_dict())
        self.assertEqual(script.scene_count, 2)
        self.assertEqual(script.video_metadata.width, 1920)
        self.assertEqual(script.video_metadata.height, 1080)
        self.assertEqual(script.tts_config.granularity, "sentence")
        self.assertTrue(script.pacing.auto_pause.per_sentence)

    def test_missing_required_key_is_reported(self):
        data = valid_script_dict()
        del data["video_metadata"]["fps"]
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(any("fps" in message for message in ctx.exception.errors))

    def test_all_errors_are_reported_at_once(self):
        data = valid_script_dict()
        data["video_metadata"]["resolution"] = "1920"  # bad format
        data["scenes"][0]["pause_after_ms"] = 99_999  # out of range
        data["scenes"][1]["ken_burns"]["end_scale"] = 3.0  # out of range
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertGreaterEqual(len(ctx.exception.errors), 3)

    def test_unknown_enum_value_is_rejected(self):
        data = valid_script_dict()
        data["scenes"][0]["ken_burns"]["type"] = "spiral"
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(
            any("spiral" in message for message in ctx.exception.errors)
        )

    def test_scene_id_must_be_positive(self):
        data = valid_script_dict()
        data["scenes"][0]["id"] = 0
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(
            any("scenes[0].id" in message for message in ctx.exception.errors)
        )

    def test_scene_needs_image_file_or_prompt(self):
        data = valid_script_dict()
        del data["scenes"][0]["image_file"]
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(
            any("image_file" in message for message in ctx.exception.errors)
        )

    def test_overlay_end_must_follow_start(self):
        data = valid_script_dict()
        overlay = data["scenes"][0]["text_overlays"][0]
        overlay["start_offset_ms"] = 4000
        overlay["end_offset_ms"] = 1000
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(
            any("end_offset_ms" in message for message in ctx.exception.errors)
        )

    def test_wrong_type_is_rejected(self):
        data = valid_script_dict()
        data["tts_config"]["speed"] = "fast"
        with self.assertRaises(ScriptSchemaError) as ctx:
            parse_script(data)
        self.assertTrue(
            any("tts_config.speed" in message for message in ctx.exception.errors)
        )

    def test_invalid_json_reports_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(ScriptSchemaError) as ctx:
                load_script(path)
            self.assertTrue(any("invalid JSON" in m for m in ctx.exception.errors))


class SentenceHelperTest(unittest.TestCase):

    def test_splits_on_sentence_endings(self):
        sentences = split_sentences(
            "Câu một. Câu hai? Câu ba! Câu bốn..."
        )
        self.assertEqual(
            sentences,
            ["Câu một.", "Câu hai?", "Câu ba!", "Câu bốn..."],
        )

    def test_keeps_decimal_numbers_together(self):
        self.assertEqual(split_sentences("Tỉ lệ là 1.5 lần."), ["Tỉ lệ là 1.5 lần."])

    def test_single_sentence_returns_one(self):
        self.assertEqual(
            split_sentences("Chỉ một câu thôi"), ["Chỉ một câu thôi"]
        )

    def test_empty_text_returns_nothing(self):
        self.assertEqual(split_sentences("   "), [])

    def test_classify_ending(self):
        self.assertEqual(classify_ending("Sao vậy?"), QUESTION)
        self.assertEqual(classify_ending("Gớm quá!"), EXCLAMATION)
        self.assertEqual(classify_ending("Và rồi..."), ELLIPSIS)
        self.assertEqual(classify_ending("Thì ra là vậy."), PERIOD)
        self.assertEqual(classify_ending("Không dấu"), NONE)

    def test_classify_ending_ignores_closing_quote(self):
        self.assertEqual(classify_ending('Anh ấy hỏi: "Sao vậy?"'), QUESTION)


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------


class ValidatorTest(WorkspaceFixture):

    def test_happy_path_passes(self):
        script = valid_script_dict()
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.errors, [])
        self.assertEqual(report.stats["scene_count"], 2)
        self.assertEqual(report.stats["images_found"], 2)

    def test_missing_image_is_an_error(self):
        script = valid_script_dict()
        self.materialise_assets(script, skip=("images/scene_002.png",))
        report = self.validate(script)
        self.assertEqual(report.status, "fail")
        self.assertIn("image_missing", self.codes(report))

    def test_missing_sfx_is_an_error(self):
        script = valid_script_dict()
        script["scenes"][0]["sfx"] = [
            {"file": "assets/sfx/nope.mp3", "time_offset_ms": 0, "volume": 0.5}
        ]
        self.materialise_assets(script, skip=("assets/sfx/nope.mp3",))
        report = self.validate(script)
        self.assertIn("sfx_missing", self.codes(report))
        self.assertEqual(report.status, "fail")

    def test_duplicate_scene_id_is_an_error(self):
        script = valid_script_dict()
        script["scenes"][1]["id"] = 1
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("scene_id_duplicate", self.codes(report))

    def test_pan_does_not_warn_about_equal_scale(self):
        """Regression: a pan moves the crop window, scale must stay equal."""
        script = valid_script_dict()
        script["scenes"][0]["ken_burns"] = {
            "enabled": True,
            "type": "pan_right",
            "start_scale": 1.10,
            "end_scale": 1.10,
        }
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertNotIn("ken_burns_no_motion", self.codes(report))
        self.assertNotIn("ken_burns_no_room_to_pan", self.codes(report))

    def test_pan_without_headroom_warns(self):
        script = valid_script_dict()
        script["scenes"][0]["ken_burns"] = {
            "enabled": True,
            "type": "pan_right",
            "start_scale": 1.0,
            "end_scale": 1.0,
        }
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("ken_burns_no_room_to_pan", self.codes(report))

    def test_zoom_without_scale_change_warns(self):
        script = valid_script_dict()
        script["scenes"][0]["ken_burns"]["end_scale"] = 1.0
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("ken_burns_no_motion", self.codes(report))

    def test_scene_ids_must_increase(self):
        script = valid_script_dict(3)
        script["scenes"][1]["id"] = 5
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("scene_id_not_increasing", self.codes(report))
        self.assertEqual(report.status, "fail")

    def test_section_break_out_of_range_is_an_error(self):
        script = valid_script_dict()
        script["pacing"]["section_breaks"] = [99]
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("section_break_out_of_range", self.codes(report))

    def test_custom_pause_for_unknown_scene_is_an_error(self):
        script = valid_script_dict()
        script["pacing"]["custom_pauses"] = {"42": 1200}
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("custom_pause_unknown_scene", self.codes(report))

    def test_overlay_longer_animation_than_visibility_is_an_error(self):
        script = valid_script_dict()
        overlay = script["scenes"][0]["text_overlays"][0]
        overlay["start_offset_ms"] = 0
        overlay["end_offset_ms"] = 200
        overlay["animation_duration_ms"] = 900
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("overlay_animation_too_long", self.codes(report))

    def test_short_overlay_warns(self):
        script = valid_script_dict()
        overlay = script["scenes"][0]["text_overlays"][0]
        overlay["start_offset_ms"] = 0
        overlay["end_offset_ms"] = 900
        overlay["animation_duration_ms"] = 200
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("overlay_too_short", self.codes(report))

    def test_missing_font_warns_with_fallback(self):
        script = valid_script_dict()
        self.materialise_assets(script)
        (self.workspace / "assets/fonts/handwriting.ttf").unlink()
        report = self.validate(script)
        self.assertIn("font_missing", self.codes(report))
        self.assertEqual(report.status, "pass")

    def test_strict_promotes_warnings_to_failure(self):
        script = valid_script_dict()
        self.materialise_assets(script)
        lenient = self.validate(script, strict=False)
        strict = self.validate(script, strict=True)
        self.assertTrue(lenient.warnings)
        self.assertEqual(lenient.status, "pass")
        self.assertEqual(strict.status, "fail")
        self.assertIn("strict_warnings", self.codes(strict))

    def test_dense_sfx_warns(self):
        script = valid_script_dict()
        script["scenes"][0]["sfx"] = [
            {"file": "assets/sfx/whoosh.mp3", "time_offset_ms": 0, "volume": 0.8},
            {"file": "assets/sfx/whoosh.mp3", "time_offset_ms": 1000, "volume": 0.8},
        ]
        self.materialise_assets(script)
        report = self.validate(script)
        self.assertIn("sfx_too_dense", self.codes(report))

    def test_scene_with_prompt_only_warns(self):
        script = valid_script_dict()
        self.materialise_assets(script)
        scene = script["scenes"][0]
        del scene["image_file"]
        scene["image_prompt"] = "A doodle explaining the answer."
        report = self.validate(script)
        self.assertIn("image_not_generated", self.codes(report))
        self.assertEqual(report.stats["images_to_generate"], 1)

    def test_estimate_grows_with_text(self):
        short = parse_script(valid_script_dict(1))
        long_data = valid_script_dict(1)
        long_data["scenes"][0]["text"] = long_data["scenes"][0]["text"] * 10
        longer = parse_script(long_data)
        self.assertGreater(
            estimate_total_seconds(longer), estimate_total_seconds(short)
        )

    def test_voice_catalog_comes_from_the_installed_engine(self):
        """
        The engine's shipped catalog is authoritative, so a voice added in a
        newer SDK is not reported as unknown just because the story
        pipeline's static map predates it.
        """
        from autovid.application.validate import _engine_voice_names

        names = _engine_voice_names()
        if not names:
            self.skipTest("the VieNeu engine is not installed here")

        self.assertEqual(ScriptValidator._known_voices(), names)
        try:
            from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
                VIENEU_VOICES,
            )
        except Exception:
            return
        # The legacy list must be a subset: anything the old pipeline used is
        # still a valid preset, and everything else is a genuine addition.
        self.assertEqual(set(VIENEU_VOICES.values()) - names, set())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class CliTest(WorkspaceFixture):

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_validate_writes_reports_and_state(self):
        script = valid_script_dict()
        self.materialise_assets(script)
        script_path = self.workspace / "script.json"
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        code, output = self.run_cli(
            ["validate", str(script_path), "--skip-engine-check"]
        )

        self.assertEqual(code, 0, output)
        self.assertIn("validation passed", output)
        self.assertTrue((self.workspace / "output/validation_report.json").exists())
        self.assertTrue((self.workspace / "output/config_validated.json").exists())

        state = json.loads(
            (self.workspace / "output/state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["stages"]["validate"]["status"], "pass")

    def test_validate_fails_on_broken_assets(self):
        script = valid_script_dict()
        self.materialise_assets(script, skip=("images/scene_001.png",))
        script_path = self.workspace / "script.json"
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        code, output = self.run_cli(
            ["validate", str(script_path), "--skip-engine-check", "--no-write"]
        )

        self.assertEqual(code, 1)
        self.assertIn("image_missing", output)

    def test_schema_error_exits_cleanly(self):
        script_path = self.workspace / "script.json"
        script_path.write_text('{"scenes": []}', encoding="utf-8")

        code, output = self.run_cli(["validate", str(script_path), "--no-write"])

        self.assertEqual(code, 1)
        self.assertIn("script.json is invalid", output)

    def test_a_missing_script_exits_two_for_every_stage(self):
        """Each stage refuses up front rather than half-running, and no stage
        is left as a stub that reports success without doing anything."""
        missing = str(self.workspace / "script.json")
        for stage in (
            "validate",
            "tts",
            "images",
            "assembly",
            "mix",
            "render",
            "captions",
        ):
            with self.subTest(stage=stage):
                code, output = self.run_cli([stage, missing])
                self.assertEqual(code, 2)
                self.assertIn("script not found", output)

    def test_help_lists_every_stage_as_implemented(self):
        # Read the parser's help directly: argparse exits the process for
        # --help, which is not what this test is about.
        output = build_parser().format_help()

        for stage in (
            "validate",
            "tts",
            "images",
            "assembly",
            "mix",
            "render",
            "captions",
        ):
            with self.subTest(stage=stage):
                self.assertIn(stage, output)
        self.assertNotIn("not implemented", output)


if __name__ == "__main__":
    unittest.main()
