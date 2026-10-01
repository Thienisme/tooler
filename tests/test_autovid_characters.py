"""
Character tests: schema, cue timing, sprite baking, filtergraph, rendering.

Same split as the other stage tests, and for the same reason.

The *pure* tests cover the parts that can be reasoned about: how a preset is
expanded, where a cue anchored to "the third sentence" lands, what geometry a
baked sprite has, and what the filtergraph says.  The *expression* tests go
one step further and evaluate the generated ffmpeg expressions in Python,
because a motion expression is arithmetic: "does this drop land at rest, or
does it hover 228px above the ground for the rest of the cue" is answerable
without rendering anything -- and that exact bug happened while this was
being written.

The *render* tests encode real video and compare it against the same scene
rendered without characters.  Comparing against a reference is what makes
them robust: the question is never "is this pixel green", it is "did adding
a character change this frame, and where".  That is how a sprite that never
appears, a shrink that does nothing, or a handover that flickers actually
get caught.

Run with:
    python -m unittest tests.test_autovid_characters
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PIL import Image, ImageDraw  # noqa: E402

from autovid.domain.characters import (  # noqa: E402
    MIN_ON_SCREEN_S,
    character_sfx_cues,
    impact_offset_s,
    resolve_character_cues,
    sentence_windows,
)
from autovid.domain.frames import TRANSITION_XFADE_NAMES  # noqa: E402
from autovid.domain.script import (  # noqa: E402
    TRANSITION_TYPES,
    ScriptSchemaError,
    parse_script,
)
from autovid.infrastructure.ffmpeg import (  # noqa: E402
    FFMPEG,
    ffmpeg_available,
    probe_duration,
)
from autovid.infrastructure.video.filters import (  # noqa: E402
    ImpactPunch,
    OverlayLayer,
    build_scene_filtergraph,
)
from autovid.infrastructure.video.sprites import (  # noqa: E402
    FRAME_PAD,
    SPIN_PAD,
    CharacterLayer,
    SpriteFrame,
    SpritePlanner,
    bake_sprite,
    sprite_transparency,
)

FPS = 10
FRAME = (320, 180)

# The test sprite's body colour, chosen to be nowhere near the grey
# background the render tests use.
BODY = (20, 180, 90, 255)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def character_dict(**overrides) -> dict:
    payload = {
        "image_file": "assets/characters/host.png",
        "x": 0.3,
        "y": 0.95,
        "height": 0.5,
    }
    payload.update(overrides)
    return payload


def script_dict(scenes: list[dict], *, fps: int = FPS, resolution: str = "320x180"):
    return parse_script(
        {
            "video_metadata": {
                "title": "characters",
                "resolution": resolution,
                "fps": fps,
            },
            "tts_config": {"voice": "test", "speed": 1.0},
            "scenes": scenes,
        }
    )


def scene_dict(scene_id: int = 1, *, characters: list[dict] | None = None,
               impact: dict | None = None) -> dict:
    payload = {
        "id": scene_id,
        "text": "Câu một. Câu hai. Câu ba.",
        "image_file": "images/bg.png",
        "ken_burns": {"enabled": False, "type": "none"},
    }
    if characters:
        payload["characters"] = characters
    if impact is not None:
        payload["impact"] = impact
    return payload


def timeline_dict(*, scene_id: int = 1, duration_s: float = 8.0) -> dict:
    return {
        "scenes": [
            {
                "id": scene_id,
                "start_s": 0.0,
                "narration_s": duration_s,
                "pause_after_s": 0.0,
                "end_s": duration_s,
            }
        ],
        "total_duration_s": duration_s,
    }


def reports_dict(*, scene_id: int = 1, moments: list[tuple[float, float]],
                 pauses: list[int] | None = None) -> tuple[dict, dict]:
    """
    Stage-2 style reports for one scene.

    `moments` is (start, end) per sentence, which is what the cue resolver
    reconstructs from unit durations and pause lengths.
    """
    pauses = pauses or [500] * len(moments)
    units = [
        {"duration_s": round(end - start, 4)} for start, end in moments
    ]
    tts = {"scenes": [{"id": scene_id, "units": units}]}
    pacing = {
        "scenes": [
            {
                "id": scene_id,
                "sentences": [
                    {"index": index, "pause_ms": pause}
                    for index, pause in enumerate(pauses)
                ],
            }
        ]
    }
    return tts, pacing


def make_sprite(path: Path, *, margin: int = 0, size: tuple[int, int] = (60, 100)) -> Path:
    """A blocky figure, optionally with a transparent border around it."""
    width, height = size
    image = Image.new(
        "RGBA", (width + 2 * margin, height + 2 * margin), (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), fill=BODY
    )
    # An asymmetric mark, so a horizontal flip is detectable.
    draw.rectangle(
        (margin, margin, margin + 12, margin + 20), fill=(255, 0, 0, 255)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def make_background(path: Path) -> Path:
    """
    A soft gradient plate.

    Deliberately without a hard horizontal edge: a sharp edge is where x264
    spends bits, so two renders that differ only by a character end up with
    slightly different quantisation at that edge -- which would show up in
    the frame diff as a "changed" row and look like a character that will
    not leave.
    """
    width, height = FRAME
    image = Image.new("RGB", FRAME)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        blend = y / max(height - 1, 1)
        draw.line(
            (0, y, width, y),
            fill=(
                int(170 - 46 * blend),
                int(174 - 46 * blend),
                int(178 - 46 * blend),
            ),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


# --------------------------------------------------------------------------
# Expression evaluation: the generated motion maths, in Python
# --------------------------------------------------------------------------


def _if(condition, when_true, when_false):
    return when_true if condition else when_false


def _gt(a, b):
    return a > b


def _lt(a, b):
    return a < b


def evaluate(expression: str, *, t: float, on: float = 0.0) -> float:
    """
    Evaluate one generated ffmpeg expression.

    The expressions are the contract between the sprite planner and the
    renderer, and they are arithmetic; running them here is what turns
    "the drop lands at rest" from a hope into a test.  Only the functions
    this project actually emits are provided, so an expression that reaches
    for anything else fails loudly instead of silently.
    """
    translated = expression.replace("if(", "_if(")
    translated = translated.replace("gt(", "_gt(").replace("lt(", "_lt(")
    translated = translated.replace("PI", "math.pi")
    return float(
        eval(  # noqa: S307 - a fixed namespace, no builtins
            translated,
            {"__builtins__": {}},
            {
                "t": t,
                "on": on,
                "_if": _if,
                "_gt": _gt,
                "_lt": _lt,
                "min": min,
                "max": max,
                "abs": abs,
                "pow": pow,
                "sin": math.sin,
                "cos": math.cos,
                "exp": math.exp,
                "math": math,
            },
        )
    )


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class TestCharacterSchema(unittest.TestCase):
    def test_a_tilt_amplitude_is_read_as_degrees_and_capped(self):
        """`tilt` sways rotation, so its amplitude is degrees, capped hard."""
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            idle={"type": "tilt", "amplitude_px": 8,
                                  "period_s": 2.4},
                        )
                    ]
                )
            ]
        )
        character = script.scenes[0].characters[0]
        self.assertEqual(character.idle.type, "tilt")
        self.assertEqual(character.idle.amplitude_px, 8)

        with self.assertRaises(ScriptSchemaError):
            script_dict(
                [
                    scene_dict(
                        characters=[
                            character_dict(
                                idle={"type": "tilt", "amplitude_px": 60,
                                      "period_s": 2.4},
                            )
                        ]
                    )
                ]
            )

    def test_preset_fills_in_the_whole_bit(self):
        script = script_dict(
            [scene_dict(characters=[character_dict(preset="boing")])]
        )
        character = script.scenes[0].characters[0]

        self.assertEqual(character.enter.type, "drop_bounce")
        self.assertEqual(character.enter.edge, "top")
        self.assertEqual(character.exit.type, "shrink_out")
        self.assertEqual(character.idle.type, "bob_sway")
        self.assertTrue(character.sfx.endswith("boing.mp3"))

    def test_explicit_fields_beat_the_preset(self):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            preset="boing",
                            exit={"type": "fly_out", "to": "right",
                                  "duration_ms": 500},
                        )
                    ]
                )
            ]
        )
        character = script.scenes[0].characters[0]

        # The entrance still comes from the preset...
        self.assertEqual(character.enter.type, "drop_bounce")
        # ...while the author's own departure wins.
        self.assertEqual(character.exit.type, "fly_out")
        self.assertEqual(character.exit.edge, "right")
        self.assertEqual(character.exit.duration_ms, 500)

    def test_every_preset_parses(self):
        for preset in ("pop", "boing", "whoosh", "ta_da", "sneak", "ninja"):
            with self.subTest(preset=preset):
                script = script_dict(
                    [scene_dict(characters=[character_dict(preset=preset)])]
                )
                character = script.scenes[0].characters[0]
                self.assertEqual(character.preset, preset)

    def test_unknown_enter_type_is_rejected(self):
        with self.assertRaises(ScriptSchemaError) as caught:
            script_dict(
                [
                    scene_dict(
                        characters=[character_dict(enter={"type": "teleport"})]
                    )
                ]
            )
        self.assertIn("teleport", str(caught.exception))
        self.assertIn("drop_bounce", str(caught.exception))

    def test_height_out_of_range_is_rejected(self):
        with self.assertRaises(ScriptSchemaError):
            script_dict([scene_dict(characters=[character_dict(height=1.4)])])

    def test_character_needs_an_image(self):
        with self.assertRaises(ScriptSchemaError) as caught:
            script_dict(
                [scene_dict(characters=[{"preset": "boing", "x": 0.2}])]
            )
        self.assertIn("image_file", str(caught.exception))

    def test_impact_defaults_and_flash_validation(self):
        script = script_dict([scene_dict(impact={"at_sentence": 2})])
        impact = script.scenes[0].impact

        self.assertTrue(impact.enabled)
        self.assertEqual(impact.at_sentence, 2)
        self.assertEqual(impact.intensity, 0.08)
        self.assertEqual(impact.flash, "none")

        with self.assertRaises(ScriptSchemaError):
            script_dict([scene_dict(impact={"flash": "rainbow"})])

    def test_absent_impact_is_none(self):
        script = script_dict([scene_dict()])
        self.assertIsNone(script.scenes[0].impact)

    def test_new_transitions_are_accepted(self):
        for name in ("pixelize", "flash_white", "circle_open", "wipe_up",
                     "dissolve", "fade_fast"):
            with self.subTest(transition=name):
                script = script_dict(
                    [
                        scene_dict(scene_id=1),
                        {
                            **scene_dict(scene_id=2),
                            "transition_in": {"type": name, "duration": 0.4},
                        },
                    ]
                )
                self.assertEqual(script.scenes[1].transition_in.type, name)

    def test_every_transition_maps_to_a_real_xfade_name(self):
        """
        A transition name that ffmpeg does not know is rejected at render
        time, in the middle of a long run, which is the worst place to find
        out.  The mapping is the guard against that.
        """
        for name in TRANSITION_TYPES:
            if name == "cut":
                continue
            with self.subTest(transition=name):
                self.assertIn(name, TRANSITION_XFADE_NAMES)


# --------------------------------------------------------------------------
# Cue resolution
# --------------------------------------------------------------------------


class TestCueResolution(unittest.TestCase):
    def test_windows_come_from_unit_durations_and_pauses(self):
        tts, pacing = reports_dict(
            moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)],
            pauses=[500, 500, 500],
        )
        windows = sentence_windows(tts, pacing)[1]

        self.assertAlmostEqual(windows[0][0], 0.0)
        self.assertAlmostEqual(windows[1][0], 2.5)
        self.assertAlmostEqual(windows[2][0], 5.0)
        self.assertAlmostEqual(windows[2][1], 7.0)

    def test_sentence_anchor_lands_on_the_measured_sentence(self):
        tts, pacing = reports_dict(
            moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)]
        )
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            at_sentence=2, for_sentences=2, enter={"type": "none"}
                        )
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )
        cue = plan.for_scene(1)[0]

        self.assertEqual(cue.timing_source, "sentence")
        self.assertEqual(cue.sentence_index, 2)
        self.assertAlmostEqual(cue.start_s, 2.5)
        # Two sentences from the second one ends at the third's end.
        self.assertAlmostEqual(cue.end_s, 7.0)

    def test_without_reports_the_offset_is_used_and_reported(self):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            at_sentence=2, start_offset_ms=1500,
                            end_offset_ms=5000,
                        )
                    ]
                )
            ]
        )
        plan = resolve_character_cues(script, timeline=timeline_dict())
        cue = plan.for_scene(1)[0]

        self.assertEqual(cue.timing_source, "offset_fallback")
        self.assertAlmostEqual(cue.start_s, 1.5)
        self.assertAlmostEqual(cue.end_s, 5.0)
        self.assertIn(
            "character_timing_unmeasured",
            [warning["code"] for warning in plan.warnings],
        )

    def test_a_sentence_beyond_the_scene_falls_back_with_a_warning(self):
        tts, pacing = reports_dict(moments=[(0.0, 1.5)])
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(at_sentence=4, start_offset_ms=1000)
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )
        cue = plan.for_scene(1)[0]

        self.assertAlmostEqual(cue.start_s, 1.0)
        self.assertIn(
            "character_sentence_missing",
            [warning["code"] for warning in plan.warnings],
        )

    def test_a_cue_is_clamped_inside_its_clip(self):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(start_offset_ms=0, end_offset_ms=20000)
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script, timeline=timeline_dict(duration_s=6.0)
        )
        cue = plan.for_scene(1)[0]

        self.assertLessEqual(cue.end_s, 6.0 + 1e-9)

    def test_a_cue_with_no_room_still_gets_a_minimum_window(self):
        # A ten-millisecond window at the very end of a four-second clip.
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(start_offset_ms=3990,
                                       end_offset_ms=4000)
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script, timeline=timeline_dict(duration_s=4.0)
        )
        cue = plan.for_scene(1)[0]

        # The window is pushed inside the clip and widened to the minimum
        # rather than rendered as a zero-frame flash.
        self.assertLessEqual(cue.end_s, 4.0 + 1e-9)
        self.assertGreaterEqual(cue.duration_s, MIN_ON_SCREEN_S - 1e-9)

    def test_an_entrance_longer_than_its_window_is_shortened(self):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            start_offset_ms=0,
                            end_offset_ms=1000,
                            enter={"type": "fly_in", "duration_ms": 5000},
                        )
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script, timeline=timeline_dict(duration_s=10.0)
        )
        cue = plan.for_scene(1)[0]

        self.assertLess(cue.enter.duration_ms, 5000)
        self.assertIn(
            "character_motion_clamped",
            [warning["code"] for warning in plan.warnings],
        )

    def test_keyframes_are_reported_for_both_stages(self):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(at_sentence=1, start_offset_ms=0),
                        character_dict(
                            at_sentence=3,
                            enter={"type": "none"},
                            exit={"type": "none"},
                        ),
                    ]
                )
            ]
        )
        tts, pacing = reports_dict(moments=[(0.0, 2.0), (2.5, 4.0), (4.5, 6.0)])
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )

        self.assertEqual(plan.total, 2)
        self.assertEqual(plan.active, 2)
        self.assertEqual(len(plan.for_scene(1)), 2)

    def test_impact_anchors_to_a_sentence_when_given_one(self):
        windows = [(0.0, 2.0), (2.5, 4.0)]
        script = script_dict([scene_dict(impact={"at_sentence": 2})])
        impact = script.scenes[0].impact

        self.assertAlmostEqual(impact_offset_s(impact, windows=windows), 2.5)
        self.assertAlmostEqual(impact_offset_s(None, windows=windows), 0.0)

    def test_character_sfx_waits_for_a_landing_but_not_a_slide(self):
        tts, pacing = reports_dict(moments=[(0.0, 3.0)])
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            at_sentence=1,
                            enter={"type": "drop_bounce", "duration_ms": 800},
                            sfx={"file": "data/sfx/boing.mp3", "volume": 0.5},
                        )
                    ]
                )
            ]
        )
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )
        placements = character_sfx_cues(plan)

        self.assertEqual(len(placements), 1)
        # The landing is heard at the end of the fall, not at its start.
        self.assertAlmostEqual(placements[0]["offset_s"], 0.8)
        self.assertEqual(placements[0]["scene_id"], 1)


# --------------------------------------------------------------------------
# Sprite baking
# --------------------------------------------------------------------------


class TestSpriteBaking(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-sprite-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"

    def test_transparent_margin_is_trimmed_so_height_means_height(self):
        source = make_sprite(self.tmp / "host.png", margin=40)
        frame = bake_sprite(
            source=source,
            height_px=100,
            destination_dir=self.cache,
        )

        self.assertEqual(frame.content_h, 100)
        with Image.open(frame.path) as baked:
            self.assertEqual(baked.size, (frame.box_w, frame.box_h))
        # The box carries motion headroom, but not the sprite's own margin.
        self.assertLess(frame.box_h, int(frame.content_h * FRAME_PAD) + 2)

    def test_flip_mirrors_the_sprite(self):
        source = make_sprite(self.tmp / "host.png")
        normal = bake_sprite(
            source=source, height_px=40, destination_dir=self.cache
        )
        flipped = bake_sprite(
            source=source, height_px=40, flip=True, destination_dir=self.cache
        )

        with Image.open(normal.path) as first, Image.open(flipped.path) as second:
            self.assertNotEqual(
                first.convert("RGB").tobytes(), second.convert("RGB").tobytes()
            )

    def test_the_feet_stay_on_the_baseline_across_sizes(self):
        source = make_sprite(self.tmp / "host.png")
        small = bake_sprite(
            source=source, height_px=100, scale=0.3, destination_dir=self.cache
        )
        large = bake_sprite(
            source=source, height_px=100, scale=1.0, destination_dir=self.cache
        )

        self.assertEqual(small.feet_inset_px, small.box_h)
        self.assertEqual(large.feet_inset_px, large.box_h)

    def test_a_spin_reserves_room_for_its_diagonal(self):
        source = make_sprite(self.tmp / "host.png")
        plain = bake_sprite(
            source=source, height_px=100, destination_dir=self.cache
        )
        spinning = bake_sprite(
            source=source,
            height_px=100,
            spinning=True,
            destination_dir=self.cache,
        )

        self.assertGreater(spinning.box_w, plain.box_w)
        self.assertAlmostEqual(
            spinning.box_w / spinning.content_w, SPIN_PAD, places=1
        )

    def test_baking_is_cached(self):
        source = make_sprite(self.tmp / "host.png")
        first = bake_sprite(
            source=source, height_px=64, destination_dir=self.cache
        )
        before = first.path.stat().st_mtime_ns
        second = bake_sprite(
            source=source, height_px=64, destination_dir=self.cache
        )

        self.assertEqual(first.path, second.path)
        self.assertEqual(second.path.stat().st_mtime_ns, before)

    def test_transparency_detection(self):
        transparent = make_sprite(self.tmp / "host.png", margin=20)
        has_alpha, share = sprite_transparency(transparent)
        self.assertTrue(has_alpha)
        self.assertGreater(share, 0.1)

        opaque_path = self.tmp / "opaque.png"
        Image.new("RGB", (20, 20), (10, 20, 30)).save(opaque_path)
        has_alpha, share = sprite_transparency(opaque_path)
        self.assertFalse(has_alpha)

    def test_pop_bakes_a_stack_that_shares_one_box(self):
        source = make_sprite(self.tmp / "host.png")
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(
                            preset="pop", height=0.4, at_sentence=1
                        )
                    ]
                )
            ]
        )
        tts, pacing = reports_dict(moments=[(0.0, 4.0)])
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )
        layers = SpritePlanner(self.cache).plan(
            plan.for_scene(1)[0], source=source, frame_size=FRAME, fps=FPS
        )

        self.assertGreater(len(layers), 2)
        boxes = {(layer.frame.box_w, layer.frame.box_h) for layer in layers}
        self.assertEqual(len(boxes), 1, "every size step must share one box")
        heights = [layer.frame.content_h for layer in layers]
        self.assertLess(heights[0], heights[-1])
        # The last step of `pop` overshoots before it settles, which is what
        # makes it read as a cartoon rather than as a zoom.
        self.assertGreater(max(heights), heights[-1])


# --------------------------------------------------------------------------
# Filtergraph
# --------------------------------------------------------------------------


class TestCharacterGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-graph-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.source = make_sprite(self.tmp / "host.png")

    def layers_for(self, **overrides):
        script = script_dict(
            [
                scene_dict(
                    characters=[
                        character_dict(at_sentence=1, **overrides)
                    ]
                )
            ]
        )
        tts, pacing = reports_dict(moments=[(0.0, 4.0)])
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(),
            tts_report=tts,
            pacing_report=pacing,
        )
        return SpritePlanner(self.cache).plan(
            plan.for_scene(1)[0], source=self.source, frame_size=FRAME, fps=FPS
        )

    def test_a_sprite_is_gated_to_its_own_window(self):
        layers = self.layers_for()
        graph = build_scene_filtergraph(
            frames=40,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[],
            sprites=layers,
        )

        self.assertIn("[0:v]", graph)
        self.assertIn("[1:v]", graph)
        self.assertIn("enable='between(t,", graph)
        self.assertIn("[out]", graph)
        for line in graph.splitlines():
            for token in line.split(","):
                self.assertNotIn("]_", token, f"malformed label in: {line}")

    def test_the_position_comes_from_the_script(self):
        layers = self.layers_for(x=0.5, y=0.9, height=0.5)
        layer = layers[0]
        expected_x = int(round(0.5 * FRAME[0] - layer.box_w / 2))
        expected_y = int(round(0.9 * FRAME[1])) - layer.frame.feet_inset_px

        self.assertEqual(layer.box_x, expected_x)
        self.assertEqual(layer.box_y, expected_y)
        self.assertTrue(layer.x_expression().startswith(str(expected_x)))

    def test_a_spin_rotates_and_a_slide_does_not(self):
        spinning = self.layers_for(
            enter={"type": "spin_in", "from": "right", "duration_ms": 600},
            exit={"type": "none"},
        )
        graph = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS, motion=None, overlays=[],
            sprites=spinning,
        )
        self.assertIn("rotate=a=", graph)
        self.assertIn("c=none", graph)

        sliding = self.layers_for(
            enter={"type": "slide_in", "from": "left", "duration_ms": 600},
            exit={"type": "none"},
        )
        graph = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS, motion=None, overlays=[],
            sprites=sliding,
        )
        self.assertNotIn("rotate=", graph)

    def test_text_overlays_number_after_the_characters(self):
        layers = self.layers_for()
        graph = build_scene_filtergraph(
            frames=40,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[
                OverlayLayer(
                    path=Path("label.png"), start_s=0.0, end_s=4.0
                )
            ],
            sprites=layers,
        )

        # One sprite layer takes input 1, so the text layer is input 2.
        self.assertIn(f"[{1 + len(layers)}:v]", graph)
        self.assertIn("[ov0]", graph)
        self.assertIn("[v0]", graph)

    def test_a_flash_is_only_added_when_asked_for(self):
        layers = self.layers_for()
        plain = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS, motion=None, overlays=[],
            sprites=layers,
        )
        self.assertNotIn("[fl]", plain)

        flashing = build_scene_filtergraph(
            frames=40,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[],
            sprites=layers,
            impact=ImpactPunch(
                offset_s=1.0, intensity=0.08, shake_px=8, duration_s=0.4,
                flash="white",
            ),
        )
        self.assertIn("[fl]", flashing)
        self.assertIn("enable='between(t,1,1.4)'", flashing)
        self.assertIn("colorchannelmixer=aa=", flashing)

    def test_a_punch_in_moves_the_background_camera(self):
        base = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS,
            motion={"enabled": True, "type": "zoom_in",
                    "start_scale": 1.0, "end_scale": 1.05},
            overlays=[],
        )
        punched = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS,
            motion={"enabled": True, "type": "zoom_in",
                    "start_scale": 1.0, "end_scale": 1.05},
            overlays=[],
            impact=ImpactPunch(
                offset_s=1.0, intensity=0.08, shake_px=10, duration_s=0.4
            ),
        )
        self.assertNotEqual(base, punched)
        self.assertIn("exp(-(on-", punched)
        self.assertIn("sin(2*PI*(on-", punched)


# --------------------------------------------------------------------------
# Expressions: the motion maths, evaluated
# --------------------------------------------------------------------------


class TestMotionExpressions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-motion-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.source = make_sprite(self.tmp / "host.png")
        self.background = make_background(self.tmp / "bg.png")

    def layer_for(self, **overrides):
        script = script_dict(
            [scene_dict(characters=[character_dict(at_sentence=1, **overrides)])]
        )
        tts, pacing = reports_dict(moments=[(0.0, 6.0)])
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(duration_s=6.0),
            tts_report=tts,
            pacing_report=pacing,
        )
        return SpritePlanner(self.cache).plan(
            plan.for_scene(1)[0], source=self.source, frame_size=FRAME, fps=FPS
        )

    def test_a_drop_lands_at_rest_and_stays_there(self):
        """
        An unclamped fall ramp goes negative past the landing point, and
        squaring a negative brings it back up: the character then hovers
        above the ground line for the rest of the cue.  This is the test
        that caught it.
        """
        layers = self.layer_for(
            preset="boing", enter={"type": "drop_bounce", "duration_ms": 900},
            exit={"type": "none"},
        )
        # A `boing` cue idles, and the idle is *added* to the position, so
        # "at rest" means "at rest plus or minus the idle amplitude" rather
        # than exactly on the baseline.
        layer = layers[0]
        rest = layer.box_y
        amplitude = layer.idle.amplitude_px

        start = evaluate(layer.y_expression(), t=layer.start_s)
        self.assertLess(start, rest - 100, "the drop should start above frame")

        for moment in (layer.start_s + 1.2, layer.start_s + 3.0,
                       layer.start_s + 5.9):
            with self.subTest(t=moment):
                value = evaluate(layer.y_expression(), t=moment)
                self.assertLessEqual(value, rest + 1e-6, "the drop hovered")
                self.assertGreaterEqual(value, rest - amplitude - 1e-6)

        # Somewhere inside the entrance it bounces back up off the floor.
        hops = [
            evaluate(layer.y_expression(), t=layer.start_s + offset)
            for offset in (0.65, 0.7, 0.75, 0.8, 0.85)
        ]
        self.assertLess(min(hops), rest - 10, "no bounce was visible")

    def test_a_fly_in_starts_off_screen_and_settles(self):
        layers = self.layer_for(
            enter={"type": "fly_in", "from": "left", "duration_ms": 600},
            exit={"type": "none"},
        )
        layer = layers[0]

        start_x = evaluate(layer.x_expression(), t=layer.start_s)
        end_x = evaluate(layer.x_expression(), t=layer.start_s + 0.6)

        # Completely off the left edge, even with the idle sway at its
        # extreme, and settled on the script's x once it has arrived.
        self.assertLessEqual(start_x + layer.box_w, 0)
        settled = evaluate(layer.x_expression(), t=layer.start_s + 3.0)
        self.assertAlmostEqual(end_x, layer.box_x, delta=layer.idle.amplitude_px)
        self.assertAlmostEqual(
            settled, layer.box_x, delta=layer.idle.amplitude_px
        )

    def test_a_fly_out_leaves_the_frame(self):
        layers = self.layer_for(
            enter={"type": "none"},
            exit={"type": "fly_out", "to": "right", "duration_ms": 600},
        )
        layer = layers[-1]
        end_x = evaluate(layer.x_expression(), t=layer.end_s)

        self.assertGreaterEqual(end_x, FRAME[0])

    def test_a_bob_stays_inside_its_amplitude(self):
        layers = self.layer_for(
            enter={"type": "none"},
            exit={"type": "none"},
            idle={"type": "bob", "amplitude_px": 20, "period_s": 2.0},
        )
        layer = layers[0]
        offsets = [
            evaluate(layer.y_expression(), t=layer.start_s + step / 20)
            - layer.box_y
            for step in range(60)
        ]

        self.assertLessEqual(max(offsets), 1e-6)
        self.assertGreaterEqual(min(offsets), -20 - 1e-6)
        self.assertLess(
            min(offsets), -5, "the bob should actually move the character"
        )

    def test_a_tilt_rotates_but_never_moves_the_box(self):
        """A tilt owns rotation: the box stays put, the angle swings."""
        layers = self.layer_for(
            enter={"type": "none"},
            exit={"type": "none"},
            idle={"type": "tilt", "amplitude_px": 8, "period_s": 2.0},
        )
        layer = layers[0]

        # No positional idle at all: the placement expressions stay flat.
        self.assertEqual(layer.x_expression(), str(layer.box_x))
        self.assertEqual(layer.y_expression(), str(layer.box_y))

        # The angle oscillates, starts upright, peaks at the amplitude
        # (the 0.5 matches the bob: `1-cos` swings 0..2) and stays inside
        # it everywhere.
        angle = layer.angle_expression()
        self.assertIsNotNone(angle)
        values = [
            evaluate(angle, t=layer.start_s + step / 40)
            for step in range(80)
        ]
        peak = 8 * math.pi / 180
        self.assertAlmostEqual(values[0], 0.0, places=9)
        self.assertAlmostEqual(max(values), peak, delta=peak * 0.01)
        self.assertGreaterEqual(min(values), -1e-9)

        # And the graph carries the rotate with a transparent fill.
        graph = build_scene_filtergraph(
            frames=60, frame_size=FRAME, fps=FPS, motion=None,
            overlays=[], sprites=layers,
        )
        self.assertIn("rotate=a=", graph)
        self.assertIn("c=none", graph)

    def test_the_shrink_series_ends_tiny(self):
        layers = self.layer_for(
            enter={"type": "none"},
            exit={"type": "shrink_out", "duration_ms": 800},
        )
        self.assertGreater(len(layers), 2)
        self.assertLess(layers[-1].frame.content_h, layers[0].frame.content_h / 4)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")
class TestCharacterRender(unittest.TestCase):
    """
    Encode the same scene twice -- once with the character and once without
    it -- and compare frames.  The difference *is* the character.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-charrender-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.source = make_sprite(self.tmp / "host.png")
        self.background = make_background(self.tmp / "bg.png")
        self.frames = 20

    def render(self, destination: Path, *, layers, impact=None) -> Path:
        graph = build_scene_filtergraph(
            frames=self.frames,
            frame_size=FRAME,
            fps=FPS,
            motion={"enabled": True, "type": "zoom_in",
                    "start_scale": 1.0, "end_scale": 1.04},
            overlays=[],
            sprites=layers,
            impact=impact,
        )
        duration_s = self.frames / FPS
        inputs: list[str] = ["-i", str(self.background)]
        for layer in layers:
            inputs += [
                "-loop", "1", "-framerate", str(FPS), "-t", f"{duration_s}",
                "-i", str(layer.frame.path),
            ]
        if impact is not None and impact.flash != "none":
            inputs += [
                "-f", "lavfi", "-i",
                f"color=c=white:s={FRAME[0]}x{FRAME[1]}:r={FPS}:d=0.3",
            ]
        result = subprocess.run(
            [
                str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                *inputs,
                "-filter_complex", graph,
                "-map", "[out]",
                "-frames:v", str(self.frames),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", str(FPS), "-an",
                str(destination),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr.strip()[-600:])
        return destination

    def decode(self, video: Path, name: str) -> list[Path]:
        directory = self.tmp / name
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video), str(directory / "f%03d.png")],
            check=True,
        )
        return sorted(directory.glob("*.png"))

    def difference(self, reference: Path, candidate: Path) -> tuple[int, int, int]:
        """Changed pixels, and the box they occupy, against a reference frame."""
        with Image.open(reference) as first, Image.open(candidate) as second:
            a = first.convert("RGB").load()
            b = second.convert("RGB").load()
            width, height = first.size
            changed = 0
            xs: list[int] = []
            ys: list[int] = []
            for y in range(height):
                for x in range(width):
                    ra, ga, ba = a[x, y]
                    rb, gb, bb = b[x, y]
                    if abs(ra - rb) + abs(ga - gb) + abs(ba - bb) > 90:
                        changed += 1
                        xs.append(x)
                        ys.append(y)
            if not changed:
                return 0, 0, 0
            return changed, max(xs) - min(xs), max(ys) - min(ys)

    def plan_layers(self, **overrides):
        script = script_dict(
            [scene_dict(characters=[character_dict(**overrides)])]
        )
        tts, pacing = reports_dict(moments=[(0.0, 2.0)])
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(duration_s=2.0),
            tts_report=tts,
            pacing_report=pacing,
        )
        return SpritePlanner(self.cache).plan(
            plan.for_scene(1)[0], source=self.source, frame_size=FRAME, fps=FPS
        )

    def test_a_character_appears_and_disappears_on_its_cue(self):
        layers = self.plan_layers(
            at_sentence=1,
            start_offset_ms=400,
            end_offset_ms=1400,
            enter={"type": "fade_in", "duration_ms": 200},
            exit={"type": "shrink_out", "duration_ms": 400},
        )
        reference = self.decode(
            self.render(self.tmp / "reference.mp4", layers=[]), "ref"
        )
        candidate = self.decode(
            self.render(self.tmp / "with-character.mp4", layers=layers), "cmp"
        )

        # Frame 1 is t=0.1s: before the cue.
        changed, _, _ = self.difference(reference[0], candidate[0])
        self.assertEqual(changed, 0, "the character showed before its cue")

        # Frame 10 is t=1.0s: solidly inside the cue.
        changed, width, height = self.difference(reference[9], candidate[9])
        self.assertGreater(changed, 200, "the character never appeared")
        self.assertGreater(width, 20)
        self.assertGreater(height, 20)

        # The very last frame is past the end of the cue.  The tolerance is
        # encoder noise, not a character: at 320x180 and CRF 18 two renders
        # of the same plate differ by a few dozen pixels on their own.
        changed, _, _ = self.difference(reference[-1], candidate[-1])
        self.assertLess(changed, 400, "the character outstayed its cue")

    def test_a_shrink_really_shrinks(self):
        layers = self.plan_layers(
            at_sentence=1,
            start_offset_ms=0,
            end_offset_ms=2000,
            enter={"type": "none"},
            exit={"type": "shrink_out", "duration_ms": 1000},
        )
        reference = self.decode(
            self.render(self.tmp / "ref2.mp4", layers=[]), "ref2"
        )
        candidate = self.decode(
            self.render(self.tmp / "shrink.mp4", layers=layers), "shrink"
        )

        _, _, early_height = self.difference(reference[5], candidate[5])
        _, _, late_height = self.difference(reference[19], candidate[19])

        self.assertGreater(early_height, 60)
        self.assertLess(late_height, early_height, "the character never shrank")

    def test_a_punch_in_flashes_the_frame(self):
        layers = self.plan_layers(
            at_sentence=1, enter={"type": "none"}, exit={"type": "none"}
        )
        punch = ImpactPunch(
            offset_s=1.0, intensity=0.08, shake_px=10, duration_s=0.4,
            flash="white",
        )
        plain = self.decode(
            self.render(self.tmp / "plain.mp4", layers=layers), "plain"
        )
        flashed = self.decode(
            self.render(self.tmp / "flash.mp4", layers=layers, impact=punch),
            "flashed",
        )

        def brightness(path: Path) -> float:
            with Image.open(path) as image:
                grey = image.convert("L")
                return sum(grey.getdata()) / (grey.width * grey.height)

        at_impact = brightness(flashed[10])
        reference = brightness(plain[10])
        self.assertGreater(
            at_impact, reference + 40, "the punch-in did not flash the frame"
        )
        # ...and it is over by the end of the scene.
        self.assertLess(abs(brightness(flashed[-1]) - brightness(plain[-1])), 20)


class TestVariants(unittest.TestCase):
    """
    Pose and talk swaps: the schema, the resolved windows, the baked stack.

    The window maths is where this feature can quietly go wrong -- a pose
    landing outside its cue, a swap composited while the sprite is still
    mid-flight -- so those are the cases that are pinned here.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-variants-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.source = make_sprite(self.tmp / "host.png")
        self.talk_a = make_sprite(self.tmp / "talk_a.png", size=(60, 100))
        self.talk_b = make_sprite(self.tmp / "talk_b.png", size=(52, 100))
        self.pose = make_sprite(self.tmp / "pose.png", size=(60, 96))

    def variants_for(self, **overrides):
        character = character_dict(
            talk=[str(self.talk_a), str(self.talk_b)],
            auto_pose_s=1.0,
            poses=[
                {
                    "image_file": str(self.pose),
                    "from_sentence": 2,
                    "to_sentence": 3,
                }
            ],
            enter={"type": "none"},
            exit={"type": "none"},
        )
        character.update(overrides)
        script = script_dict([scene_dict(characters=[character])])
        # Three sentences of 2s each: windows (0,2), (2.5,4.5), (5,7).
        tts, pacing = reports_dict(
            moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)],
        )
        return resolve_character_cues(
            script,
            timeline=timeline_dict(duration_s=7.0),
            tts_report=tts,
            pacing_report=pacing,
        )

    def test_a_sentence_pose_fills_its_sentences(self):
        plan = self.variants_for()
        variants = plan.variants_for(1, 0)
        poses = [v for v in variants if v.kind == "pose"]

        self.assertEqual(len(poses), 1)
        # Sentences 2..3 run 2.5 -> 7.0: the pose covers exactly that.
        self.assertAlmostEqual(poses[0].start_s, 2.5, places=3)
        self.assertAlmostEqual(poses[0].end_s, 7.0, places=3)

    def test_auto_rotation_cycles_unanchored_poses(self):
        pose_b = make_sprite(self.tmp / "pose_b.png", size=(60, 96))
        plan = self.variants_for(
            poses=[
                {"image_file": str(self.pose)},
                {"image_file": str(pose_b)},
            ],
        )
        variants = plan.variants_for(1, 0)
        rotated = [v for v in variants if v.kind == "pose"]

        # Both unanchored poses appear, alternating, and never overlap.
        images = {v.image_file for v in rotated}
        self.assertEqual(len(images), 2)
        self.assertEqual(
            list(rotated), sorted(rotated, key=lambda v: v.start_s)
        )
        for first, second in zip(rotated, rotated[1:]):
            self.assertGreaterEqual(second.start_s, first.end_s - 1e-6)

    def test_talk_cycle_alternates_and_stays_inside_the_cue(self):
        plan = self.variants_for(auto_pose_s=None, poses=[])
        variants = plan.variants_for(1, 0)

        self.assertTrue(variants)
        self.assertTrue(all(v.kind == "talk" for v in variants))
        self.assertEqual(
            list(variants), sorted(variants, key=lambda v: v.start_s)
        )
        # One cadence step apart inside a sentence, never bunched...
        for first, second in zip(variants, variants[1:]):
            self.assertGreaterEqual(second.start_s, first.end_s - 1e-6)
            if abs(second.start_s - first.end_s) < 1e-6:
                self.assertAlmostEqual(
                    second.start_s - first.start_s, 0.32, delta=0.01
                )
        # ...and no swap inside the pauses: the resting look holds while
        # the narrator breathes.
        for variant in variants:
            self.assertGreaterEqual(variant.start_s, 0.0)
            self.assertLessEqual(variant.end_s, 7.0 + 1e-6)
            for pause_start, pause_end in ((2.0, 2.5), (4.5, 5.0)):
                overlaps = (
                    variant.start_s < pause_end - 1e-6
                    and variant.end_s > pause_start + 1e-6
                )
                self.assertFalse(overlaps, f"swap inside a pause: {variant}")

    def test_talk_cycle_keeps_flapping_without_reports(self):
        """Nothing measured: the whole cue speaks, exactly as it used to."""
        character = character_dict(
            talk=[str(self.talk_a), str(self.talk_b)],
            enter={"type": "none"},
            exit={"type": "none"},
        )
        script = script_dict([scene_dict(characters=[character])])
        plan = resolve_character_cues(
            script, timeline=timeline_dict(duration_s=7.0)
        )
        variants = plan.variants_for(1, 0)

        self.assertTrue(variants)
        # Contiguous across the whole cue: there are no measured pauses to
        # close on, so the cycle never stops.
        for first, second in zip(variants, variants[1:]):
            self.assertAlmostEqual(second.start_s, first.end_s, delta=1e-6)

    def test_swaps_never_fire_during_the_entrance(self):
        """A swap mid-flight would composite two characters at once."""
        plan = self.variants_for(
            enter={"type": "drop_bounce", "duration_ms": 900},
        )
        variants = plan.variants_for(1, 0)

        self.assertTrue(variants)
        for variant in variants:
            self.assertGreaterEqual(variant.start_s, 0.9 - 1e-6)

    def test_swaps_avoid_the_exit(self):
        plan = self.variants_for(
            exit={"type": "shrink_out", "duration_ms": 500},
        )
        variants = plan.variants_for(1, 0)

        self.assertTrue(variants)
        for variant in variants:
            self.assertLessEqual(variant.end_s, 7.0 - 0.5 + 1e-6)

    def test_a_pose_outside_its_cue_is_dropped(self):
        plan = self.variants_for(
            poses=[
                {
                    "image_file": str(self.pose),
                    "start_offset_ms": 6950,
                    "end_offset_ms": 6980,
                }
            ],
            talk=[],
            auto_pose_s=None,
        )
        variants = plan.variants_for(1, 0)
        self.assertEqual(variants, ())
        dropped = [
            w for w in plan.warnings if w["code"] == "character_pose_dropped"
        ]
        self.assertTrue(dropped)

    def test_the_parser_accepts_talk_and_poses(self):
        character = character_dict(talk=["assets/characters/missing.png"])
        script = script_dict([scene_dict(characters=[character])])
        # Parse succeeds; the validator catches the missing file.  Here we
        # pin that the parser itself accepts the structure.
        self.assertEqual(
            script.scenes[0].characters[0].talk_images,
            ("assets/characters/missing.png",),
        )

    def test_variant_layers_share_the_resting_box(self):
        plan = self.variants_for(auto_pose_s=None, poses=[])
        cue = plan.for_scene(1)[0]
        layers = SpritePlanner(self.cache).plan(
            cue,
            source=self.source,
            frame_size=FRAME,
            fps=FPS,
            variants=plan.variants_for(1, 0),
        )
        boxes = {(layer.frame.box_w, layer.frame.box_h) for layer in layers}
        self.assertEqual(len(boxes), 1, "a swap changed the composite geometry")

        # And the swap layers genuinely differ from the resting sprite.
        swap_files = {
            layer.frame.path
            for layer in layers
            if layer.travel == "none"
            and layer.fade_in_s == 0
            and layer.fade_out_s == 0
        }
        self.assertGreaterEqual(len(swap_files), 2)

    def test_the_base_sprite_goes_dark_while_a_pose_is_up(self):
        """
        A pose composites over the resting sprite, so a silhouette smaller
        than the resting art would leave the old pose ghosting around the
        new one.  The base layer is gated off for exactly the pose windows.
        """
        plan = self.variants_for(auto_pose_s=None, poses=[])
        cue = plan.for_scene(1)[0]
        layers = SpritePlanner(self.cache).plan(
            cue,
            source=self.source,
            frame_size=FRAME,
            fps=FPS,
            variants=plan.variants_for(1, 0),
        )
        base = layers[0]
        swaps = layers[1:]
        self.assertTrue(swaps)
        self.assertTrue(base.hide_windows, "the base never ducked")

        # Every talk window hides the base, and the windows stay inside the
        # base layer's own span.
        for swap in swaps:
            self.assertTrue(
                any(
                    hide_start <= swap.start_s + 1e-6
                    and swap.end_s <= hide_end + 1e-6
                    for hide_start, hide_end in base.hide_windows
                ),
                f"{swap.start_s}..{swap.end_s} not hidden",
            )

        graph = build_scene_filtergraph(
            frames=40, frame_size=FRAME, fps=FPS, motion=None,
            overlays=[], sprites=layers,
        )
        self.assertIn("*not(between(t,", graph)

    def test_a_wider_pose_widens_the_shared_box(self):
        """A pose wider than the resting art must not be sheared by it."""
        wide = make_sprite(self.tmp / "wide.png", size=(120, 100))
        plan = self.variants_for(
            auto_pose_s=None,
            poses=[{"image_file": str(wide), "from_sentence": 1,
                    "to_sentence": 1}],
        )
        cue = plan.for_scene(1)[0]
        layers = SpritePlanner(self.cache).plan(
            cue,
            source=self.source,
            frame_size=FRAME,
            fps=FPS,
            variants=plan.variants_for(1, 0),
        )
        plain = SpritePlanner(self.cache).plan(
            cue, source=self.source, frame_size=FRAME, fps=FPS
        )
        self.assertGreater(layers[0].frame.box_w, plain[0].frame.box_w)
        # Feet stay on the baseline: the height never moved.
        self.assertEqual(
            layers[0].frame.box_h, plain[0].frame.box_h
        )


class TestMouthPatch(unittest.TestCase):
    """
    The mouth flap: patches composited onto a still body.

    The whole point of the feature is that the body never moves while the
    mouth does, so the pinned property is that the baked mouth frames are
    identical everywhere except the face -- plus the cycle timing, which is
    the talk cycle's job taken over by a patch that cannot twitch a limb.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-mouth-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.body = make_sprite(self.tmp / "host.png")
        self.closed = self.tmp / "closed.png"
        self.open = self.tmp / "open.png"
        self.red = self.tmp / "red.png"
        self.blue = self.tmp / "blue.png"
        Image.new("RGB", (40, 20), (24, 24, 24)).save(self.closed)
        Image.new("RGB", (40, 20), (200, 60, 60)).save(self.open)
        Image.new("RGB", (40, 20), (200, 60, 60)).save(self.red)
        Image.new("RGB", (40, 20), (60, 60, 200)).save(self.blue)

    def mouth_character(self, **overrides):
        character = character_dict(
            mouth={
                "images": [str(self.closed), str(self.open)],
                "x": 0.5,
                "y": 0.25,
                "size": [0.3, 0.12],
                "period_s": 0.3,
            },
            enter={"type": "none"},
            exit={"type": "none"},
        )
        character.update(overrides)
        return character

    def resolved(self, character):
        script = script_dict([scene_dict(characters=[character])])
        tts, pacing = reports_dict(
            moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)]
        )
        return resolve_character_cues(
            script,
            timeline=timeline_dict(duration_s=7.0),
            tts_report=tts,
            pacing_report=pacing,
        )

    def test_the_parser_keeps_anchor_and_size(self):
        script = script_dict(
            [scene_dict(characters=[self.mouth_character()])]
        )
        mouth = script.scenes[0].characters[0].mouth
        self.assertIsNotNone(mouth)
        self.assertEqual(
            mouth.images, (str(self.closed), str(self.open))
        )
        self.assertEqual(mouth.x, 0.5)
        self.assertEqual(mouth.y, 0.25)
        self.assertEqual(mouth.size, (0.3, 0.12))
        self.assertAlmostEqual(mouth.period_s, 0.3)

    def test_a_mouth_without_images_is_rejected(self):
        with self.assertRaises(ScriptSchemaError):
            script_dict(
                [
                    scene_dict(
                        characters=[
                            self.mouth_character(
                                mouth={
                                    "images": [],
                                    "x": 0.5,
                                    "y": 0.25,
                                    "size": [0.3, 0.12],
                                }
                            )
                        ]
                    )
                ]
            )

    def test_a_mouth_size_out_of_range_is_rejected(self):
        with self.assertRaises(ScriptSchemaError):
            script_dict(
                [
                    scene_dict(
                        characters=[
                            self.mouth_character(
                                mouth={
                                    "images": [str(self.closed)],
                                    "x": 0.5,
                                    "y": 0.25,
                                    "size": [2.0, 0.1],
                                }
                            )
                        ]
                    )
                ]
            )

    def test_mouth_windows_cycle_on_their_period(self):
        plan = self.resolved(self.mouth_character())
        variants = plan.variants_for(1, 0)
        self.assertTrue(variants)
        self.assertTrue(all(v.kind == "mouth" for v in variants))
        self.assertEqual(
            list(variants), sorted(variants, key=lambda v: v.start_s)
        )
        # Within one sentence the patches cycle on their period; across a
        # pause the schedule closes the mouth instead, so only neighbours
        # inside the same speech run are one period apart.
        for first, second in zip(variants, variants[1:]):
            if first.silence or second.silence:
                continue
            self.assertAlmostEqual(
                second.start_s - first.start_s, 0.3, delta=0.01
            )
        # Both patches take a turn, and no body swap is scheduled at all.
        self.assertEqual(
            {v.mouth_file for v in variants},
            {str(self.closed), str(self.open)},
        )
        self.assertTrue(all(v.image_file == "" for v in variants))

    def test_a_mouth_and_a_single_pose_can_share_a_cue(self):
        """
        A sentence-anchored pose holds its window while the flap covers the
        speech around it -- the old rule dropped the pose entirely.
        """
        pose = self.tmp / "waving.png"
        make_sprite(pose)
        plan = self.resolved(
            self.mouth_character(
                poses=[{"image_file": str(pose), "from_sentence": 1,
                        "to_sentence": 1}],
            )
        )
        variants = plan.variants_for(1, 0)
        kinds = {v.kind for v in variants}
        self.assertIn("pose", kinds)
        self.assertIn("mouth", kinds)
        for variant in variants:
            for other in variants:
                if other is variant:
                    continue
                overlap = (
                    min(variant.end_s, other.end_s)
                    - max(variant.start_s, other.start_s)
                )
                self.assertLessEqual(overlap, 1e-6, "schedule overlapped")

    def test_the_mouth_closes_during_measured_pauses(self):
        """The flap follows the voice: a pause holds the closed patch."""
        plan = self.resolved(self.mouth_character())
        variants = plan.variants_for(1, 0)
        # Stage 2 measured the sentences (0,2), (2.5,4.5), (5,7), so the
        # pauses are (2.0,2.5) and (4.5,5.0).
        for pause_start, pause_end in ((2.0, 2.5), (4.5, 5.0)):
            inside = [
                variant
                for variant in variants
                if variant.start_s < pause_end - 1e-6
                and variant.end_s > pause_start + 1e-6
            ]
            self.assertTrue(inside, f"nothing scheduled in {pause_start}")
            for variant in inside:
                self.assertTrue(variant.silence)
                self.assertEqual(variant.mouth_file, str(self.closed))
        # ...and the flap really moves while the narrator speaks.
        self.assertTrue(any(not variant.silence for variant in variants))

    def test_the_schedule_covers_the_whole_cue(self):
        """A gap would let the resting artwork's own mouth show through."""
        plan = self.resolved(self.mouth_character())
        variants = plan.variants_for(1, 0)
        cursor = variants[0].start_s
        for variant in variants:
            self.assertAlmostEqual(variant.start_s, cursor, delta=1e-6)
            cursor = variant.end_s
        self.assertAlmostEqual(cursor, variants[-1].end_s)

    def test_a_micro_pause_does_not_close_the_mouth(self):
        """A 100ms gap is the narrator breathing, not a stop."""
        character = self.mouth_character()
        script = script_dict([scene_dict(characters=[character])])
        tts, pacing = reports_dict(
            moments=[(0.0, 2.0), (2.1, 4.0), (5.0, 7.0)],
            pauses=[100, 500],
        )
        plan = resolve_character_cues(
            script,
            timeline=timeline_dict(duration_s=7.0),
            tts_report=tts,
            pacing_report=pacing,
        )
        variants = plan.variants_for(1, 0)
        self.assertTrue(variants)
        for variant in variants:
            overlaps_micro = (
                variant.start_s < 2.1 - 1e-6 and variant.end_s > 2.0 + 1e-6
            )
            self.assertFalse(
                overlaps_micro and variant.silence,
                "the mouth closed for a 100ms breath",
            )

    def test_a_cue_parked_in_a_pause_keeps_its_mouth_shut(self):
        """No measured speech overlaps the cue: closed, nothing flaps."""
        plan = self.resolved(
            self.mouth_character(
                start_offset_ms=2000, end_offset_ms=2500
            )
        )
        variants = plan.variants_for(1, 0)

        self.assertEqual(len(variants), 1)
        self.assertTrue(variants[0].silence)
        self.assertEqual(variants[0].mouth_file, str(self.closed))

    def test_a_single_patch_still_schedules_silence(self):
        """One closed patch: the hold and the flap share the same image."""
        plan = self.resolved(
            self.mouth_character(mouth={
                "images": [str(self.closed)],
                "x": 0.5,
                "y": 0.25,
                "size": [0.3, 0.12],
                "period_s": 0.3,
            })
        )
        variants = plan.variants_for(1, 0)
        self.assertTrue(variants)
        self.assertTrue(
            all(variant.mouth_file == str(self.closed) for variant in variants)
        )
        self.assertTrue(any(variant.silence for variant in variants))

    def test_a_mouth_block_shares_the_cue_with_unanchored_poses(self):
        """
        The flap owns the speech; an unanchored pose pool takes the pauses.
        A talk list is not used beside a flap -- the patch already is the
        mouth -- so the only body swaps are the poses themselves.
        """
        plan = self.resolved(
            self.mouth_character(
                talk=[str(self.body)],
                poses=[str(self.body)],
                auto_pose_s=1.0,
            )
        )
        variants = plan.variants_for(1, 0)
        self.assertTrue(variants)
        self.assertEqual({v.kind for v in variants}, {"mouth", "pose"})

        # Stage 2 measured (0,2), (2.5,4.5), (5,7): every pose sits inside
        # a pause, never on top of a flapping slice.
        speech = [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)]
        for variant in variants:
            if variant.kind != "pose":
                continue
            middle = (variant.start_s + variant.end_s) / 2
            self.assertFalse(
                any(start <= middle < end for start, end in speech),
                f"pose {variant.start_s}..{variant.end_s} covered speech",
            )

    def test_mouth_layers_share_the_resting_geometry(self):
        plan = self.resolved(self.mouth_character())
        cue = plan.for_scene(1)[0]
        layers = SpritePlanner(self.cache).plan(
            cue,
            source=self.body,
            frame_size=FRAME,
            fps=FPS,
            variants=plan.variants_for(1, 0),
        )
        boxes = {
            (layer.frame.box_w, layer.frame.box_h) for layer in layers
        }
        self.assertEqual(len(boxes), 1)

    def test_the_patch_lands_on_its_anchor_and_nowhere_else(self):
        """Two bakes differing only in patch colour diff only at the mouth."""
        common = dict(
            height_px=120,
            flip=False,
            spinning=False,
            anchor="bottom",
            destination_dir=self.cache,
            mouth_anchor=(0.5, 0.25),
            mouth_size=(0.4, 0.2),
        )
        frame_a = bake_sprite(source=self.body, mouth=self.red, **common)
        frame_b = bake_sprite(source=self.body, mouth=self.blue, **common)
        self.assertEqual(
            (frame_a.box_w, frame_a.box_h), (frame_b.box_w, frame_b.box_h)
        )

        a = np.asarray(Image.open(frame_a.path).convert("RGB"), int)
        b = np.asarray(Image.open(frame_b.path).convert("RGB"), int)
        diff = np.abs(a - b).max(axis=2) > 10
        self.assertTrue(diff.any())
        ys, xs = np.where(diff)
        content_x0 = (frame_a.box_w - frame_a.content_w) // 2
        content_y0 = frame_a.box_h - frame_a.content_h
        rel_x = (xs.mean() - content_x0) / frame_a.content_w
        rel_y = (ys.mean() - content_y0) / frame_a.content_h
        self.assertAlmostEqual(rel_x, 0.5, delta=0.05)
        self.assertAlmostEqual(rel_y, 0.25, delta=0.05)

    def test_a_flipped_sprite_mirrors_the_patch_anchor(self):
        common = dict(
            height_px=120,
            spinning=False,
            anchor="bottom",
            destination_dir=self.cache,
            mouth_anchor=(0.3, 0.3),
            mouth_size=(0.4, 0.2),
        )
        frame_a = bake_sprite(
            source=self.body, flip=False, mouth=self.red, **common
        )
        frame_b = bake_sprite(
            source=self.body, flip=True, mouth=self.red, **common
        )
        a = np.asarray(Image.open(frame_a.path).convert("RGB"), int)
        b = np.asarray(Image.open(frame_b.path).convert("RGB"), int)
        # The patched bakes are horizontal mirrors of each other: the red
        # footprint sits on the left in one and on the right in the other.
        red_a = (a[..., 0] > 150) & (a[..., 1] < 110) & (a[..., 2] < 110)
        red_b = (b[..., 0] > 150) & (b[..., 1] < 110) & (b[..., 2] < 110)
        self.assertTrue(red_a.any())
        self.assertTrue(red_b.any())
        self.assertGreater(red_a[:, : a.shape[1] // 2].sum(), red_a[:, a.shape[1] // 2 :].sum())
        self.assertLess(red_b[:, : b.shape[1] // 2].sum(), red_b[:, b.shape[1] // 2 :].sum())


class TestTransitionRenderPlan(unittest.TestCase):
    """The new transitions have to survive the frame planner, not just parse."""

    def test_a_punchy_transition_still_reconciles_frames(self):
        from autovid.domain.frames import build_frame_plan

        timeline = {
            "scenes": [
                {"id": 1, "start_s": 0.0, "end_s": 3.0, "narration_s": 2.5,
                 "pause_after_s": 0.5},
                {"id": 2, "start_s": 3.0, "end_s": 6.0, "narration_s": 2.5,
                 "pause_after_s": 0.5,
                 "transition_in": {"type": "pixelize", "duration": 0.4}},
            ],
            "total_duration_s": 6.0,
        }
        plan = build_frame_plan(timeline, fps=FPS, width=FRAME[0], height=FRAME[1])

        self.assertEqual(plan.transition_offsets()[0]["type"], "pixelize")
        self.assertEqual(
            plan.rendered_frames - plan.overlapped_frames, plan.total_frames
        )

class TestPersistentHost(unittest.TestCase):
    """
    The persistent host: one presenter injected into every scene.

    The pinned properties: it is in every scene with no enter and no exit,
    its registry frames keep working in the chair, an explicit per-scene
    character beats it, and a scene character parked in its corner
    suppresses it for exactly that window instead of stacking under it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-host-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.host = make_sprite(self.tmp / "host.png")

    def host_script(self, scenes: list[dict], **layout) -> object:
        payload = {
            "video_metadata": {
                "title": "host",
                "resolution": "320x180",
                "fps": FPS,
            },
            "tts_config": {"voice": "test", "speed": 1.0},
            "scenes": scenes,
            "host_layout": {"image_file": str(self.host), **layout},
        }
        return parse_script(payload)

    def resolved(self, script, *, duration_s: float = 6.0):
        tts, pacing = reports_dict(moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 5.9)])
        timeline = {
            "scenes": [
                {
                    "id": scene.id,
                    "start_s": (scene.id - 1) * duration_s,
                    "end_s": scene.id * duration_s,
                }
                for scene in script.scenes
            ]
        }
        return resolve_character_cues(
            script,
            timeline=timeline,
            tts_report=tts,
            pacing_report=pacing,
        )

    def test_the_host_is_in_every_scene(self):
        script = self.host_script(
            [scene_dict(1), scene_dict(2, characters=[character_dict()])]
        )
        plan = self.resolved(script)

        self.assertEqual(len(plan.for_scene(1)), 1)
        self.assertEqual(len(plan.for_scene(2)), 2)
        for scene in script.scenes:
            host = plan.for_scene(scene.id)[-1]
            self.assertEqual(host.timing_source, "host_layout")
            self.assertEqual(host.enter.type, "none")
            self.assertEqual(host.exit.type, "none")
            self.assertEqual(host.start_s, 0.0)
            self.assertEqual(host.end_s, 6.0)
            self.assertEqual(host.idle.type, "tilt")

    def test_a_script_character_in_the_host_corner_hides_it(self):
        """The host stands down while a cue would land underneath it."""
        script = self.host_script(
            [
                scene_dict(
                    1,
                    characters=[character_dict(x=0.85)],
                ),
                scene_dict(
                    2,
                    characters=[character_dict(x=0.3)],
                ),
            ]
        )
        plan = self.resolved(script)

        hidden = plan.for_scene(1)[-1]
        kept = plan.for_scene(2)[-1]
        self.assertTrue(hidden.hide_windows, "the host never ducked")
        self.assertEqual(kept.hide_windows, ())
        # The window covers the whole cue, edges aligned.
        cue = plan.for_scene(1)[0]
        self.assertEqual(
            hidden.hide_windows, ((cue.start_s, cue.end_s),)
        )

    def test_an_explicit_character_beats_the_host(self):
        """A scene listing the same artwork gets exactly what it asked for."""
        script = self.host_script(
            [scene_dict(1, characters=[character_dict(image_file=str(self.host))])]
        )
        plan = self.resolved(script)

        self.assertEqual(len(plan.for_scene(1)), 1)
        cue = plan.for_scene(1)[0]
        self.assertNotEqual(cue.timing_source, "host_layout")

    def test_the_host_plans_layers_with_its_registry_frames(self):
        """The chair comes with the act: flap follows the voice in every scene."""
        closed = self.tmp / "closed.png"
        Image.new("RGB", (40, 20), (24, 24, 24)).save(closed)
        mouth = {
            "images": [str(closed), str(self.host)],
            "x": 0.5, "y": 0.25, "size": [0.3, 0.12], "period_s": 0.3,
        }
        script = self.host_script(
            [scene_dict(1), scene_dict(2)],
            mouth=mouth,
        )
        plan = self.resolved(script)
        for scene in script.scenes:
            host = plan.for_scene(scene.id)[-1]
            variants = plan.variants_for(scene.id, host.index)
            self.assertTrue(variants, f"scene {scene.id}: no host flap")
            self.assertTrue(all(v.kind == "mouth" for v in variants))

            # And the layers plan cleanly at the host's corner position.
            layers = SpritePlanner(self.cache).plan(
                host,
                source=Path(host.image_file),
                frame_size=FRAME,
                fps=FPS,
                variants=variants,
            )
            self.assertTrue(layers)
            self.assertTrue(layers[0].x_expression())

    def test_no_host_layout_means_no_host(self):
        payload = {
            "video_metadata": {
                "title": "x", "resolution": "320x180", "fps": FPS,
            },
            "tts_config": {"voice": "test"},
            "scenes": [scene_dict(1)],
        }
        script = parse_script(payload)
        plan = self.resolved(script)
        self.assertEqual(plan.for_scene(1), ())


class TestStoryFrame(unittest.TestCase):
    """
    The storytelling frame: the art lives in a fixed panel, the narrator
    stands OUTSIDE it.

    The pinned properties: the narrator is a persistent host cue anchored
    beside the panel in every scene; a guest fully inside the panel is the
    story being told and never suppresses the narrator; a guest sticking
    out into the narrator's strip does; and the filtergraph composites the
    panel above the background but under every character.
    """

    FRAME_BOX = (0.04, 0.06, 0.66, 0.88)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autovid-frame-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.host = make_sprite(self.tmp / "host.png")

    def frame_script(self, scenes: list[dict], **frame) -> object:
        payload = {
            "video_metadata": {
                "title": "story frame",
                "resolution": "320x180",
                "fps": FPS,
            },
            "tts_config": {"voice": "test", "speed": 1.0},
            "scenes": scenes,
            "story_frame": {"image_file": str(self.host), **frame},
        }
        return parse_script(payload)

    def resolved(self, script, *, duration_s: float = 6.0):
        tts, pacing = reports_dict(moments=[(0.0, 2.0), (2.5, 4.5), (5.0, 5.9)])
        timeline = {
            "scenes": [
                {
                    "id": scene.id,
                    "start_s": (scene.id - 1) * duration_s,
                    "end_s": scene.id * duration_s,
                }
                for scene in script.scenes
            ]
        }
        return resolve_character_cues(
            script,
            timeline=timeline,
            tts_report=tts,
            pacing_report=pacing,
        )

    def test_the_narrator_stands_outside_the_frame_in_every_scene(self):
        script = self.frame_script([scene_dict(1), scene_dict(2)])
        plan = self.resolved(script)

        for scene in script.scenes:
            cues = plan.for_scene(scene.id)
            self.assertEqual(len(cues), 1)
            narrator = cues[-1]
            self.assertEqual(narrator.timing_source, "host_layout")
            self.assertEqual(narrator.note, "storytelling-frame narrator")
            self.assertAlmostEqual(narrator.x, 0.845, places=3)
            self.assertAlmostEqual(narrator.y, 0.985, places=3)
            self.assertAlmostEqual(narrator.height, 0.46, places=3)
            self.assertEqual(narrator.start_s, 0.0)
            self.assertEqual(narrator.end_s, 6.0)

    def test_a_guest_inside_the_frame_never_suppresses_the_narrator(self):
        """Art inside the panel is the story being told; the narrator talks on."""
        # Feet hang past the panel's bottom edge (scene characters stand on
        # the screen floor, y ~ 0.95, while the panel stops at 0.94) and the
        # guest still counts as inside: the x span decides sideways.
        script = self.frame_script(
            [scene_dict(1, characters=[character_dict(x=0.35, y=0.99, height=0.5)])]
        )
        plan = self.resolved(script)

        narrator = plan.for_scene(1)[-1]
        self.assertEqual(narrator.hide_windows, ())

    def test_a_guest_sticking_out_suppresses_the_narrator_with_margin(self):
        script = self.frame_script(
            [scene_dict(1, characters=[character_dict(x=0.8)])]
        )
        plan = self.resolved(script)

        narrator = plan.for_scene(1)[-1]
        self.assertTrue(narrator.hide_windows, "the narrator never ducked")
        cue = plan.for_scene(1)[0]
        start, end = narrator.hide_windows[0]
        self.assertAlmostEqual(start, max(cue.start_s - 0.06, 0.0), places=3)
        self.assertAlmostEqual(end, cue.end_s + 0.06, places=3)

    def test_the_narrator_keeps_the_registry_act(self):
        closed = self.tmp / "closed.png"
        Image.new("RGB", (40, 20), (24, 24, 24)).save(closed)
        mouth = {
            "images": [str(closed), str(self.host)],
            "x": 0.5, "y": 0.25, "size": [0.3, 0.12], "period_s": 0.3,
        }
        script = self.frame_script([scene_dict(1)], mouth=mouth)
        plan = self.resolved(script)

        narrator = plan.for_scene(1)[-1]
        variants = plan.variants_for(1, narrator.index)
        self.assertTrue(variants, "no narrator flap")
        self.assertTrue(all(v.kind == "mouth" for v in variants))

        layers = SpritePlanner(self.cache).plan(
            narrator,
            source=Path(narrator.image_file),
            frame_size=FRAME,
            fps=FPS,
            variants=variants,
        )
        self.assertTrue(layers)

    def test_frame_geometry_defaults_match_the_left_panel(self):
        script = self.frame_script([scene_dict(1)])
        frame = script.story_frame
        self.assertEqual(
            (frame.x, frame.y, frame.width, frame.height), self.FRAME_BOX
        )
        self.assertEqual(frame.style, "border")

    def test_custom_style_requires_frame_png(self):
        with self.assertRaises(ScriptSchemaError):
            self.frame_script([scene_dict(1)], style="custom")

    def test_art_inset_parses_per_side(self):
        script = self.frame_script(
            [scene_dict(1)],
            art_inset={"t": 0.10, "b": 0.12, "l": 0.07, "r": 0.30},
        )
        self.assertEqual(
            script.story_frame.art_inset,
            {"t": 0.10, "b": 0.12, "l": 0.07, "r": 0.30},
        )

    def test_art_inset_rejects_unknown_sides_and_out_of_range(self):
        with self.assertRaises(ScriptSchemaError):
            self.frame_script([scene_dict(1)], art_inset={"x": 0.1})
        with self.assertRaises(ScriptSchemaError):
            self.frame_script([scene_dict(1)], art_inset=0.6)

    def test_tv_retro_art_window_is_uneven(self):
        """The TV keeps a slim control strip at the bottom: per-side inset."""
        # Panel 1000x500 -> inset t=0.05*500=25, b=0.155*500=78 (round),
        # l=0.04*1000=40, r=0.04*1000=40.
        box = {"x": 40, "y": 30, "w": 1000, "h": 500,
               "style": "tv_retro"}
        graph = build_scene_filtergraph(
            frames=90,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[],
            sprites=[],
            story_frame=box,
        )
        # Window = panel box shrunk per side, NOT a uniform shrink.
        self.assertIn("[bgart]crop=920:397:80:55[art]", graph)
        self.assertIn("[bfg][art]overlay=80:55", graph)

    def test_tv_wobble_and_lamp_blink(self):
        """tv_retro sways gently and its lamp hops between green/red."""
        box = {
            "x": 51, "y": 43, "w": 200, "h": 150, "style": "tv_retro",
            "wobble": {"ax": 3.0, "ay": 2.0, "period": 2.4},
            "lamp": {"png": "lamp.png", "x": 100, "y": 50,
                     "half": 64, "period": 1.0},
        }
        graph = build_scene_filtergraph(
            frames=90,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[],
            sprites=[],
            story_frame=box,
        )
        # The panel sways on a sine; the art copies the identical shift.
        self.assertIn("overlay=x='51+sin(2*PI*t/2.4)*3.0", graph)
        self.assertIn("y='43+sin(2*PI*t/2.4+PI/2)*2.0", graph)
        self.assertIn("[bgart]crop=184:119:59:51[art]", graph)
        self.assertIn("[bfg][art]overlay=x='59+sin(2*PI*t/2.4)*3.0", graph)
        # The lamp sheet hops half-width every half second; it takes the
        # input slot right after the panel, pushing later inputs up by 2.
        self.assertIn("[2:v]format=rgba[lampsheet]", graph)
        self.assertIn("100+sin(2*PI*t/2.4)*3.0-64*gte(mod(t,1.0),0.5)", graph)

    def test_the_panel_composites_under_the_characters(self):
        """Graph order: background -> frame panel -> sprites -> text."""
        box = {"x": 51, "y": 43, "w": 200, "h": 150}
        sprite = CharacterLayer(
            frame=SpriteFrame(
                path=Path("sprite.png"),
                box_w=10,
                box_h=20,
                content_w=10,
                content_h=20,
                feet_inset_px=20,
                centre_inset_px=10,
            ),
            start_s=0.0,
            end_s=1.0,
        )
        overlay = OverlayLayer(path=Path("text.png"), start_s=0.0, end_s=1.0)

        with_frame = build_scene_filtergraph(
            frames=90,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[overlay],
            sprites=[sprite],
            story_frame=box,
        )
        self.assertIn("[bfg]", with_frame)
        self.assertIn("overlay=51:43", with_frame)
        # The plate behind the panel is the scene cropped to the panel box
        # and padded out with the dark backdrop -- no full-frame art anywhere.
        self.assertIn("[bgfull]crop=200:150:51:43", with_frame)
        self.assertIn("pad=320:180:51:43:color=0x1F232D", with_frame)
        # The artwork is cropped into the panel's window -- the box shrunk
        # by the mat inset (3.5% of the panel's short side, rounded to 5) --
        # and composited onto the mat above the panel.
        self.assertIn("[bgart]crop=190:140:56:48[art]", with_frame)
        self.assertIn("[bfg][art]overlay=56:48", with_frame)
        # The panel occupies input 1; sprite and text shift one slot each.
        self.assertIn("[2:v]format=rgba[sp0]", with_frame)
        self.assertIn("[3:v]format=rgba,fade", with_frame)
        self.assertLess(with_frame.index("[bfg]"), with_frame.index("[sp0]"))
        # The art lands after the panel and before every character too.
        self.assertLess(with_frame.index("[bfart]"), with_frame.index("[sp0]"))

        without = build_scene_filtergraph(
            frames=90,
            frame_size=FRAME,
            fps=FPS,
            motion=None,
            overlays=[overlay],
            sprites=[sprite],
        )
        self.assertNotIn("[bfg]", without)
        self.assertIn("[1:v]format=rgba[sp0]", without)
        self.assertIn("[2:v]format=rgba,fade", without)

    def test_the_assembly_stage_renders_panel_then_narrator(self):
        """End to end: the panel is on screen, the narrator above it."""
        import json

        from autovid.application.assembly import AssemblyStage
        from autovid.paths import Paths

        payload = {
            "video_metadata": {
                "title": "sf e2e", "resolution": "320x180", "fps": FPS,
            },
            "tts_config": {"voice": "test", "speed": 1.0},
            "scenes": [scene_dict(1)],
            "story_frame": {"image_file": str(self.host), "style": "border"},
        }
        script = parse_script(payload)
        paths = Paths.from_workspace(self.tmp)
        paths.create()
        Image.new("RGB", FRAME, (235, 240, 245)).save(
            paths.prepared_images_dir / "scene_001.png"
        )
        for name, payload_json in (
            ("timeline.json", {"scenes": [
                {"id": 1, "start_s": 0.0, "narration_s": 1.0,
                 "pause_after_s": 0.0, "end_s": 1.0}
            ]}),
            ("tts_report.json", {"scenes": [
                {"id": 1, "units": [{"duration_s": 1.0}]}
            ]}),
            ("pacing_report.json", {"scenes": [
                {"id": 1, "sentences": [{"index": 0, "pause_ms": 0}]}
            ]}),
        ):
            (paths.output_dir / name).write_text(
                json.dumps(payload_json), encoding="utf-8"
            )

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()
        self.assertEqual(result.status, "pass", result.issues)
        clip = result.clips[0].clip
        frame_path = self.tmp / "probe.png"
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-ss", "0.5",
             "-i", str(clip), "-frames:v", "1", str(frame_path)],
            check=True,
        )

        image = np.array(Image.open(frame_path).convert("RGB")).astype(int)
        W, H = FRAME
        # The panel's centre is now the ARTWORK WINDOW: the scene itself,
        # cropped inside the frame.  The prepared plate is uniform
        # (235, 240, 245), so the window must show exactly that.
        window = image[H // 2, int(0.35 * W)]
        self.assertTrue(
            abs(int(window[0]) - 235) <= 4 and abs(int(window[1]) - 240) <= 4
            and abs(int(window[2]) - 245) <= 4,
            f"art window does not show the scene: {window}",
        )
        # Outside the panel, top-left: the dark backdrop, never the picture.
        corner = image[int(0.03 * H), int(0.01 * W)]
        self.assertLess(int(corner[0]), 100, f"corner {corner}")
        # The narrator's strip: the green test sprite stands there.
        strip = image[:, int(0.72 * W):]
        green = (
            (strip[:, :, 1] > strip[:, :, 0] + 40)
            & (strip[:, :, 1] > strip[:, :, 2] + 30)
        ).mean()
        self.assertGreater(float(green), 0.02, "narrator missing from strip")


if __name__ == "__main__":
    unittest.main()
