"""
Character walks: the script says "be over there by sentence 3", and the
sprite has to actually be there.

The expression tests here run against a real ffmpeg render rather than
checking string shapes, because a walk that is algebraically right but
evaluated wrongly by the filter is invisible until someone watches the
video -- which is exactly the bug class worth catching in CI.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from autovid.domain.characters import (  # noqa: E402
    CharacterIdle,
    CharacterStop,
    _resolve_moves,
)
from autovid.domain.script import (  # noqa: E402
    CharacterMotion,
    CharacterMove,
    parse_script,
)
from autovid.infrastructure.video.sprites import (  # noqa: E402
    _ease,
    _walk_angle_expression,
    _walk_expression,
    _walk_flip_windows,
)

WIDTH, HEIGHT = 640, 360
FPS = 30
BOX_W = 20


def _motion(kind: str = "none") -> CharacterMotion:
    return CharacterMotion(type=kind, duration_ms=0, edge="bottom")


def _ffmpeg() -> str | None:
    """The ffmpeg to verify against, or None when none is installed."""
    try:
        import imageio_ffmpeg
    except ImportError:
        from shutil import which

        return which("ffmpeg")
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    return exe if exe and Path(exe).exists() else None


class MoveParsingTest(unittest.TestCase):

    def _script(self, moves: list) -> dict:
        return {
            "video_metadata": {
                "title": "T",
                "author": "A",
                "resolution": "1920x1080",
                "fps": 30,
                "language": "vi",
            },
            "tts_config": {"engine": "vieneu", "voice": "Minh Quân Pro"},
            "scenes": [
                {
                    "id": 1,
                    "text": "Mot",
                    "image_file": "a.png",
                    "characters": [
                        {
                            "image_file": "chi_pheo.png",
                            "x": 0.2,
                            "moves": moves,
                        }
                    ],
                }
            ],
        }

    def test_parses_a_walk(self) -> None:
        script = parse_script(
            self._script(
                [
                    {"x": 0.6, "at_sentence": 2, "duration_ms": 800},
                    {"x": 0.85, "at_sentence": 4, "duration_ms": 600, "flip": True},
                ]
            )
        )
        moves = script.scenes[0].characters[0].moves
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0].x, 0.6)
        self.assertEqual(moves[0].duration_ms, 800)
        self.assertIs(moves[0].at_sentence, 2)
        self.assertIsNone(moves[1].flip if moves[1].flip is False else None)

    def test_a_walk_without_a_moment_is_rejected(self) -> None:
        from autovid.domain.script import ScriptSchemaError

        with self.assertRaises(ScriptSchemaError) as caught:
            parse_script(_walk_script([{"x": 0.6, "duration_ms": 500}]))
        self.assertIn("at_sentence", str(caught.exception))

    def test_no_moves_is_the_default(self) -> None:
        script = parse_script(_walk_script([]))
        self.assertEqual(script.scenes[0].characters[0].moves, ())

    def test_a_character_without_moves_still_parses(self) -> None:
        raw = _walk_script([])
        raw["scenes"][0]["characters"][0].pop("moves")
        script = parse_script(raw)
        self.assertEqual(script.scenes[0].characters[0].moves, ())


class ResolveMovesTest(unittest.TestCase):

    def test_sentence_anchor_uses_the_measured_narration(self) -> None:
        moves = (CharacterMove(x=0.6, at_sentence=2, duration_ms=500),)
        stops, warnings = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=8.0,
            windows=[(0.0, 1.5), (1.5, 3.0), (3.0, 5.0)],
            measured=True,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(stops[0].start_s, 1.5)

    def test_offset_anchor_is_relative_to_the_cue(self) -> None:
        moves = (CharacterMove(x=0.5, at_offset_ms=1200, duration_ms=400),)
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=2.0,
            cue_end=9.0,
            windows=[],
            measured=False,
        )
        self.assertAlmostEqual(stops[0].start_s, 3.2)

    def test_a_sentence_past_the_ending_falls_back_to_the_offset(self) -> None:
        moves = (CharacterMove(x=0.5, at_sentence=9, at_offset_ms=400),)
        stops, warnings = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=6.0,
            windows=[(0.0, 2.0)],
            measured=True,
        )
        self.assertEqual(warnings[0]["code"], "character_move_sentence_missing")
        self.assertAlmostEqual(stops[0].start_s, 0.4)

    def test_unmeasured_narration_is_reported(self) -> None:
        moves = (CharacterMove(x=0.5, at_sentence=2, at_offset_ms=300),)
        _, warnings = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=6.0,
            windows=[],
            measured=False,
        )
        self.assertEqual(warnings[0]["code"], "character_timing_unmeasured")

    def test_a_walk_never_starts_before_the_character_is_visible(self) -> None:
        moves = (CharacterMove(x=0.5, at_offset_ms=0, duration_ms=400),)
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=2.0,
            cue_end=6.0,
            windows=[],
            measured=False,
        )
        self.assertEqual(stops[0].start_s, 2.0)

    def test_a_walk_is_cut_short_at_the_end_of_the_scene(self) -> None:
        """Walking on alone off the end of the clip looks like a freeze."""
        moves = (CharacterMove(x=0.5, at_offset_ms=1000, duration_ms=4000),)
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=2.0,
            windows=[],
            measured=False,
        )
        self.assertAlmostEqual(stops[0].start_s + stops[0].duration_s, 2.0)

    def test_stops_are_returned_in_time_order(self) -> None:
        moves = (
            CharacterMove(x=0.8, at_offset_ms=2000, duration_ms=300),
            CharacterMove(x=0.4, at_offset_ms=500, duration_ms=300),
        )
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=9.0,
            windows=[],
            measured=False,
        )
        self.assertEqual([stop.x for stop in stops], [0.4, 0.8])

    def test_two_stops_at_the_same_moment_collapse(self) -> None:
        """A character cannot be in two places that no time separates."""
        moves = (
            CharacterMove(x=0.3, at_offset_ms=1000, duration_ms=300),
            CharacterMove(x=0.7, at_offset_ms=1000, duration_ms=300),
        )
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=9.0,
            windows=[],
            measured=False,
        )
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].x, 0.7)

    def test_y_is_inherited_when_a_stop_omits_it(self) -> None:
        moves = (
            CharacterMove(x=0.5, y=0.9, at_offset_ms=500, duration_ms=300),
            CharacterMove(x=0.7, at_offset_ms=1500, duration_ms=300),
        )
        stops, _ = _resolve_moves(
            moves,
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=9.0,
            windows=[],
            measured=False,
        )
        self.assertEqual(stops[1].y, 0.9)


class WalkExpressionTest(unittest.TestCase):

    class _Cue:
        def __init__(self, moves):
            self.x = 0.2
            self.y = 0.8
            self.moves = moves

    def test_no_walk_means_no_expression(self) -> None:
        expression = _walk_expression(
            self._Cue(()), axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        self.assertEqual(expression, "")

    def test_one_stop_produces_one_term(self) -> None:
        expression = _walk_expression(
            self._Cue((CharacterStop(0.6, None, 1.0, 0.8, "in_out"),)),
            axis="x",
            frame_size=(WIDTH, HEIGHT),
            box_w=BOX_W,
        )
        # 0.2 -> 0.6 is 0.4 of the frame width.
        self.assertIn("256", expression)

    def test_every_stop_contributes_a_term(self) -> None:
        stops = (
            CharacterStop(0.6, None, 1.0, 0.8, "in_out"),
            CharacterStop(0.85, None, 3.0, 0.6, "linear"),
        )
        expression = _walk_expression(
            self._Cue(stops), axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        # Each stop appears as its own ramp, keyed to its own start time.
        self.assertIn("(t-1)", expression)
        self.assertIn("(t-3)", expression)
        # 0.2 -> 0.6 is 0.4 of the frame; 0.6 -> 0.85 is 0.25 of it.
        self.assertTrue(expression.startswith("256"))
        self.assertIn("160", expression)

    def test_a_stop_that_returns_to_the_same_place_contributes_nothing(self) -> None:
        stops = (CharacterStop(0.2, None, 1.0, 0.5, "in_out"),)
        expression = _walk_expression(
            self._Cue(stops), axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        self.assertEqual(expression, "")

    def test_walking_left_gives_a_negative_term(self) -> None:
        stops = (CharacterStop(0.05, None, 1.0, 0.5, "linear"),)
        expression = _walk_expression(
            self._Cue(stops), axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        self.assertTrue(expression.startswith("-"))

    def test_y_only_moves_when_a_stop_names_it(self) -> None:
        flat = (CharacterStop(0.6, None, 1.0, 0.5, "in_out"),)
        self.assertEqual(
            _walk_expression(
                self._Cue(flat), axis="y", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
            ),
            "",
        )
        stepped = (CharacterStop(0.6, 0.5, 1.0, 0.5, "linear"),)
        self.assertNotEqual(
            _walk_expression(
                self._Cue(stepped), axis="y", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
            ),
            "",
        )


class EaseTest(unittest.TestCase):
    """
    The walk's easing curves, and the preview/renderer pair that must agree.

    The renderer emits an ffmpeg expression and the preview evaluates the
    same curve in Python.  If those two ever drift, the Studio shows an
    author a walk the finished video does not perform, which is worse than
    either being wrong on its own -- so parity is tested directly.
    """

    def _rendered(self, kind: str, p: float) -> float:
        """Run the expression the renderer would hand to ffmpeg."""
        import math

        expression = _ease(repr(float(p)), kind)
        return eval(  # noqa: S307 - the arithmetic the renderer emits
            expression.replace("PI", repr(math.pi)),
            {"__builtins__": {}},
            {"pow": pow, "min": min, "max": max},
        )

    def test_linear_is_the_progress_unchanged(self) -> None:
        self.assertEqual(_ease("P", "linear"), "P")

    def test_each_ease_mentions_the_progress(self) -> None:
        for kind in ("in_out", "out"):
            self.assertIn("P", _ease("P", kind))

    def test_out_is_not_the_in_out_curve_under_another_name(self) -> None:
        """The bug this file's ease section exists for.

        `1-(1-p)^3` expands to exactly `3p-3p^2+p^3`, so implementing "out"
        as the cubic made it the same curve as "in_out" and the panel's two
        options did nothing different.
        """
        for step in range(1, 100):
            p = step / 100
            self.assertNotAlmostEqual(
                self._rendered("out", p), self._rendered("in_out", p), places=6
            )

    def test_in_out_starts_and_stops_gently(self) -> None:
        """A walk has to ease off the mark and plant the final foot.

        `3p^2-2p^3` has a derivative that vanishes at both ends; the curve
        that used to stand in for it, `3p-3p^2+p^3`, has a slope of 3.0 at
        p=0, which is what made every walk lurch away from its mark.
        """
        for at in (0.0, 1.0):
            self.assertLess(abs(self._slope("in_out", at)), 1e-3)
        self.assertGreater(self._slope("out", 0.0), 1.5)

    def test_linear_holds_one_speed_the_whole_way(self) -> None:
        """`linear` is the one option that is deliberately not eased."""
        for at in (0.0, 0.5, 1.0):
            self.assertAlmostEqual(self._slope("linear", at), 1.0, places=3)

    def test_out_leaves_faster_than_in_out_all_the_way(self) -> None:
        """`out` is the hurry-across-and-settle option, by definition."""
        for step in range(1, 100):
            p = step / 100
            self.assertGreater(
                self._rendered("out", p), self._rendered("in_out", p)
            )

    def test_every_ease_lands_exactly_on_its_endpoints(self) -> None:
        """Otherwise the character jumps when a walk starts or finishes."""
        for kind in ("linear", "in_out", "out"):
            self.assertAlmostEqual(self._rendered(kind, 0.0), 0.0, places=9)
            self.assertAlmostEqual(self._rendered(kind, 1.0), 1.0, places=9)

    def test_the_preview_smooths_exactly_what_the_renderer_smooths(self) -> None:
        from autovid.presentation.studio.preview import ease as preview_ease

        for kind in ("linear", "in_out", "out"):
            for step in range(0, 101):
                p = step / 100
                self.assertAlmostEqual(
                    preview_ease(p, kind),
                    self._rendered(kind, p),
                    places=9,
                    msg=f"preview and renderer disagree on {kind} at p={p}",
                )

    def _slope(self, kind: str, at: float, h: float = 1e-6) -> float:
        """A forward/backward difference that still works at p=0 and p=1."""
        before = max(at - h, 0.0)
        span = (at + h) - before
        return (self._rendered(kind, at + h) - self._rendered(kind, before)) / span


class WalkAgainstFfmpegTest(unittest.TestCase):
    """The expression, measured off an actual render."""

    def setUp(self) -> None:
        self.ffmpeg = _ffmpeg()
        if self.ffmpeg is None:
            self.skipTest("no ffmpeg available to verify the walk")

    def test_the_sprite_actually_reaches_each_stop(self) -> None:
        from PIL import Image

        stops = (
            CharacterStop(0.6, None, 1.0, 0.8, "in_out"),
            CharacterStop(0.85, None, 3.0, 0.6, "linear"),
        )
        cue = WalkExpressionTest._Cue(stops)
        expression = _walk_expression(
            cue, axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        box_x = int(round(cue.x * WIDTH - BOX_W / 2))
        seconds = 4.5
        total = int(seconds * FPS)

        with tempfile.TemporaryDirectory(prefix="autovid_walk_") as staging:
            staging = Path(staging)
            subprocess.run(
                [
                    self.ffmpeg, "-y",
                    "-f", "lavfi",
                    "-i",
                    f"color=c=navy:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
                    "-f", "lavfi",
                    "-i",
                    f"color=c=red:s={BOX_W}x20:r={FPS}:d={seconds}",
                    "-filter_complex",
                    # The same sum the real filtergraph builds.
                    f"[0:v][1:v]overlay=x='{box_x}+{expression}':y=100"
                    ":format=auto[out]",
                    "-map", "[out]",
                    str(staging / "%04d.png"),
                ],
                check=True,
                capture_output=True,
            )
            shots = sorted(staging.glob("*.png"))
            self.assertTrue(shots, "ffmpeg rendered no frames")

            def centre_at(moment: float) -> float:
                index = min(len(shots) - 1, int(round(moment * FPS)))
                with Image.open(shots[index]) as raw:
                    pixels = raw.convert("RGB").load()
                    xs = [
                        x
                        for x in range(WIDTH)
                        for y in range(100, 120)
                        if pixels[x, y][0] > 120 and pixels[x, y][1] < 100
                    ]
                if not xs:
                    return -1.0
                return (sum(xs) / len(xs) + BOX_W / 2) / WIDTH

            # The character stands where it started until the first stop.
            self.assertAlmostEqual(centre_at(0.0), 0.20, delta=0.02)
            self.assertAlmostEqual(centre_at(0.9), 0.20, delta=0.02)
            # ...then it has arrived at the first stop and stopped there.
            self.assertAlmostEqual(centre_at(2.0), 0.60, delta=0.02)
            self.assertAlmostEqual(centre_at(2.9), 0.60, delta=0.02)
            # ...and finally reaches the last stop.
            self.assertAlmostEqual(centre_at(4.4), 0.85, delta=0.02)

    def test_the_character_actually_moves_between_the_samples(self) -> None:
        """A guard against the whole expression being silently dropped."""
        from PIL import Image

        # The walk starts at t=0 so the gap is visible anywhere in the clip.
        cue = WalkExpressionTest._Cue(
            (CharacterStop(0.85, None, 0.0, 0.8, "linear"),)
        )
        expression = _walk_expression(
            cue, axis="x", frame_size=(WIDTH, HEIGHT), box_w=BOX_W
        )
        box_x = int(round(cue.x * WIDTH - BOX_W / 2))
        seconds = 2.0

        with tempfile.TemporaryDirectory(prefix="autovid_walk_") as staging:
            staging = Path(staging)
            subprocess.run(
                [
                    self.ffmpeg, "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=navy:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
                    "-f", "lavfi",
                    "-i", f"color=c=red:s={BOX_W}x20:r={FPS}:d={seconds}",
                    "-filter_complex",
                    f"[0:v][1:v]overlay=x='{box_x}+{expression}':y=100"
                    ":format=auto[out]",
                    "-map", "[out]",
                    str(staging / "%04d.png"),
                ],
                check=True,
                capture_output=True,
            )
            shots = sorted(staging.glob("*.png"))
            positions = []
            for index in (0, len(shots) // 2):
                with Image.open(shots[index]) as raw:
                    pixels = raw.convert("RGB").load()
                    xs = [
                        x
                        for x in range(WIDTH)
                        for y in range(100, 120)
                        if pixels[x, y][0] > 120 and pixels[x, y][1] < 100
                    ]
                positions.append(min(xs) if xs else -1)
            self.assertGreater(positions[1], positions[0])


class WalkFlipTest(unittest.TestCase):
    """`moves[].flip`: turning the character around at a stop."""

    def test_no_flip_means_no_windows(self) -> None:
        self.assertEqual(
            _walk_flip_windows(
                (CharacterStop(0.6, None, 1.0, 0.8, "in_out"),),
                start_s=0.0,
                end_s=5.0,
                baked_facing=False,
            ),
            (),
        )

    def test_the_turn_holds_after_the_walk_finishes(self) -> None:
        """A window that ended with the walk would cancel the turn."""
        windows = _walk_flip_windows(
            (CharacterStop(0.6, None, 1.0, 0.8, "in_out", flip=True),),
            start_s=0.0,
            end_s=5.0,
            baked_facing=False,
        )
        self.assertEqual(windows, ((1.0, 5.0, True),))

    def test_a_walk_that_turns_back_covers_both_stretches(self) -> None:
        windows = _walk_flip_windows(
            (
                CharacterStop(0.6, None, 1.0, 0.8, "in_out", flip=True),
                CharacterStop(0.3, None, 3.0, 0.8, "in_out", flip=False),
            ),
            start_s=0.0,
            end_s=5.0,
            baked_facing=False,
        )
        self.assertEqual(windows, ((1.0, 3.0, True),))

    def test_a_stop_asked_to_keep_the_baked_facing_does_nothing(self) -> None:
        self.assertEqual(
            _walk_flip_windows(
                (CharacterStop(0.6, None, 1.0, 0.8, "in_out", flip=False),),
                start_s=0.0,
                end_s=5.0,
                baked_facing=False,
            ),
            (),
        )

    def test_the_windows_never_reach_past_the_cue(self) -> None:
        for window in _walk_flip_windows(
            (CharacterStop(0.6, None, 4.5, 0.8, "in_out", flip=True),),
            start_s=0.0,
            end_s=5.0,
            baked_facing=True,
        ):
            self.assertLessEqual(window[1], 5.0)
            self.assertGreater(window[1], window[0])

    # -- the filtergraph the renderer actually builds ---------------------

    def _sprite_block(self, layer) -> str:
        from autovid.infrastructure.video.filters import build_sprite_block

        return build_sprite_block(
            input_index=1,
            label_index=0,
            background="[bg]",
            layer=layer,
        )[0]

    @staticmethod
    def _layer(**kwargs):
        from autovid.infrastructure.video.sprites import (
            CharacterLayer,
            SpriteFrame,
        )

        frame = SpriteFrame(
            path=Path("sprite.png"),
            box_w=BOX_W,
            box_h=40,
            content_w=BOX_W,
            content_h=40,
            feet_inset_px=40,
            centre_inset_px=20,
        )
        return CharacterLayer(
            frame=frame, start_s=0.0, end_s=5.0, box_x=10, box_y=10, **kwargs
        )

    def test_a_turning_walk_adds_a_gated_mirror_to_the_chain(self) -> None:
        chain = self._sprite_block(
            self._layer(walk_flips=((1.0, 5.0, True),))
        )
        self.assertIn("hflip", chain)
        self.assertIn("enable='between(t,1,5)'", chain)

    def test_a_walk_without_a_turn_mirrors_nothing(self) -> None:
        chain = self._sprite_block(self._layer())
        self.assertNotIn("hflip", chain)

    def test_a_walk_mirrored_only_off_screen_does_not_flip(self) -> None:
        """A window asking for the baked facing must not be a flip."""
        chain = self._sprite_block(self._layer(walk_flips=((1.0, 5.0, False),)))
        self.assertNotIn("hflip", chain)

    def test_a_layer_that_turns_becomes_a_new_layer(self) -> None:
        """The layer is frozen, so the planner must rebuild the stack."""
        from PIL import Image

        from autovid.infrastructure.video.sprites import SpritePlanner

        class Cue:
            scene_id = 1
            index = 0
            image_file = ""
            start_s = 0.0
            end_s = 5.0
            x = 0.2
            y = 0.8
            height = 0.4
            flip = False
            enter = _motion("none")
            idle = CharacterIdle(type="none", amplitude_px=0, period_s=2.0)
            exit = _motion("none")
            sfx = None
            sfx_volume = 0.5
            timing_source = "offset"
            sentence_index = None
            dropped = False
            note = ""
            hide_windows = ()
            mouth = None

            def __init__(self, moves) -> None:
                self.moves = moves

            @property
            def duration_s(self) -> float:
                return self.end_s - self.start_s

        with tempfile.TemporaryDirectory(prefix="autovid_flip_") as staging:
            staging = Path(staging)
            sprite = staging / "sprite.png"
            Image.new("RGBA", (40, 80), (230, 60, 60, 255)).save(sprite)

            cue = Cue((CharacterStop(0.6, None, 1.0, 0.8, "in_out", flip=True),))
            planner = SpritePlanner(staging / "cache")
            layers = planner.plan(
                cue,
                source=sprite,
                frame_size=(WIDTH, HEIGHT),
                fps=FPS,
            )
            self.assertTrue(layers)
            for layer in layers:
                self.assertEqual(layer.walk_flips, ((1.0, 5.0, True),))


class FlipAgainstFfmpegTest(unittest.TestCase):
    """The mirror, measured off an actual render -- not off the string."""

    def setUp(self) -> None:
        self.ffmpeg = _ffmpeg()
        if self.ffmpeg is None:
            self.skipTest("no ffmpeg available to verify the flip")

    def test_the_sprite_is_mirrored_after_the_turn(self) -> None:
        from PIL import Image, ImageDraw

        seconds = 4.0
        turn = 2.0
        total = int(seconds * FPS)

        with tempfile.TemporaryDirectory(prefix="autovid_flip_") as staging:
            staging = Path(staging)
            # An asymmetric sprite: a wide block on the left, a thin arm on
            # the right.  Mirroring moves the ink, so the measurement is a
            # sign change rather than a magnitude that stays put.
            sprite = Image.new("RGBA", (160, 60), (0, 0, 0, 0))
            painter = ImageDraw.Draw(sprite)
            painter.rectangle((2, 2, 58, 57), fill=(230, 60, 60, 255))
            painter.rectangle((60, 24, 158, 36), fill=(60, 230, 60, 255))
            sprite.save(staging / "sprite.png")

            expression = (
                f"between(t,{turn},999)"
            )
            subprocess.run(
                [
                    self.ffmpeg, "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=navy:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
                    "-loop", "1", "-t", str(seconds), "-i",
                    str(staging / "sprite.png"),
                    "-filter_complex",
                    (
                        f"[1:v]format=rgba,hflip=enable='{expression}'[sp];"
                        f"[0:v][sp]overlay=x=60:y=100:format=auto[out]"
                    ),
                    "-map", "[out]",
                    "-frames:v", str(total),
                    str(staging / "%04d.png"),
                ],
                check=True,
                capture_output=True,
            )
            shots = sorted(staging.glob("*.png"))
            self.assertTrue(shots, "ffmpeg rendered no frames")

            def red_edge(moment: float) -> float:
                """Distance from the sprite box's left edge to the red block."""
                index = min(len(shots) - 1, int(round(moment * FPS)))
                with Image.open(shots[index]) as raw:
                    pixels = raw.convert("RGB").load()
                    xs = [
                        x for x in range(WIDTH)
                        for y in range(100, 160)
                        if pixels[x, y][0] > 170
                        and pixels[x, y][1] < 120
                        and pixels[x, y][2] < 120
                    ]
                self.assertTrue(xs, "the sprite disappeared")
                return float(min(xs))

            before = red_edge(1.0)
            after = red_edge(3.5)
            # Before the turn the red mass sits at the box's left edge;
            # after it, the mirror has moved it to the right-hand side.
            self.assertGreater(after - before, 60)


def _walk_script(moves: list) -> dict:
    """The smallest script the schema accepts that carries a walk."""
    return {
        "video_metadata": {
            "title": "T",
            "author": "A",
            "resolution": "1920x1080",
            "fps": 30,
            "language": "vi",
        },
        "tts_config": {"engine": "vieneu", "voice": "Minh Quân Pro"},
        "scenes": [
            {
                "id": 1,
                "text": "Mot",
                "image_file": "a.png",
                "characters": [
                    {"image_file": "chi_pheo.png", "x": 0.2, "moves": moves}
                ],
            }
        ],
    }


class WalkPaceTest(unittest.TestCase):
    """`moves[].speed`: the same distance over a different length of time."""

    def _stop(self, **kwargs) -> CharacterStop:
        return _resolve_moves(
            (CharacterMove(x=0.8, at_offset_ms=0, **kwargs),),
            scene_id=1,
            index=0,
            cue_start=0.0,
            cue_end=30.0,
            windows=[],
            measured=True,
        )[0][0]

    def test_speed_one_leaves_the_duration_alone(self) -> None:
        self.assertAlmostEqual(self._stop(duration_ms=800).duration_s, 0.8)

    def test_a_slower_speed_takes_longer_over_the_same_distance(self) -> None:
        self.assertAlmostEqual(self._stop(duration_ms=800, speed=0.5).duration_s, 1.6)

    def test_a_faster_speed_takes_less(self) -> None:
        self.assertAlmostEqual(self._stop(duration_ms=800, speed=2.0).duration_s, 0.4)

    def test_a_dash_is_still_cut_short_at_the_end_of_the_cue(self) -> None:
        """Speed divides the duration *before* the clamp, not after.

        Otherwise a fast walk would overrun the cue and leave the character
        walking on alone off the end of the clip.
        """
        stops, _ = _resolve_moves(
            (CharacterMove(x=0.8, at_offset_ms=0, duration_ms=900, speed=3.0),),
            scene_id=1, index=0, cue_start=0.0, cue_end=0.4, windows=[],
            measured=True,
        )
        # 900ms at 3x is 300ms, which already overruns the 400ms cue once the
        # walk starts at the cue's own start -- so the clamp, not the speed,
        # is what stops it.
        self.assertAlmostEqual(stops[0].duration_s, 0.3)

    def test_the_schema_rejects_a_speed_outside_the_range(self) -> None:
        from autovid.domain.script import ScriptSchemaError

        for bad in (0.0, 9.0):
            with self.subTest(speed=bad):
                with self.assertRaises(ScriptSchemaError):
                    parse_script(
                        _walk_script([{"x": 0.5, "at_offset_ms": 0, "speed": bad}])
                    )

    def test_the_schema_defaults_speed_to_one(self) -> None:
        move = parse_script(
            _walk_script([{"x": 0.5, "at_offset_ms": 0}])
        ).scenes[0].characters[0].moves[0]
        self.assertEqual(move.speed, 1.0)

    def test_the_schema_reads_speed(self) -> None:
        move = parse_script(
            _walk_script([{"x": 0.5, "at_offset_ms": 0, "speed": 0.4}])
        ).scenes[0].characters[0].moves[0]
        self.assertAlmostEqual(move.speed, 0.4)


class WalkSwayTest(unittest.TestCase):
    """`moves[].sway_deg`: the body leans into the direction of travel."""

    class _Cue:
        def __init__(self, moves, *, x=0.2, flip=False):
            self.moves = tuple(moves)
            self.x = x
            self.y = 0.8
            self.flip = flip

    def _expression(self, stops, *, flip=False) -> str:
        return _walk_angle_expression(self._Cue(stops, flip=flip))

    def _degrees(self, expression: str, at: float) -> float:
        import math

        return math.degrees(
            eval(  # noqa: S307 - the same arithmetic the renderer evaluates
                expression.replace("PI", repr(math.pi)),
                {"__builtins__": {}},
                {"t": at, "min": min, "max": max, "abs": abs,
                 "pow": pow, "sin": math.sin, "cos": math.cos},
            )
        )

    def _peak(self, expression: str, start: float, end: float) -> float:
        return max(
            abs(self._degrees(expression, start + (end - start) * i / 200))
            for i in range(201)
        )

    def test_no_sway_means_no_rotation(self) -> None:
        self.assertEqual(
            self._expression((CharacterStop(0.8, None, 1.0, 1.0, "in_out"),)), ""
        )

    def test_the_lean_follows_the_direction_of_travel(self) -> None:
        right = self._expression(
            (CharacterStop(0.8, None, 1.0, 1.0, "in_out", sway_deg=15.0),)
        )
        left = self._expression(
            (CharacterStop(0.05, None, 1.0, 1.0, "in_out", sway_deg=15.0),)
        )
        self.assertGreater(self._degrees(right, 1.5), 0.0)
        self.assertLess(self._degrees(left, 1.5), 0.0)

    def test_the_lean_reaches_the_angle_that_was_asked_for(self) -> None:
        """An author who typed 15 degrees should see about 15 degrees.

        The envelope has to peak at exactly one, or the control silently
        means a fraction of its own label.
        """
        expression = self._expression(
            (CharacterStop(0.9, None, 1.0, 1.0, "in_out", sway_deg=15.0),)
        )
        self.assertAlmostEqual(
            self._peak(expression, 1.0, 2.0), 15.0, delta=0.01
        )

    def test_the_character_stands_upright_outside_the_walk(self) -> None:
        """A finished walk must not leave the character leaning.

        Because the terms sum, a lean that never returned would also tilt
        the character during every later walk and every pause after it.
        """
        expression = self._expression(
            (CharacterStop(0.8, None, 1.0, 1.0, "in_out", sway_deg=15.0),)
        )
        self.assertAlmostEqual(self._degrees(expression, 0.5), 0.0, places=9)
        self.assertAlmostEqual(self._degrees(expression, 1.0), 0.0, places=9)
        self.assertAlmostEqual(self._degrees(expression, 2.0), 0.0, places=9)
        self.assertAlmostEqual(self._degrees(expression, 5.0), 0.0, places=9)

    def test_a_short_walk_leans_less_than_a_long_one(self) -> None:
        """Reach grows with the square root of the distance travelled."""
        short = self._expression(
            (CharacterStop(0.3, None, 1.0, 1.0, "in_out", sway_deg=20.0),)
        )
        long_walk = self._expression(
            (CharacterStop(0.9, None, 1.0, 1.0, "in_out", sway_deg=20.0),)
        )
        self.assertLess(
            self._peak(short, 1.0, 2.0), self._peak(long_walk, 1.0, 2.0)
        )

    def test_a_turn_leans_the_other_way_in_baked_space(self) -> None:
        """`hflip` runs after `rotate`, so a mirrored lean is pre-inverted.

        Leaning the same way in baked space would make a character walking
        left appear to lean backwards -- the one thing this must never do.
        """
        stops = (
            CharacterStop(0.8, None, 1.0, 0.8, "in_out", sway_deg=15.0),
            CharacterStop(0.2, None, 2.5, 0.8, "in_out", flip=True, sway_deg=15.0),
        )
        expression = self._expression(stops)
        first = self._degrees(expression, 1.4)
        second = self._degrees(expression, 2.9)
        self.assertGreater(first, 0.0)
        self.assertGreater(second, 0.0)
        # ...but the second walk is mirrored, so on screen it leans left.
        self.assertLess(second * -1, 0.0)

    def test_mirrored_artwork_leans_the_other_way_in_baked_space(self) -> None:
        stops = (
            CharacterStop(0.8, None, 1.0, 1.0, "in_out", flip=False, sway_deg=15.0),
        )
        expression = self._expression(stops, flip=True)
        windows = _walk_flip_windows(
            stops, start_s=0.0, end_s=3.0, baked_facing=True
        )
        self.assertTrue(windows, "this cue should be mirrored at 1.5s")
        baked = self._degrees(expression, 1.5)
        screen = baked * -1 if windows[0][0] <= 1.5 < windows[0][1] else baked
        self.assertGreater(screen, 0.0)

    def test_the_lean_joins_the_rotation_the_layer_already_had(self) -> None:
        """A walk and a spin are both rotations; they must add, not fight."""
        from autovid.infrastructure.video.sprites import CharacterLayer

        layer = CharacterLayer(
            frame=self._frame(),
            start_s=0.0,
            end_s=5.0,
            idle=CharacterIdle(type="tilt", amplitude_px=4, period_s=2.0),
            walk_angle="0.1",
        )
        angle = layer.angle_expression()
        self.assertIsNotNone(angle)
        # The tilt idle's own curve and the walk's lean are both in there,
        # summed, rather than one having displaced the other.
        self.assertIn("cos", angle)
        self.assertIn("0.1", angle)
        self.assertEqual(angle.count("+"), 1)

    def _frame(self):
        from pathlib import Path

        from autovid.infrastructure.video.sprites import SpriteFrame

        return SpriteFrame(
            path=Path("a.png"), box_w=40, box_h=80, content_w=40,
            content_h=80, feet_inset_px=0, centre_inset_px=0,
        )

    def test_a_layer_with_no_rotation_at_all_says_so(self) -> None:
        from autovid.infrastructure.video.sprites import CharacterLayer

        layer = CharacterLayer(frame=self._frame(), start_s=0.0, end_s=5.0)
        self.assertIsNone(layer.angle_expression())

    def test_the_walk_lean_is_part_of_the_cache_signature(self) -> None:
        """Without it, a retuned sway would reuse the previous clip."""
        from autovid.infrastructure.video.sprites import CharacterLayer

        frame = self._frame()
        plain = CharacterLayer(frame=frame, start_s=0.0, end_s=5.0)
        leaning = CharacterLayer(frame=frame, start_s=0.0, end_s=5.0, walk_angle="0.2")
        self.assertNotEqual(plain.to_dict(), leaning.to_dict())

    def test_the_schema_reads_sway_deg(self) -> None:
        move = parse_script(
            _walk_script([{"x": 0.5, "at_offset_ms": 0, "sway_deg": 20.0}])
        ).scenes[0].characters[0].moves[0]
        self.assertAlmostEqual(move.sway_deg, 20.0)

    def test_the_schema_refuses_an_absurd_lean(self) -> None:
        from autovid.domain.script import ScriptSchemaError

        with self.assertRaises(ScriptSchemaError):
            parse_script(
                _walk_script([{"x": 0.5, "at_offset_ms": 0, "sway_deg": 400.0}])
            )


if __name__ == "__main__":
    unittest.main()