"""
Stage 6 tests: muxing the picture and the sound into the deliverable.

The interesting failures here are all about *length*: audio that is shorter or
longer than the picture, a timeline that no longer matches the video, an
audio stream that never made it into the file.  So the tests measure the
output rather than trusting the command line that produced it.

Run with:
    python -m unittest tests.test_autovid_stage6
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovid.application.quality import build_quality_report  # noqa: E402
from autovid.application.render import RenderStage  # noqa: E402
from autovid.domain.script import parse_script  # noqa: E402
from autovid.infrastructure.ffmpeg import (  # noqa: E402
    ffmpeg_available,
    probe_duration,
    probe_streams,
    run,
)
from autovid.paths import Paths, read_json  # noqa: E402

FPS = 10
FRAME = (320, 180)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def script_dict(scenes: list[dict], *, resolution: str = "320x180") -> dict:
    return {
        "video_metadata": {
            "title": "Stage 6",
            "author": "tester",
            "resolution": resolution,
            "fps": FPS,
            "language": "vi",
        },
        "tts_config": {"engine": "vieneu", "voice": "Thái Sơn", "speed": 1.0},
        "audio_config": {
            "background_music": None,
            "background_volume": 0.0,
            "master_volume": -14.0,
            "fade_in_seconds": 0.0,
            "fade_out_seconds": 0.0,
        },
        "pacing": {"auto_pause": {"enabled": True}, "section_breaks": []},
        "scenes": scenes,
    }


def scene_dict(scene_id: int, *, narration_s: float = 2.0, pause_s: float = 0.2) -> dict:
    return {
        "id": scene_id,
        "text": f"Cảnh số {scene_id}.",
        "image_file": f"images/scene_{scene_id:03d}.png",
        "ken_burns": {"enabled": False, "type": "none"},
        "_narration_s": narration_s,
        "_pause_s": pause_s,
    }


def timeline_dict(scenes: list[dict], *, fps: int = FPS) -> dict:
    entries = []
    cursor = 0.0
    for index, scene in enumerate(scenes):
        narration_s = scene.get("_narration_s", 2.0)
        pause_s = scene.get("_pause_s", 0.2)
        entries.append(
            {
                "id": scene["id"],
                "index": index,
                "start_s": round(cursor, 4),
                "narration_s": narration_s,
                "pause_after_s": pause_s,
                "natural_tail_s": 0.0,
                "boundary_silence_s": pause_s,
                "end_s": round(cursor + narration_s + pause_s, 4),
                "audio_file": f"audio/scene_{scene['id']:03d}.wav",
                "unit_count": 1,
                "transition_in": {"type": "cut", "duration": 0.0},
                "ken_burns": {"enabled": False, "type": "none"},
            }
        )
        cursor += narration_s + pause_s

    return {
        "version": 1,
        "title": "Stage 6",
        "resolution": f"{FRAME[0]}x{FRAME[1]}",
        "fps": fps,
        "sample_rate": 48000,
        "channels": 2,
        "voiceover_file": "audio/voiceover_full.wav",
        "voiceover_duration_s": round(cursor, 4),
        "total_duration_s": round(cursor, 4),
        "scenes": entries,
    }


def make_tone(path: Path, *, seconds: float, frequency: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
            "-t",
            f"{seconds:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def make_colour_video(
    path: Path, *, seconds: float, fps: int = FPS, size: tuple[int, int] = FRAME
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s={size[0]}x{size[1]}:r={fps}",
            "-t",
            f"{seconds:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    return path


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is required")
class RenderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage6_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(
        self,
        scenes: list[dict],
        *,
        video_seconds: float | None = None,
        video_size: tuple[int, int] = FRAME,
        mix_seconds: float | None = None,
        voice_seconds: float | None = None,
        write_mix: bool = True,
        write_voice: bool = True,
        write_timeline: bool = True,
        resolution: str = "320x180",
    ) -> tuple:
        payload = script_dict(scenes, resolution=resolution)
        script = parse_script(payload)
        (self.workspace / "script.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        paths = Paths.from_workspace(self.workspace)
        paths.create()

        timeline = timeline_dict(scenes)
        if write_timeline:
            (paths.output_dir / "timeline.json").write_text(
                json.dumps(timeline, ensure_ascii=False), encoding="utf-8"
            )

        total = timeline["total_duration_s"]
        make_colour_video(
            paths.preview_path,
            seconds=video_seconds if video_seconds is not None else total,
            size=video_size,
        )
        if write_mix:
            make_tone(
                paths.mix_path,
                seconds=mix_seconds if mix_seconds is not None else total,
                frequency=440,
            )
        if write_voice:
            make_tone(
                paths.voiceover_path,
                seconds=voice_seconds if voice_seconds is not None else total,
                frequency=330,
            )
        return script, paths


class TestRenderStage(RenderFixture):
    def test_deliverable_carries_both_streams_and_keeps_the_video_length(self):
        script, paths = self.build([scene_dict(1), scene_dict(2)])

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertTrue(paths.final_video_path.exists())

        streams = probe_streams(paths.final_video_path)["streams"]
        types = {stream["codec_type"] for stream in streams}
        self.assertEqual(types, {"video", "audio"})

        video_s = probe_duration(paths.preview_path)
        self.assertAlmostEqual(
            probe_duration(paths.final_video_path), video_s, places=2
        )

    def test_video_is_copied_rather_than_re_encoded(self):
        """One lossy generation of the frames, not two."""
        script, paths = self.build([scene_dict(1)])

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertFalse(result.report["deliverable"]["video_reencoded"])
        self.assertTrue(result.report["deliverable"]["has_audio"])

    def test_audio_stream_is_aac_stereo_48k(self):
        script, paths = self.build([scene_dict(1)])

        RenderStage(script, paths).run()

        streams = probe_streams(paths.final_video_path)["streams"]
        audio = next(
            stream for stream in streams if stream["codec_type"] == "audio"
        )
        self.assertEqual(audio["codec_name"], "aac")
        self.assertEqual(audio["channels"], 2)
        self.assertEqual(audio["sample_rate"], "48000")

    def test_missing_preview_stops_the_stage(self):
        script, paths = self.build([scene_dict(1)])
        paths.preview_path.unlink()

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIsNone(result.output)
        self.assertIn("preview_missing", {issue.code for issue in result.issues})

    def test_no_audio_at_all_stops_the_stage(self):
        script, paths = self.build(
            [scene_dict(1)], write_mix=False, write_voice=False
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn("audio_missing", {issue.code for issue in result.issues})

    def test_missing_mix_falls_back_to_the_voiceover_but_says_so(self):
        script, paths = self.build(
            [scene_dict(1)], write_mix=False, write_voice=True
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn("audio_not_mixed", {issue.code for issue in result.issues})
        self.assertEqual(
            result.report["inputs"]["audio"], str(paths.voiceover_path)
        )

    def test_voice_only_flag_skips_the_mix(self):
        script, paths = self.build([scene_dict(1)])

        result = RenderStage(script, paths, use_voiceover=True).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn("voice_only_render", {issue.code for issue in result.issues})
        self.assertEqual(
            result.report["inputs"]["audio"], str(paths.voiceover_path)
        )

    def test_audio_shorter_than_the_video_is_padded_not_cut(self):
        """
        The video is the timeline.  Ending early would cut the episode's last
        image off; the picture must survive and the tail go quiet.
        """
        script, paths = self.build(
            [scene_dict(1, narration_s=3.0), scene_dict(2, narration_s=3.0)],
            mix_seconds=2.0,
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn(
            "audio_shorter_than_video", {issue.code for issue in result.issues}
        )
        self.assertAlmostEqual(
            probe_duration(paths.final_video_path),
            probe_duration(paths.preview_path),
            places=2,
        )

    def test_audio_longer_than_the_video_is_trimmed_and_reported(self):
        script, paths = self.build(
            [scene_dict(1, narration_s=1.0, pause_s=0.0)],
            mix_seconds=5.0,
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn(
            "audio_longer_than_video", {issue.code for issue in result.issues}
        )
        self.assertAlmostEqual(
            probe_duration(paths.final_video_path),
            probe_duration(paths.preview_path),
            places=2,
        )

    def test_wrong_resolution_in_the_preview_is_an_error(self):
        script, paths = self.build(
            [scene_dict(1)], video_size=(640, 360), resolution="320x180"
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn("render_wrong_size", {issue.code for issue in result.issues})

    def test_timeline_mismatch_is_reported(self):
        script, paths = self.build(
            [scene_dict(1, narration_s=4.0, pause_s=0.0)], video_seconds=2.0
        )
        timeline = json.loads(
            (paths.output_dir / "timeline.json").read_text(encoding="utf-8")
        )
        timeline["total_duration_s"] = 9.0
        (paths.output_dir / "timeline.json").write_text(
            json.dumps(timeline, ensure_ascii=False), encoding="utf-8"
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn(
            "video_length_mismatch", {issue.code for issue in result.issues}
        )

    def test_dry_run_encodes_nothing(self):
        script, paths = self.build([scene_dict(1)])

        result = RenderStage(script, paths, dry_run=True).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIsNone(result.output)
        self.assertFalse(paths.final_video_path.exists())
        self.assertIsNone(result.report["deliverable"])

    def test_report_describes_the_file_that_was_written(self):
        script, paths = self.build([scene_dict(1)])

        RenderStage(script, paths).run()

        report = read_json(paths.output_dir / "render_report.json")
        self.assertEqual(report["stage"], "render")
        self.assertEqual(report["status"], "pass")
        deliverable = report["deliverable"]
        self.assertEqual(deliverable["resolution"], "320x180")
        self.assertEqual(deliverable["fps"], FPS)
        self.assertEqual(deliverable["audio_codec"], "aac")
        self.assertGreater(deliverable["size_mb"], 0)

    def test_quality_report_is_written_and_merges_the_earlier_stages(self):
        script, paths = self.build([scene_dict(1)])
        (paths.output_dir / "tts_report.json").write_text(
            json.dumps(
                {
                    "stage": "tts",
                    "status": "pass",
                    "warnings": [
                        {"code": "unit_internal_silence", "severity": "warning",
                         "message": "one silence inside a unit"}
                    ],
                    "errors": [],
                }
            ),
            encoding="utf-8",
        )

        result = RenderStage(script, paths).run()

        quality = result.quality
        self.assertEqual(quality["stage"], "quality")
        staged = {entry["stage"] for entry in quality["stages"]}
        self.assertIn("tts", staged)
        self.assertIn("render", staged)
        # A stage that never ran is named, rather than silently assumed fine.
        self.assertIn("validate", quality["stages_not_run"])
        self.assertIn(
            ("tts", "unit_internal_silence"),
            {(issue["stage"], issue["code"]) for issue in quality["issues"]},
        )
        self.assertTrue(paths.quality_report_path.exists())

    def test_failed_validation_makes_the_merged_report_fail(self):
        script, paths = self.build([scene_dict(1)])
        (paths.output_dir / "validation_report.json").write_text(
            json.dumps(
                {
                    "stage": "validate",
                    "status": "fail",
                    "warnings": [],
                    "errors": [
                        {"code": "image_missing", "severity": "error",
                         "message": "image not found"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = RenderStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(result.quality["status"], "fail")
        self.assertEqual(result.quality["totals"]["errors"], 1)

    def test_abort_leaves_a_report_behind_for_the_merged_report(self):
        script, paths = self.build([scene_dict(1)])
        paths.preview_path.unlink()

        RenderStage(script, paths).run()

        report = read_json(paths.output_dir / "render_report.json")
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "preview_missing", {issue["code"] for issue in report["errors"]}
        )


class TestRenderCli(RenderFixture):
    """The manifest and the merged report have to agree after a CLI run."""

    def test_cli_records_the_stage_and_the_merged_report_sees_it(self):
        import contextlib
        import io

        from autovid.presentation.cli import main

        script, paths = self.build([scene_dict(1)])
        # The CLI gates every stage on validation, which wants the artwork to
        # exist; only its presence is checked, not its contents.
        artwork = self.workspace / "images" / "scene_001.png"
        artwork.parent.mkdir(parents=True, exist_ok=True)
        artwork.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

        for name, stage in (
            ("validation_report.json", "validate"),
            ("tts_report.json", "tts"),
            ("image_report.json", "images"),
            ("assembly_report.json", "assembly"),
            ("mix_report.json", "mix"),
        ):
            (paths.output_dir / name).write_text(
                json.dumps(
                    {"stage": stage, "status": "pass", "warnings": [],
                     "errors": []}
                ),
                encoding="utf-8",
            )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(["render", str(self.workspace / "script.json"), "--quiet"])

        self.assertEqual(code, 0, buffer.getvalue())

        quality = read_json(paths.quality_report_path)
        self.assertEqual(quality["status"], "pass")
        # The manifest was written after the stage stopped; a report built
        # during the stage would still be missing this entry.
        self.assertEqual(quality["state"].get("render"), "pass")
        self.assertIn("render", [entry["stage"] for entry in quality["stages"]])


class TestQualityReport(unittest.TestCase):
    """The merge is derived data, so it is tested without rendering."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_quality_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_reports_are_named_not_invented(self):
        payload = script_dict([scene_dict(1)])
        script = parse_script(payload)
        paths = Paths.from_workspace(self.workspace)
        paths.create()

        quality = build_quality_report(script, paths)

        self.assertEqual(quality["status"], "fail")
        self.assertEqual(quality["stages"], [])
        self.assertIn("render", quality["stages_not_run"])
        self.assertEqual(quality["totals"]["required_stages_not_run"], 6)

    def test_status_is_pass_when_every_required_stage_passed(self):
        payload = script_dict([scene_dict(1)])
        script = parse_script(payload)
        paths = Paths.from_workspace(self.workspace)
        paths.create()

        for name in (
            "validation_report.json",
            "tts_report.json",
            "image_report.json",
            "assembly_report.json",
            "mix_report.json",
            "render_report.json",
        ):
            (paths.output_dir / name).write_text(
                json.dumps(
                    {"stage": name.split("_")[0], "status": "pass",
                     "warnings": [], "errors": []}
                ),
                encoding="utf-8",
            )

        quality = build_quality_report(script, paths)

        self.assertEqual(quality["status"], "pass")
        self.assertEqual(quality["totals"]["errors"], 0)
        self.assertEqual(quality["stages_not_run"], [])
        # Captions are optional by design, so their absence is not a reason
        # to hold an episode back.
        self.assertNotIn("captions", quality["stages_not_run"])
