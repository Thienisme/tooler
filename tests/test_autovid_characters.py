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


if __name__ == "__main__":
    unittest.main()
