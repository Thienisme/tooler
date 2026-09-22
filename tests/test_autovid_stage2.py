"""
Stage 2 tests: the auto-pause engine and the voiceover stage.

These exercise the real ffmpeg toolchain with `FakeTTSBackend`, which
produces placeholder audio of realistic length.  Durations, silence
measurements and the resulting timeline are therefore real numbers read
back off real files, not stubs — which is the only way to test the claim
that image changes land exactly on the narration.

Run with:
    python -m unittest tests.test_autovid_stage2
"""

from __future__ import annotations

import contextlib
import dataclasses
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

from autovid.application.tts import (  # noqa: E402
    TIMELINE_TOLERANCE_S,
    SynthesisFailed,
    TTSStage,
)
from autovid.application.validate import ScriptValidator  # noqa: E402
from autovid.domain.pacing import (  # noqa: E402
    FIRST_SCENE_MAX_MS,
    LAST_SCENE_MIN_MS,
    PacingEngine,
    build_pacing_result,
    semantic_bonus,
)
from autovid.domain.script import (  # noqa: E402
    AutoPauseConfig,
    KenBurns,
    Scene,
    TransitionIn,
    load_script,
    parse_script,
)
from autovid.infrastructure.ffmpeg import probe_duration  # noqa: E402
from autovid.infrastructure.tts.backend import FakeTTSBackend  # noqa: E402
from autovid.paths import Paths  # noqa: E402
from autovid.presentation.cli import main  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_scene(
    scene_id: int,
    text: str,
    *,
    pause_after_ms: int | None = None,
    overlays=(),
    sfx=(),
) -> Scene:
    return Scene(
        id=scene_id,
        text=text,
        image_file=f"images/scene_{scene_id:03d}.png",
        image_prompt=None,
        pause_after_ms=pause_after_ms,
        transition_in=TransitionIn("cut", 0.0),
        ken_burns=KenBurns(True, "zoom_in", 1.0, 1.08),
        text_overlays=tuple(overlays),
        sfx=tuple(sfx),
    )


def pacing_config(**overrides) -> AutoPauseConfig:
    values = {
        "enabled": True,
        "per_sentence": True,
        "min_pause_ms": 300,
        "max_pause_ms": 5000,
        "respect_tts_natural_pause": True,
    }
    values.update(overrides)
    return AutoPauseConfig(**values)


def script_dict(texts: list[str], **pacing_overrides) -> dict:
    scenes = []
    for index, text in enumerate(texts):
        scenes.append(
            {
                "id": index + 1,
                "text": text,
                "image_file": f"images/scene_{index + 1:03d}.png",
                "ken_burns": {
                    "enabled": True,
                    "type": "zoom_in",
                    "start_scale": 1.0,
                    "end_scale": 1.08,
                },
            }
        )

    pacing = {
        "auto_pause": {
            "enabled": True,
            "per_sentence": True,
            "min_pause_ms": 300,
            "max_pause_ms": 5000,
            "respect_tts_natural_pause": True,
        },
        "section_breaks": [],
        "custom_pauses": {},
    }
    pacing.update(pacing_overrides)

    return {
        "video_metadata": {
            "title": "Test",
            "author": "tester",
            "resolution": "1920x1080",
            "fps": 30,
            "language": "vi",
        },
        "tts_config": {
            "engine": "vieneu",
            "voice": "Thái Sơn",
            "speed": 1.0,
            "granularity": "sentence",
        },
        "audio_config": {
            "background_music": None,
            "background_volume": 0.0,
            "master_volume": -14.0,
            "fade_in_seconds": 0.0,
            "fade_out_seconds": 0.0,
        },
        "pacing": pacing,
        "scenes": scenes,
    }


class WorkspaceFixture(unittest.TestCase):
    """Provides a real workspace on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage2_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str) -> Path:
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return path

    def prepare(self, script: dict) -> Paths:
        """Write script.json plus every image it references."""
        self.workspace.joinpath("script.json").write_text(
            json.dumps(script, ensure_ascii=False), encoding="utf-8"
        )
        for scene in script["scenes"]:
            self.write(scene["image_file"])
        return Paths.from_workspace(self.workspace)

    def load(self) -> tuple:
        script = load_script(self.workspace / "script.json")
        return script, Paths.from_workspace(self.workspace)

    def run_stage(
        self,
        backend: FakeTTSBackend | None = None,
        *,
        force: bool = False,
        max_retries: int = 3,
    ):
        script, paths = self.load()
        backend = backend or FakeTTSBackend()
        return TTSStage(
            script,
            paths,
            backend,
            force=force,
            max_retries=max_retries,
        ).run()


# --------------------------------------------------------------------------
# Pacing engine
# --------------------------------------------------------------------------


class PacingEngineTest(unittest.TestCase):

    def engine(self, config: AutoPauseConfig, *, section_breaks=(), count=3):
        return PacingEngine(config, tuple(section_breaks), scene_count=count)

    def scene_pause(self, engine, scene, **kwargs):
        return engine.scene_pause(scene, index=kwargs.pop("index", 1), **kwargs)

    def test_punctuation_sets_the_base_pause(self):
        engine = self.engine(pacing_config(min_pause_ms=0, max_pause_ms=5000))
        expected = {
            "Câu bình thường.": 700,
            "Câu hỏi?": 1500,
            "Câu cảm thán!": 1000,
            "Bỏ lửng...": 2000,
        }
        for text, base in expected.items():
            with self.subTest(text=text):
                _, breakdown = self.scene_pause(
                    engine, make_scene(1, text), scene_audio_ms=10_000
                )
                self.assertEqual(breakdown.punctuation_ms, base)

        # A period-only sentence carries no context adjustment at all,
        # so its final pause equals its punctuation base.
        _, plain = self.scene_pause(
            engine, make_scene(1, "Câu bình thường."), scene_audio_ms=10_000
        )
        self.assertEqual(plain.final_ms, 700)

        # A question also earns the rhetorical-question bonus on top.
        _, question = self.scene_pause(
            engine, make_scene(1, "Câu hỏi?"), scene_audio_ms=10_000
        )
        self.assertEqual(question.final_ms, 1500 + 400)

    def test_most_specific_marker_wins(self):
        """`nhưng khoan đã` is suspense, not the twist marker `nhưng`."""
        self.assertEqual(semantic_bonus("Nhưng khoan đã, còn một chi tiết."),
                         ("suspense", 500))
        self.assertEqual(semantic_bonus("Nhưng điều đó là sai."),
                         ("twist", 800))

    def test_marker_must_start_the_sentence(self):
        self.assertEqual(semantic_bonus("Tôi nghĩ nhưng không nói."), ("none", 0))

    def test_markers_do_not_stack(self):
        engine = self.engine(pacing_config(min_pause_ms=0, max_pause_ms=5000))
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Nhưng tóm lại sự thật là như vậy."),
            scene_audio_ms=10_000,
        )
        # Only the twist bonus applies; conclusion and suspense are ignored.
        self.assertEqual(breakdown.semantic_ms, 800)
        self.assertEqual(breakdown.final_ms, 700 + 800)

    def test_clamped_to_configured_range(self):
        engine = self.engine(pacing_config(min_pause_ms=1000, max_pause_ms=1200))
        _, breakdown = self.scene_pause(
            engine, make_scene(1, "Ngắn."), scene_audio_ms=10_000, index=1
        )
        self.assertEqual(breakdown.final_ms, 1000)

    def test_first_scene_is_capped(self):
        engine = self.engine(pacing_config())
        _, breakdown = self.scene_pause(
            engine, make_scene(1, "Mở đầu dài dòng."), scene_audio_ms=10_000, index=0
        )
        self.assertLessEqual(breakdown.final_ms, FIRST_SCENE_MAX_MS)

    def test_last_scene_has_a_floor(self):
        engine = self.engine(pacing_config(), count=2)
        _, breakdown = self.scene_pause(
            engine, make_scene(2, "Kết thúc."), scene_audio_ms=10_000, index=1
        )
        self.assertGreaterEqual(breakdown.final_ms, LAST_SCENE_MIN_MS)

    def test_section_break_raises_the_floor(self):
        engine = self.engine(pacing_config(), section_breaks=(1,), count=3)
        _, breakdown = self.scene_pause(
            engine, make_scene(2, "Sang phần mới."), scene_audio_ms=10_000, index=1
        )
        self.assertGreaterEqual(breakdown.final_ms, 2500)

    def test_question_gets_extra_room(self):
        engine = self.engine(pacing_config(min_pause_ms=0), count=1)
        _, breakdown = self.scene_pause(
            engine, make_scene(1, "Vậy thì sao?"), scene_audio_ms=10_000, index=1
        )
        self.assertEqual(breakdown.final_ms, 1500 + 400)

    def test_sfx_running_past_the_scene_extends_the_pause(self):
        """An SFX still playing at the boundary must not be cut off."""
        engine = self.engine(pacing_config(), count=5)
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Cảnh này."),
            scene_audio_ms=8000,
            sfx_end_ms=9500,
            index=1,
        )
        self.assertGreaterEqual(breakdown.final_ms, 1500)

    def test_overlay_running_past_the_scene_extends_the_pause(self):
        engine = self.engine(pacing_config(), count=5)
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Cảnh này."),
            scene_audio_ms=8000,
            overlay_end_ms=9200,
            index=1,
        )
        self.assertGreaterEqual(breakdown.final_ms, 1200)

    def test_natural_silence_is_subtracted_not_doubled(self):
        engine = self.engine(pacing_config(min_pause_ms=0), count=5)
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Câu này."),
            natural_ms=900,
            scene_audio_ms=10_000,
            index=1,
        )
        # Natural silence is capped at the target: it cannot remove more
        # than the pause we were going to insert anyway.
        self.assertEqual(breakdown.natural_ms, -700)
        self.assertEqual(breakdown.final_ms, 0)

    def test_natural_silence_cannot_make_a_pause_negative(self):
        engine = self.engine(pacing_config(min_pause_ms=0), count=5)
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Câu này."),
            natural_ms=99_999,
            scene_audio_ms=10_000,
            index=1,
        )
        self.assertEqual(breakdown.final_ms, 0)

    def test_natural_silence_is_ignored_when_disabled(self):
        engine = self.engine(
            pacing_config(
                respect_tts_natural_pause=False, min_pause_ms=0, max_pause_ms=5000
            ),
            count=5,
        )
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Câu này."),
            natural_ms=900,
            scene_audio_ms=10_000,
            index=1,
        )
        self.assertEqual(breakdown.natural_ms, 0)
        self.assertEqual(breakdown.final_ms, 700)

    def test_explicit_override_is_honoured_exactly(self):
        engine = self.engine(pacing_config(), count=5)
        scene = make_scene(1, "Cảnh này.", pause_after_ms=3333)
        _, breakdown = self.scene_pause(
            engine, scene, scene_audio_ms=10_000, explicit_pause_ms=3333, index=1
        )
        self.assertEqual(breakdown.final_ms, 3333)

    def test_override_source_labels(self):
        engine = self.engine(pacing_config(), count=5)
        script_scene = make_scene(1, "A.", pause_after_ms=1000)
        self.assertEqual(engine.source_for(script_scene, 1000), "override_scene")
        self.assertEqual(engine.source_for(make_scene(1, "A."), 1000),
                         "override_custom")
        self.assertEqual(engine.source_for(make_scene(1, "A."), None), "auto")

    def test_disabled_auto_pause_zeroes_everything(self):
        engine = self.engine(pacing_config(enabled=False), count=3)
        scene = make_scene(1, "Câu một. Câu hai. Câu ba.")
        pacing = engine.compute(scene, index=1, scene_audio_ms=10_000)
        self.assertEqual(pacing.pause_ms, 0)
        self.assertTrue(all(item.pause_ms == 0 for item in pacing.sentences))
        self.assertEqual(pacing.source, "disabled")

    def test_disabled_auto_pause_keeps_an_override(self):
        engine = self.engine(pacing_config(enabled=False), count=3)
        scene = make_scene(1, "Câu một. Câu hai.", pause_after_ms=1500)
        pacing = engine.compute(
            scene, index=1, scene_audio_ms=10_000, explicit_pause_ms=1500
        )
        self.assertEqual(pacing.pause_ms, 1500)

    def test_only_the_last_sentence_carries_the_scene_pause(self):
        engine = self.engine(pacing_config(), count=3)
        scene = make_scene(1, "Câu một. Câu hai. Câu ba.")
        pacing = engine.compute(scene, index=1, scene_audio_ms=10_000)
        self.assertEqual(len(pacing.sentences), 3)
        self.assertFalse(pacing.sentences[0].is_last)
        self.assertFalse(pacing.sentences[1].is_last)
        self.assertTrue(pacing.sentences[-1].is_last)
        self.assertEqual(pacing.pause_ms, pacing.sentences[-1].pause_ms)

    def test_inter_sentence_pauses_skip_the_last_sentence(self):
        engine = self.engine(pacing_config(), count=3)
        scene = make_scene(1, "Câu một. Câu hai. Câu ba.")
        inter = engine.inter_sentence_pauses(scene, natural_trailing_ms=[0, 0, 0])
        self.assertEqual(len(inter), 2)
        self.assertTrue(all(not item.is_last for item in inter))

    def test_breakdown_adds_up(self):
        engine = self.engine(pacing_config(min_pause_ms=0, max_pause_ms=5000))
        _, breakdown = self.scene_pause(
            engine,
            make_scene(1, "Tuy nhiên, có một điều."),
            natural_ms=200,
            scene_audio_ms=10_000,
            index=1,
        )
        total = (
            breakdown.punctuation_ms
            + breakdown.semantic_ms
            + breakdown.context_ms
            + breakdown.natural_ms
        )
        self.assertEqual(total, breakdown.final_ms)

    def test_scene_pacing_reports_the_pause_it_actually_applies(self):
        """
        Regression: the scene-closing pause must be the value *after*
        natural silence is subtracted.  Reporting the raw target applied a
        double pause at every scene boundary in the rendered audio.
        """
        engine = self.engine(pacing_config(min_pause_ms=0), count=5)
        pacing = engine.compute(
            make_scene(1, "Câu này."),
            index=1,
            natural_trailing_ms=[250],
            scene_audio_ms=10_000,
        )

        self.assertEqual(pacing.pause_ms, pacing.breakdown.final_ms)
        self.assertEqual(pacing.pause_ms, 700 - 250)
        self.assertEqual(
            pacing.breakdown.punctuation_ms
            + pacing.breakdown.semantic_ms
            + pacing.breakdown.context_ms,
            pacing.breakdown.final_ms - pacing.breakdown.natural_ms,
        )

    def test_scene_target_equals_the_clamped_sum(self):
        engine = self.engine(pacing_config(min_pause_ms=1000), count=5)
        pacing = engine.compute(
            make_scene(1, "Ngắn."), index=1, scene_audio_ms=10_000
        )
        sentence = pacing.sentences[-1]
        self.assertEqual(
            sentence.target_ms,
            sentence.breakdown.punctuation_ms
            + sentence.breakdown.semantic_ms
            + sentence.breakdown.context_ms,
        )

    def test_two_sentence_pauses_sum_to_the_audible_gap(self):
        """Punctuation + the pause we insert equals the intended gap."""
        engine = self.engine(pacing_config(min_pause_ms=0), count=5)
        pacing = engine.compute(
            make_scene(1, "Câu này."),
            index=1,
            natural_trailing_ms=[250],
            scene_audio_ms=10_000,
        )
        audible_gap = 250 + pacing.pause_ms
        self.assertEqual(audible_gap, 700)

    def test_distribution_covers_every_pause(self):
        engine = self.engine(pacing_config(), count=3)
        scenes = [
            make_scene(1, "Câu một. Câu hai?"),
            make_scene(2, "Kết thúc."),
        ]
        pacings = [
            engine.compute(scene, index=index, scene_audio_ms=10_000)
            for index, scene in enumerate(scenes)
        ]
        result = build_pacing_result(pacings)
        total_counted = sum(result.distribution.values())
        expected = sum(len(pacing.sentences) for pacing in pacings)
        self.assertEqual(total_counted, expected)
        self.assertEqual(
            result.total_pause_ms,
            sum(
                sentence.pause_ms
                for pacing in pacings
                for sentence in pacing.sentences
            ),
        )


# --------------------------------------------------------------------------
# TTS stage
# --------------------------------------------------------------------------


class TTSStageTest(WorkspaceFixture):

    TEXTS = ["Câu mở đầu ngắn.", "Câu thứ hai dài hơn một chút."]

    def test_voiceover_is_built_and_reports_written(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()

        self.assertEqual(result.status, "pass")
        paths = Paths.from_workspace(self.workspace)
        self.assertTrue(paths.voiceover_path.exists())
        for index in range(len(self.TEXTS)):
            self.assertTrue(paths.scene_audio_path(index + 1).exists())
        for name in ("timeline.json", "tts_report.json", "pacing_report.json"):
            self.assertTrue((paths.output_dir / name).exists())

    def test_timeline_is_contiguous_and_matches_the_master(self):
        """The core sync guarantee: scenes tile the master exactly."""
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()

        master = probe_duration(Paths.from_workspace(self.workspace).voiceover_path)
        timeline = result.timeline

        self.assertAlmostEqual(
            timeline["total_duration_s"], master, delta=TIMELINE_TOLERANCE_S
        )

        cursor = 0.0
        for entry in timeline["scenes"]:
            self.assertAlmostEqual(entry["start_s"], cursor, places=3)
            self.assertAlmostEqual(
                entry["end_s"],
                entry["start_s"] + entry["narration_s"] + entry["pause_after_s"],
                places=3,
            )
            cursor = entry["end_s"]
        self.assertAlmostEqual(cursor, master, delta=TIMELINE_TOLERANCE_S)

    def test_scene_audio_duration_matches_the_timeline(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()
        for scene_result, entry in zip(result.scenes, result.timeline["scenes"]):
            measured = probe_duration(scene_result.audio_file)
            self.assertAlmostEqual(measured, entry["narration_s"], places=3)

    def test_parts_add_up_to_the_master(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()
        totals = result.report["totals"]
        self.assertAlmostEqual(
            totals["parts_total_s"],
            totals["scene_audio_s"] + totals["scene_pauses_s"],
            places=3,
        )
        self.assertLessEqual(abs(totals["master_delta_s"]), TIMELINE_TOLERANCE_S)

    def test_master_is_normalised_to_the_configured_loudness(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()
        loudness = result.report["totals"]["master_loudness"]
        self.assertAlmostEqual(loudness["integrated_lufs"], -14.0, delta=1.0)

    def test_second_run_reuses_every_unit(self):
        self.prepare(script_dict(self.TEXTS))
        first = self.run_stage()
        second = self.run_stage()

        self.assertEqual(first.report["totals"]["units_synthesized"],
                         first.report["totals"]["units"])
        self.assertEqual(second.report["totals"]["units_cached"],
                         second.report["totals"]["units"])
        self.assertEqual(second.report["totals"]["retries"], 0)

    def test_editing_one_sentence_invalidates_only_that_sentence(self):
        self.prepare(script_dict(self.TEXTS))
        self.run_stage()

        data = script_dict(self.TEXTS)
        data["scenes"][1]["text"] = "Câu thứ hai đã được sửa."
        self.workspace.joinpath("script.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        second = self.run_stage()
        self.assertEqual(second.report["totals"]["units"], 2)
        self.assertEqual(second.report["totals"]["units_synthesized"], 1)
        self.assertEqual(second.report["totals"]["units_cached"], 1)

    def test_force_resynthesizes_everything(self):
        self.prepare(script_dict(self.TEXTS))
        self.run_stage()
        forced = self.run_stage(force=True)
        self.assertEqual(forced.report["totals"]["units_cached"], 0)

    def test_retry_recovers_from_a_transient_failure(self):
        self.prepare(script_dict(self.TEXTS))
        backend = FakeTTSBackend(fail_attempts=2, fail_texts=set(self.TEXTS))
        result = self.run_stage(backend, max_retries=3)

        # Both sentences fail twice, so four retries happen before success.
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.report["totals"]["retries"], 4)
        codes = {issue.code for issue in result.issues}
        self.assertIn("synthesis_retry", codes)

    def test_retries_exhausted_raises(self):
        self.prepare(script_dict(self.TEXTS))
        backend = FakeTTSBackend(fail_attempts=99, fail_texts=set(self.TEXTS))
        with self.assertRaises(SynthesisFailed):
            self.run_stage(backend, max_retries=2)

    def test_natural_trailing_silence_suppresses_the_added_pause(self):
        """
        A 700ms target with 800ms of natural silence must add nothing: the
        model already paused for longer than the engine wanted.
        """
        self.prepare(script_dict(self.TEXTS))
        backend = FakeTTSBackend(trailing_silence_ms=800)
        result = self.run_stage(backend)

        for scene_result in result.scenes:
            for sentence in scene_result.pacing.sentences:
                if not sentence.is_last:
                    self.assertEqual(sentence.pause_ms, 0)
                    self.assertLess(sentence.breakdown.natural_ms, 0)

    def test_first_scene_pause_is_capped_in_the_timeline(self):
        self.prepare(
            script_dict(["Mở đầu ngắn gọn.", "Cảnh thứ hai ở đây."])
        )
        result = self.run_stage()
        self.assertLessEqual(
            result.scenes[0].boundary_silence_s,
            FIRST_SCENE_MAX_MS / 1000 + 0.05,
        )
        # The closing scene still gets its floor, since the floor wins.
        self.assertGreaterEqual(
            result.scenes[-1].boundary_silence_s,
            LAST_SCENE_MIN_MS / 1000 - 0.05,
        )

    def test_explicit_pause_reaches_the_timeline(self):
        """An override is the *total* boundary gap, natural tail included."""
        data = script_dict(self.TEXTS)
        data["scenes"][0]["pause_after_ms"] = 2500
        self.prepare(data)
        result = self.run_stage()
        self.assertAlmostEqual(
            result.scenes[0].boundary_silence_s, 2.5, places=2
        )
        self.assertEqual(result.scenes[0].pacing.source, "override_scene")

    def test_custom_pause_reaches_the_timeline(self):
        data = script_dict(self.TEXTS, custom_pauses={"2": 1800})
        self.prepare(data)
        result = self.run_stage()
        self.assertAlmostEqual(
            result.scenes[1].boundary_silence_s, 1.8, places=2
        )
        self.assertEqual(result.scenes[1].pacing.source, "override_custom")

    def test_scene_granularity_produces_one_unit_per_scene(self):
        data = script_dict(["Câu một. Câu hai. Câu ba."])
        data["tts_config"]["granularity"] = "scene"
        self.prepare(data)
        result = self.run_stage()

        self.assertEqual(len(result.scenes[0].units), 1)
        # No pauses inside the scene when there is only one unit.
        self.assertEqual(result.scenes[0].pacing.inter_sentence_pause_ms, 0)

    def test_timeline_carries_the_render_settings(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()
        timeline = result.timeline
        self.assertEqual(timeline["fps"], 30)
        self.assertEqual(timeline["resolution"], "1920x1080")
        self.assertEqual(
            timeline["scenes"][0]["ken_burns"]["type"], "zoom_in"
        )
        self.assertIn("transition_in", timeline["scenes"][0])

    def test_boundary_silence_in_the_master_matches_the_timeline(self):
        """
        End-to-end sync check: at each scene boundary the master must
        actually be silent, and the timeline's end_s must fall inside that
        silence rather than drifting into speech.
        """
        self.prepare(script_dict(self.TEXTS, custom_pauses={"1": 1500}))
        result = self.run_stage()

        from autovid.infrastructure.audio.tools import measure_silence

        paths = Paths.from_workspace(self.workspace)
        silence = measure_silence(
            paths.voiceover_path, threshold_db=-40.0, min_duration_s=0.2
        )

        first = result.timeline["scenes"][0]
        boundary = first["end_s"]
        inside = any(
            start - TIMELINE_TOLERANCE_S <= boundary <= end + TIMELINE_TOLERANCE_S
            for start, end in silence.internal
        )
        self.assertTrue(
            inside,
            f"no silence around {boundary:.3f}s; silences were "
            f"{[(round(s, 3), round(e, 3)) for s, e in silence.internal]}",
        )

    def test_pacing_report_reconciles_with_the_tts_report(self):
        self.prepare(script_dict(self.TEXTS))
        result = self.run_stage()
        paths = Paths.from_workspace(self.workspace)
        pacing = json.loads(
            (paths.output_dir / "pacing_report.json").read_text(encoding="utf-8")
        )
        totals = result.report["totals"]
        self.assertAlmostEqual(
            pacing["total_pause_ms"] / 1000.0,
            totals["inter_sentence_pauses_s"] + totals["scene_pauses_s"],
            places=3,
        )
        self.assertEqual(pacing["total_scenes"], len(self.TEXTS))

    def test_short_sentence_is_not_flagged_as_truncated(self):
        """Regression guard for the ratio heuristics firing on tiny text."""
        self.prepare(script_dict(["Ngắn."]))
        result = self.run_stage()
        codes = {issue.code for issue in result.issues}
        self.assertNotIn("unit_truncated", codes)

    def test_validate_still_passes_on_the_generated_workspace(self):
        self.prepare(script_dict(self.TEXTS))
        self.run_stage()
        script, paths = self.load()
        report = ScriptValidator(script, paths).run(skip_engine_check=True)
        self.assertEqual(report.errors, [])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class TTSCliTest(WorkspaceFixture):

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_tts_with_fake_backend_succeeds(self):
        self.prepare(script_dict(["Câu một.", "Câu hai."]))
        code, output = self.run_cli(
            ["tts", str(self.workspace / "script.json"), "--backend", "fake"]
        )
        self.assertEqual(code, 0, output)
        self.assertIn("voiceover ready", output)

        state = json.loads(
            (self.workspace / "output/state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["stages"]["tts"]["status"], "pass")

    def test_tts_is_gated_by_validation(self):
        script = script_dict(["Câu một."])
        self.prepare(script)
        (self.workspace / script["scenes"][0]["image_file"]).unlink()

        code, output = self.run_cli(
            ["tts", str(self.workspace / "script.json"), "--backend", "fake"]
        )
        self.assertEqual(code, 1)
        self.assertIn("validation failed", output)

    def test_voice_override_changes_the_cache_identity(self):
        self.prepare(script_dict(["Câu một."]))
        self.run_cli(["tts", str(self.workspace / "script.json"), "--backend", "fake"])
        code, output = self.run_cli(
            [
                "tts",
                str(self.workspace / "script.json"),
                "--backend",
                "fake",
                "--voice",
                "Mỹ Duyên",
            ]
        )
        self.assertEqual(code, 0, output)
        self.assertIn("1 synthesized", output)


if __name__ == "__main__":
    unittest.main()
