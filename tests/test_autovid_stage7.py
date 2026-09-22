"""
Stage 7 tests: subtitles from measured timings, and attaching them.

The timing tests are the point of this stage.  The pipeline measured every
sentence when it synthesized it, so a cue boundary that does not land on the
voice is a bug in the conversion from those measurements to SRT, not a
tolerance to be widened.  The layout tests cover the other half: a line that
is too long or a cue that is on screen for a quarter of a second is unreadable
even when its timing is perfect.

Run with:
    python -m unittest tests.test_autovid_stage7
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

from autovid.application.captions import (  # noqa: E402
    MAX_LINES,
    MIN_CUE_SECONDS,
    CaptionsStage,
    format_timestamp,
    paginate,
    render_srt,
    wrap_lines,
    wrap_pages,
)
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
MAX_CHARS = 30


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def script_dict(scenes: list[dict]) -> dict:
    return {
        "video_metadata": {
            "title": "Stage 7",
            "author": "tester",
            "resolution": f"{FRAME[0]}x{FRAME[1]}",
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
        "scenes": [
            {key: value for key, value in scene.items() if not key.startswith("_")}
            for scene in scenes
        ],
    }


def scene_dict(
    scene_id: int,
    *,
    text: str | None = None,
    units: list[tuple[str, float]] | None = None,
    inter_pause_ms: int = 500,
    narration_s: float = 4.0,
    pause_s: float = 0.2,
) -> dict:
    """
    A scene whose text, measured unit durations and narration all agree.

    `units` carries what stage 2 measured; `narration_s` is what the timeline
    reports, which is the units plus the in-scene pauses.
    """
    units = units or [
        (f"Câu một của cảnh {scene_id}.", narration_s / 2),
        (f"Câu hai của cảnh {scene_id}.", narration_s / 2),
    ]
    payload = {
        "id": scene_id,
        "text": text or " ".join(unit[0] for unit in units),
        "image_file": f"images/scene_{scene_id:03d}.png",
        "ken_burns": {"enabled": False, "type": "none"},
        "_units": units,
        "_inter_pause_ms": inter_pause_ms,
        "_narration_s": narration_s,
        "_pause_s": pause_s,
    }
    return payload


def timeline_dict(scenes: list[dict], *, fps: int = FPS) -> dict:
    entries = []
    cursor = 0.0
    for index, scene in enumerate(scenes):
        narration_s = scene.get("_narration_s", 4.0)
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
                "unit_count": len(scene.get("_units") or []),
                "transition_in": {"type": "cut", "duration": 0.0},
                "ken_burns": {"enabled": False, "type": "none"},
            }
        )
        cursor += narration_s + pause_s

    return {
        "version": 1,
        "title": "Stage 7",
        "resolution": f"{FRAME[0]}x{FRAME[1]}",
        "fps": fps,
        "sample_rate": 48000,
        "channels": 2,
        "voiceover_file": "audio/voiceover_full.wav",
        "voiceover_duration_s": round(cursor, 4),
        "total_duration_s": round(cursor, 4),
        "scenes": entries,
    }


def tts_report_dict(scenes: list[dict]) -> dict:
    return {
        "stage": "tts",
        "status": "pass",
        "totals": {"scenes": len(scenes)},
        "scenes": [
            {
                "id": scene["id"],
                "index": index,
                "units": [
                    {"index": position, "text": text, "duration_s": duration}
                    for position, (text, duration) in enumerate(scene["_units"])
                ],
            }
            for index, scene in enumerate(scenes)
        ],
        "warnings": [],
        "errors": [],
    }


def pacing_report_dict(scenes: list[dict]) -> dict:
    scenes_payload = []
    for scene in scenes:
        units = scene["_units"]
        inter = scene.get("_inter_pause_ms", 500)
        sentences = [
            {
                "index": position,
                "text": text,
                "is_last_of_scene": position == len(units) - 1,
                "pause_ms": scene.get("_pause_s", 0.2) * 1000
                if position == len(units) - 1
                else inter,
            }
            for position, (text, _duration) in enumerate(units)
        ]
        scenes_payload.append({"id": scene["id"], "sentences": sentences})

    return {
        "settings": {},
        "total_scenes": len(scenes),
        "scenes": scenes_payload,
    }


def make_tone(path: Path, *, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
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


def make_final_video(path: Path, *, seconds: float, audio: bool = True) -> Path:
    """A stand-in for stage 6's output: picture, and sound if asked for."""
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s={FRAME[0]}x{FRAME[1]}:r={FPS}",
            *(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000",
                ]
                if audio
                else []
            ),
            "-t",
            f"{seconds:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            *(["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"] if audio else []),
            str(path),
        ]
    )
    return path


# --------------------------------------------------------------------------
# Text layout and SRT formatting (pure)
# --------------------------------------------------------------------------


class TestLayout(unittest.TestCase):
    def test_lines_break_at_words_and_never_overflow(self):
        text = "Nghe thì dễ, nhưng đến lúc bị hỏi thì chín phần mười đều đứng hình."

        lines = wrap_lines(text, 30)

        self.assertTrue(all(len(line) <= 30 for line in lines))
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_words_are_never_split_across_lines(self):
        lines = wrap_lines("mười một mười hai mười ba", 10)

        for line in lines:
            self.assertTrue(all(len(word) <= 10 for word in line.split()))

    def test_pages_hold_at_most_the_line_budget(self):
        pages = paginate(["a", "b", "c", "d", "e"], 2)

        self.assertEqual([len(page) for page in pages], [2, 2, 1])

    def test_a_long_sentence_becomes_several_readable_pages(self):
        """
        Both limits at once: every line fits the width, and every page holds
        no more lines than a viewer can read before the text changes.
        """
        text = (
            "Hồi cấp một cô giáo từng nói với tôi rằng tiếng Việt là một trò "
            "chơi chữ và tôi đã cười vì tưởng cô đùa cho đến khi tôi thử "
            "chơi thử một lần cho biết"
        )

        pages = wrap_pages(text, MAX_CHARS, MAX_LINES)

        self.assertGreater(len(pages), 1)
        for page in pages:
            lines = page.splitlines()
            self.assertLessEqual(len(lines), MAX_LINES)
            self.assertTrue(all(len(line) <= MAX_CHARS for line in lines))
        # Nothing was dropped to make the limits work.
        self.assertEqual(
            " ".join(" ".join(page.splitlines()) for page in pages).split(),
            text.split(),
        )

    def test_timestamps_use_the_srt_format(self):
        self.assertEqual(format_timestamp(0), "00:00:00,000")
        self.assertEqual(format_timestamp(1.5), "00:00:01,500")
        self.assertEqual(format_timestamp(3725.004), "01:02:05,004")
        self.assertEqual(format_timestamp(-3), "00:00:00,000")

    def test_srt_blocks_are_numbered_and_complete(self):
        from autovid.application.captions import Cue

        srt = render_srt(
            [
                Cue(index=1, start_s=0.0, end_s=1.5, text="Xin chào"),
                Cue(index=2, start_s=1.5, end_s=3.0, text="Hai\ndòng"),
            ]
        )

        blocks = srt.strip().split("\n\n")
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("1\n00:00:00,000 --> 00:00:01,500"))
        self.assertIn("Hai\ndòng", blocks[1])


# --------------------------------------------------------------------------
# Cue timing (integration with the pipeline's measurements)
# --------------------------------------------------------------------------


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is required")
class CaptionsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage7_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(
        self,
        scenes: list[dict],
        *,
        write_timeline: bool = True,
        write_tts: bool = True,
        write_pacing: bool = True,
        write_video: bool = True,
        video_audio: bool = True,
    ) -> tuple:
        payload = script_dict(scenes)
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
        if write_tts:
            (paths.output_dir / "tts_report.json").write_text(
                json.dumps(tts_report_dict(scenes), ensure_ascii=False),
                encoding="utf-8",
            )
        if write_pacing:
            (paths.output_dir / "pacing_report.json").write_text(
                json.dumps(pacing_report_dict(scenes), ensure_ascii=False),
                encoding="utf-8",
            )

        if write_video:
            make_final_video(
                paths.final_video_path,
                seconds=timeline["total_duration_s"],
                audio=video_audio,
            )
            make_tone(paths.mix_path, seconds=timeline["total_duration_s"])
            make_tone(
                paths.voiceover_path, seconds=timeline["total_duration_s"]
            )
        return script, paths


class TestCueTiming(CaptionsFixture):
    def test_cues_follow_the_measured_sentence_durations(self):
        """
        Scene 1 starts at 0s and holds two units of 1.5s and 2.0s with 500ms
        of pause between them, so the cues are [0, 1.5] and [2.0, 4.0].
        """
        scenes = [
            scene_dict(
                1,
                units=[("Câu một rất ngắn.", 1.5), ("Câu hai dài hơn một chút.", 2.0)],
                inter_pause_ms=500,
                narration_s=4.0,
            )
        ]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(len(result.cues), 2)
        self.assertAlmostEqual(result.cues[0].start_s, 0.0, places=3)
        self.assertAlmostEqual(result.cues[0].end_s, 1.5, places=3)
        self.assertAlmostEqual(result.cues[1].start_s, 2.0, places=3)
        self.assertAlmostEqual(result.cues[1].end_s, 4.0, places=3)

    def test_second_scene_cues_are_offset_by_the_timeline(self):
        scenes = [
            scene_dict(1, units=[("Một.", 2.0)], narration_s=2.0, pause_s=0.5),
            scene_dict(2, units=[("Hai.", 2.0)], narration_s=2.0),
        ]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertAlmostEqual(result.cues[1].start_s, 2.5, places=3)
        self.assertAlmostEqual(result.cues[1].end_s, 4.5, places=3)

    def test_missing_tts_report_falls_back_to_estimates_and_says_so(self):
        scenes = [
            scene_dict(
                1,
                units=[("Câu một của cảnh một.", 2.0), ("Câu hai của cảnh một.", 2.0)],
                narration_s=4.0,
            )
        ]
        script, paths = self.build(scenes, write_tts=False)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn(
            "captions_estimated_timing", {issue.code for issue in result.issues}
        )
        # The cues still tile the scene's narration rather than collapsing.
        self.assertAlmostEqual(result.cues[0].start_s, 0.0, places=3)
        self.assertAlmostEqual(result.cues[-1].end_s, 4.0, places=3)

    def test_a_very_short_sentence_is_reported_as_unreadable(self):
        scenes = [
            scene_dict(
                1,
                units=[("Vâng.", 0.3), ("Đây là câu còn lại của cảnh.", 1.7)],
                narration_s=2.0,
            )
        ]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn("cue_too_short", {issue.code for issue in result.issues})
        self.assertLess(result.cues[0].duration_s, MIN_CUE_SECONDS)

    def test_a_long_sentence_becomes_several_cues(self):
        long_text = (
            "Hồi cấp một cô giáo từng nói với tôi rằng tiếng Việt là một trò "
            "chơi chữ và tôi đã cười vì tưởng cô đùa"
        )
        scenes = [
            scene_dict(1, units=[(long_text, 8.0)], narration_s=8.0)
        ]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertGreater(len(result.cues), 1)
        for cue in result.cues:
            self.assertLessEqual(len(cue.lines), MAX_LINES)
            self.assertTrue(all(len(line) <= MAX_CHARS for line in cue.lines))
        # The durations still add up to the sentence's measured length.
        self.assertAlmostEqual(
            sum(cue.duration_s for cue in result.cues), 8.0, places=2
        )

    def test_missing_timeline_stops_the_stage(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes, write_timeline=False)

        result = CaptionsStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn(
            "captions_source_failed", {issue.code for issue in result.issues}
        )


class TestAttaching(CaptionsFixture):
    def test_soft_subtitles_are_muxed_and_the_picture_is_kept(self):
        scenes = [scene_dict(1), scene_dict(2)]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertTrue(paths.subtitles_path.exists())
        self.assertTrue(paths.subtitled_video_path.exists())

        streams = probe_streams(paths.subtitled_video_path)["streams"]
        types = [stream["codec_type"] for stream in streams]
        self.assertIn("subtitle", types)
        self.assertIn("audio", types)
        self.assertIn("video", types)
        self.assertAlmostEqual(
            probe_duration(paths.subtitled_video_path),
            probe_duration(paths.final_video_path),
            places=2,
        )

    def test_srt_is_the_file_the_report_describes(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        srt = paths.subtitles_path.read_text(encoding="utf-8")
        self.assertIn("-->", srt)
        self.assertTrue(srt.startswith("1\n"))

        report = read_json(paths.output_dir / "captions_report.json")
        self.assertEqual(report["stage"], "captions")
        self.assertEqual(report["settings"]["mode"], "soft")
        self.assertEqual(report["settings"]["source"], "timeline")
        self.assertEqual(report["totals"]["cues"], len(result.cues))
        self.assertEqual(report["subtitles"], str(paths.subtitles_path))

    def test_burn_in_re_encodes_the_picture_and_has_no_soft_track(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes)

        result = CaptionsStage(
            script, paths, burn=True, max_chars_per_line=MAX_CHARS
        ).run()

        self.assertEqual(result.status, "pass", result.issues)
        streams = probe_streams(paths.subtitled_video_path)["streams"]
        types = [stream["codec_type"] for stream in streams]
        self.assertNotIn("subtitle", types)
        self.assertIn("audio", types)
        self.assertAlmostEqual(
            probe_duration(paths.subtitled_video_path),
            probe_duration(paths.final_video_path),
            places=1,
        )

    def test_missing_rendered_video_stops_the_stage(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes, write_video=False)

        result = CaptionsStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn("render_missing", {issue.code for issue in result.issues})

    def test_asr_without_faster_whisper_fails_clearly(self):
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("faster-whisper is installed; the error path cannot run")

        scenes = [scene_dict(1)]
        script, paths = self.build(scenes)

        result = CaptionsStage(script, paths, asr=True).run()

        self.assertEqual(result.status, "fail")
        self.assertIn(
            "captions_source_failed", {issue.code for issue in result.issues}
        )

    def test_the_merged_quality_report_is_refreshed_with_captions(self):
        """
        The render stage wrote the merged report before captions existed; a
        report that still says the episode has no subtitles would be wrong.
        """
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes)
        (paths.output_dir / "quality_report.json").write_text(
            json.dumps({"stage": "quality", "status": "pass", "stages": []}),
            encoding="utf-8",
        )

        CaptionsStage(script, paths, max_chars_per_line=MAX_CHARS).run()

        quality = read_json(paths.quality_report_path)
        self.assertIn(
            "captions", [entry["stage"] for entry in quality["stages"]]
        )
