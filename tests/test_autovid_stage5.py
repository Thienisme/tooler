"""
Stage 5 tests: music bed, SFX placement and the summed mix.

The pure tests cover the arithmetic that decides *where* things land: scene
start times, absolute SFX offsets, the duration the mix has to be.  Those are
the numbers a wrong mix comes from.

The integration tests actually mix real audio with the project's bundled
ffmpeg and then measure the file that came out.  That is the only way to catch
the class of bug this stage is prone to: a filtergraph ffmpeg accepts happily
and that produces a perfectly valid mix in which the music is silent, the SFX
never appear, or the level correction undid the balance between the layers.

Run with:
    python -m unittest tests.test_autovid_stage5
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

from autovid.application.mix import (  # noqa: E402
    MIX_LEVEL_TOLERANCE_LUFS,
    MixStage,
)
from autovid.domain.script import parse_script  # noqa: E402
from autovid.infrastructure.audio.tools import measure_loudness  # noqa: E402
from autovid.infrastructure.ffmpeg import (  # noqa: E402
    ffmpeg_available,
    probe_duration,
    run,
)
from autovid.paths import Paths, read_json  # noqa: E402

FPS = 10
FRAME = (320, 180)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def script_dict(
    scenes: list[dict],
    *,
    background_music: str | None = "assets/audio/bg.wav",
    background_volume: float = 0.12,
    fade_in: float = 0.5,
    fade_out: float = 0.5,
    ducking: bool | None = None,
) -> dict:
    audio_config: dict = {
        "background_music": background_music,
        "background_volume": background_volume,
        "master_volume": -14.0,
        "fade_in_seconds": fade_in,
        "fade_out_seconds": fade_out,
    }
    if ducking is not None:
        audio_config["ducking"] = ducking

    return {
        "video_metadata": {
            "title": "Stage 5",
            "author": "tester",
            "resolution": f"{FRAME[0]}x{FRAME[1]}",
            "fps": FPS,
            "language": "vi",
        },
        "tts_config": {"engine": "vieneu", "voice": "Thái Sơn", "speed": 1.0},
        "audio_config": audio_config,
        "pacing": {"auto_pause": {"enabled": True}, "section_breaks": []},
        "scenes": scenes,
    }


def scene_dict(
    scene_id: int,
    *,
    narration_s: float = 2.0,
    pause_s: float = 0.2,
    sfx: list[dict] | None = None,
) -> dict:
    payload = {
        "id": scene_id,
        "text": f"Cảnh số {scene_id}.",
        "image_file": f"images/scene_{scene_id:03d}.png",
        "ken_burns": {"enabled": False, "type": "none"},
        "_narration_s": narration_s,
        "_pause_s": pause_s,
    }
    if sfx is not None:
        payload["sfx"] = sfx
    return payload


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
        "title": "Stage 5",
        "resolution": f"{FRAME[0]}x{FRAME[1]}",
        "fps": fps,
        "sample_rate": 48000,
        "channels": 2,
        "voiceover_file": "audio/voiceover_full.wav",
        "voiceover_duration_s": round(cursor, 4),
        "total_duration_s": round(cursor, 4),
        "scenes": entries,
    }


def make_tone(path: Path, *, seconds: float, frequency: int) -> Path:
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


def make_colour_video(path: Path, *, seconds: float, fps: int = FPS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s={FRAME[0]}x{FRAME[1]}:r={fps}",
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
class MixFixture(unittest.TestCase):
    """A workspace with real audio, a timeline and a rendered preview."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage5_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(
        self,
        scenes: list[dict],
        *,
        voice_seconds: float | None = None,
        video_seconds: float | None = None,
        music: bool = True,
        music_seconds: float = 3.0,
        music_volume: float = 0.12,
        fade_in: float = 0.5,
        fade_out: float = 0.5,
        ducking: bool | None = None,
        write_timeline: bool = True,
    ) -> tuple:
        payload = script_dict(
            scenes,
            background_music="assets/audio/bg.wav" if music else None,
            background_volume=music_volume,
            fade_in=fade_in,
            fade_out=fade_out,
            ducking=ducking,
        )
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
        make_tone(
            paths.voiceover_path,
            seconds=voice_seconds if voice_seconds is not None else total,
            frequency=440,
        )

        if music:
            make_tone(
                self.workspace / "assets" / "audio" / "bg.wav",
                seconds=music_seconds,
                frequency=196,
            )

        for scene in script.scenes:
            for index, sfx in enumerate(scene.sfx):
                make_tone(
                    self.workspace / sfx.file,
                    seconds=0.4,
                    frequency=900 + index,
                )

        make_colour_video(
            paths.preview_path,
            seconds=video_seconds if video_seconds is not None else total,
        )
        return script, paths


class TestMixStage(MixFixture):
    def test_mix_is_as_long_as_the_video_and_is_not_silent(self):
        script, paths = self.build([scene_dict(1), scene_dict(2)])

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertTrue(paths.mix_path.exists())

        # The video is the authority: audio is muxed into it, so a mix that
        # is not exactly its length would drift or clip the ending.
        video_s = probe_duration(paths.preview_path)
        self.assertAlmostEqual(probe_duration(paths.mix_path), video_s, places=2)

        loudness = measure_loudness(paths.mix_path)
        self.assertGreater(loudness.integrated_lufs, -40)

    def test_all_three_stems_are_built_when_the_script_asks_for_them(self):
        script, paths = self.build(
            [
                scene_dict(
                    1,
                    sfx=[{"file": "assets/sfx/whoosh.wav", "time_offset_ms": 500,
                          "volume": 0.6}],
                ),
                scene_dict(2),
            ],
            music=True,
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertTrue((paths.audio_dir / "stem_music.wav").exists())
        self.assertTrue((paths.audio_dir / "stem_sfx.wav").exists())
        self.assertEqual(result.report["totals"]["stems"], 3)
        self.assertTrue(result.report["totals"]["music_used"])
        self.assertTrue(result.report["totals"]["sfx_used"])

    def test_music_is_ducked_under_the_voice_by_default(self):
        """The bed should step back under the narration, not sit at a fixed
        level that has to be quiet enough for the loudest passage."""
        script, paths = self.build([scene_dict(1), scene_dict(2)], music=True)

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertTrue(result.report["settings"]["ducked"])
        self.assertTrue(result.report["totals"]["music_ducked"])

    def test_ducking_can_be_switched_off(self):
        script, paths = self.build(
            [scene_dict(1), scene_dict(2)], music=True, ducking=False
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertFalse(result.report["settings"]["ducked"])
        self.assertFalse(result.report["totals"]["music_ducked"])

    def test_sfx_lands_at_its_absolute_timeline_position(self):
        """
        A scene-relative offset placed against the scene's own start is the
        difference between a whoosh on the beat and a whoosh a scene late.
        """
        script, paths = self.build(
            [
                scene_dict(1, narration_s=2.0, pause_s=0.0),
                scene_dict(
                    2,
                    narration_s=2.0,
                    pause_s=0.0,
                    sfx=[{"file": "assets/sfx/boom.wav", "time_offset_ms": 800,
                          "volume": 0.7}],
                ),
            ]
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        placement = result.placements[0]
        # Scene 2 starts at 2.0s, so an 800ms offset is 2.8s into the mix.
        self.assertAlmostEqual(placement.start_s, 2.8, places=3)
        self.assertTrue(placement.placed)

    def test_skipping_music_leaves_the_sfx_and_voice_alone(self):
        script, paths = self.build(
            [
                scene_dict(
                    1,
                    sfx=[{"file": "assets/sfx/whoosh.wav", "time_offset_ms": 0,
                          "volume": 0.5}],
                )
            ]
        )

        result = MixStage(script, paths, skip_music=True).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertFalse(result.report["totals"]["music_used"])
        self.assertFalse((paths.audio_dir / "stem_music.wav").exists())
        self.assertEqual(result.report["totals"]["stems"], 2)

    def test_music_shorter_than_the_video_is_looped_and_reported(self):
        script, paths = self.build(
            [scene_dict(1, narration_s=3.0), scene_dict(2, narration_s=3.0)],
            music_seconds=2.0,
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        codes = {issue.code for issue in result.issues}
        self.assertIn("background_music_looped", codes)
        # Looping still has to fill the video, not stop at the track's end.
        self.assertAlmostEqual(
            probe_duration(paths.audio_dir / "stem_music.wav"),
            probe_duration(paths.preview_path),
            places=1,
        )

    def test_declared_but_missing_music_is_an_error(self):
        script, paths = self.build([scene_dict(1)], music=False)
        # Declared in the script, absent from the disk.
        payload = script_dict([scene_dict(1)], background_music="assets/audio/bg.wav")
        script = parse_script(payload)

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn(
            "background_music_missing", {issue.code for issue in result.issues}
        )

    def test_missing_voiceover_stops_the_stage(self):
        script, paths = self.build([scene_dict(1)])
        paths.voiceover_path.unlink()

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertEqual(result.mix, None)
        self.assertIn("voiceover_missing", {issue.code for issue in result.issues})

    def test_missing_timeline_stops_the_stage(self):
        script, paths = self.build([scene_dict(1)], write_timeline=False)

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn("timeline_missing", {issue.code for issue in result.issues})

    def test_sfx_past_the_end_of_the_video_is_dropped_not_placed(self):
        script, paths = self.build(
            [
                scene_dict(
                    1,
                    narration_s=1.0,
                    pause_s=0.0,
                    sfx=[{"file": "assets/sfx/late.wav", "time_offset_ms": 9000,
                          "volume": 0.5}],
                )
            ]
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(result.report["totals"]["sfx_dropped"], 1)
        self.assertIn(
            "sfx_outside_timeline", {issue.code for issue in result.issues}
        )

    def test_sfx_beyond_its_own_scene_still_lands_on_the_timeline(self):
        """
        Offsets are absolute, not per-scene windows: an SFX scripting a sound
        that outlives its own scene (a thunder roll under the next image) must
        land where the writer put it, not be clipped to the scene that owns
        it.
        """
        script, paths = self.build(
            [
                scene_dict(
                    1,
                    narration_s=1.0,
                    pause_s=0.2,
                    sfx=[{"file": "assets/sfx/whoosh.wav",
                          "time_offset_ms": 5000, "volume": 0.5}],
                ),
                scene_dict(2, narration_s=6.0, pause_s=0.0),
            ]
        )

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(result.report["totals"]["sfx_placed"], 1)

    def test_mix_length_is_corrected_to_the_video_not_the_audio(self):
        """
        Fixing the common failure: a master that is a few milliseconds longer
        than the picture.  If the mix followed the audio, the mux would either
        cut the video or leave silence at the end.
        """
        scenes = [scene_dict(1, narration_s=2.0), scene_dict(2, narration_s=2.0)]
        script, paths = self.build(scenes, voice_seconds=6.0)

        result = MixStage(script, paths).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertAlmostEqual(
            probe_duration(paths.mix_path),
            probe_duration(paths.preview_path),
            places=2,
        )

    def test_loudness_is_reported_against_the_script_target(self):
        script, paths = self.build([scene_dict(1), scene_dict(2)])

        result = MixStage(script, paths).run()

        totals = result.report["totals"]
        self.assertEqual(totals["loudness_target_lufs"], -14.0)
        self.assertLessEqual(
            abs(totals["loudness"]["integrated_lufs"] - (-14.0)),
            MIX_LEVEL_TOLERANCE_LUFS + 0.6,
        )

    def test_report_records_what_was_mixed_and_from_where(self):
        script, paths = self.build(
            [
                scene_dict(
                    1,
                    sfx=[{"file": "assets/sfx/whoosh.wav", "time_offset_ms": 100,
                          "volume": 0.5}],
                )
            ]
        )

        result = MixStage(script, paths).run()

        report = read_json(paths.output_dir / "mix_report.json")
        self.assertEqual(report["stage"], "mix")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["totals"]["target_source"], "preview_video")
        self.assertEqual(len(report["stems"]), 3)
        self.assertEqual(report["sfx"][0]["start_s"], 0.1)
        self.assertEqual(report["output"], str(paths.mix_path))


class TestMixWithoutFfmpeg(unittest.TestCase):
    """The stage must refuse rather than half-run when ffmpeg is gone."""

    def test_reports_a_missing_toolchain(self):
        import autovid.application.mix as mix_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            payload = script_dict([scene_dict(1)], background_music=None)
            script = parse_script(payload)
            paths = Paths.from_workspace(workspace)
            paths.create()

            original = mix_module.ffmpeg_available
            mix_module.ffmpeg_available = lambda: False
            try:
                result = MixStage(script, paths).run()
            finally:
                mix_module.ffmpeg_available = original

        self.assertEqual(result.status, "fail")
        self.assertIn("ffmpeg_missing", {issue.code for issue in result.issues})
