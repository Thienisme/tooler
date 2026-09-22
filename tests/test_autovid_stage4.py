"""
Stage 4 tests: frame planning, filtergraph construction and clip rendering.

Two kinds of test live here, and the split is deliberate.

The *pure* tests cover the arithmetic and the filter strings: frame
reconciliation, transition compensation, Ken Burns travel, overlay
windows.  They are fast and they fail with a readable message.

The *render* tests actually encode video with the project's bundled ffmpeg
and then read pixels back out of the frames.  That is the only way to catch
the class of bug this stage is prone to: a filtergraph that ffmpeg accepts
happily and that produces a perfectly valid video in which the text never
appears, the motion never moves, or the overlay never goes away.  Both bugs
found while writing this stage -- `[v0]_src` not being a valid label, and
`fade` seeing only t=0 on a still input -- were invisible to string tests
and only showed up when frames were measured.

The render tests use a 320x180 frame so a whole stage run costs seconds
rather than minutes; the frame arithmetic being tested is size-independent.

Run with:
    python -m unittest tests.test_autovid_stage4
"""

from __future__ import annotations

import json
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

from autovid.application.assembly import (  # noqa: E402
    MIN_OVERLAY_SECONDS,
    TYPEWRITER_STEPS,
    AssemblyStage,
    _escape_concat_path,
)
from autovid.domain.frames import build_frame_plan  # noqa: E402
from autovid.domain.script import parse_script  # noqa: E402
from autovid.infrastructure.ffmpeg import (  # noqa: E402
    FFMPEG,
    FFPROBE,
    ffmpeg_available,
    probe_duration,
)
from autovid.infrastructure.image.fonts import find_system_font  # noqa: E402
from autovid.infrastructure.video.filters import (  # noqa: E402
    MIN_TRAVEL_PX_PER_FRAME,
    OverlayLayer,
    build_scene_filtergraph,
    build_zoompan_filter,
    inspect_motion,
)
from autovid.infrastructure.video.text import (  # noqa: E402
    parse_hex_colour,
    render_text_layer,
    render_typewriter_layers,
)
from autovid.paths import Paths, read_json  # noqa: E402

FPS = 10
FRAME = (320, 180)

# The test artwork is deliberately light: nothing in it is near black, so
# any dark pixel in a rendered frame can only be text.
DARK_LUMA = 60


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def script_dict(scenes: list[dict], *, fps: int = FPS, resolution: str = "320x180") -> dict:
    """
    A script the parser accepts.

    Fixture scenes carry their durations under `_`-prefixed keys, which are
    private to the fixture: they are stripped here so the schema never sees
    them, and read by `timeline_dict` so the timeline matches the script.
    """
    return {
        "video_metadata": {
            "title": "Stage 4",
            "author": "tester",
            "resolution": resolution,
            "fps": fps,
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
        "pacing": {
            "auto_pause": {"enabled": True, "per_sentence": True},
            "section_breaks": [],
            "custom_pauses": {},
        },
        "scenes": [
            {key: value for key, value in scene.items() if not key.startswith("_")}
            for scene in scenes
        ],
    }


def scene_dict(
    scene_id: int,
    *,
    narration_s: float = 3.0,
    pause_s: float = 0.2,
    transition: dict | None = None,
    ken_burns: dict | None = None,
    overlays: list[dict] | None = None,
) -> dict:
    payload = {
        "id": scene_id,
        "text": f"Cảnh số {scene_id}.",
        "image_file": f"images/scene_{scene_id:03d}.png",
        "ken_burns": ken_burns
        or {"enabled": False, "type": "none", "start_scale": 1.0, "end_scale": 1.0},
    }
    if transition is not None:
        payload["transition_in"] = transition
    if overlays is not None:
        payload["text_overlays"] = overlays
    # Durations live in the timeline, not the script; they are carried here
    # so the fixture can build a timeline that matches what it declares.
    payload["_narration_s"] = narration_s
    payload["_pause_s"] = pause_s
    return payload


def timeline_dict(scenes: list[dict], *, fps: int = FPS) -> dict:
    """
    Build a timeline.json from fixture scenes.

    Mirrors what stage 2 writes: `start_s` accumulates, `narration_s` is the
    measured audio length, and `pause_after_s` is the silence inserted after
    it.  `boundary_silence_s` is the audible gap, which is the two added.
    """
    entries = []
    cursor = 0.0
    for index, scene in enumerate(scenes):
        narration_s = scene.get("_narration_s", 3.0)
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
                "transition_in": scene.get("transition_in", {"type": "cut", "duration": 0.0}),
                "ken_burns": scene["ken_burns"],
            }
        )
        cursor += narration_s + pause_s

    return {
        "version": 1,
        "title": "Stage 4",
        "resolution": f"{FRAME[0]}x{FRAME[1]}",
        "fps": fps,
        "sample_rate": 48000,
        "channels": 1,
        "voiceover_file": "audio/voiceover_full.wav",
        "voiceover_duration_s": round(cursor, 4),
        "total_duration_s": round(cursor, 4),
        "scenes": entries,
    }


def artwork(path: Path, *, size: tuple[int, int] = FRAME, colour: str = "#8FD694"):
    """Light flat-colour artwork: carries motion, holds no dark pixels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, "#FFFFFF")
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.ellipse(
        (width // 4, height // 4, width * 3 // 4, height * 3 // 4),
        fill=colour,
    )
    image.save(path)
    return path


def overlay_dict(
    text: str = "TIÊU ĐỀ",
    *,
    position: str = "center",
    animation: str = "fade_in",
    start_ms: int = 500,
    end_ms: int = 2500,
    animation_ms: int = 300,
    size: int = 24,
) -> dict:
    return {
        "text": text,
        "font": "assets/fonts/handwriting.ttf",
        "font_size": size,
        "color": "#000000",
        "stroke_color": "#000000",
        "stroke_width": 1,
        "position": position,
        "start_offset_ms": start_ms,
        "end_offset_ms": end_ms,
        "animation": animation,
        "animation_duration_ms": animation_ms,
    }


class WorkspaceFixture(unittest.TestCase):
    """A real workspace on disk, with a parsed script and a timeline."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_stage4_")
        self.workspace = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(
        self,
        scenes: list[dict],
        *,
        draw_images: bool = True,
        provide_font: bool = True,
        fps: int = FPS,
    ) -> tuple:
        """Write script.json, assets and timeline.json; return (script, paths)."""
        payload = script_dict(scenes, fps=fps)
        script = parse_script(payload)
        (self.workspace / "script.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        paths = Paths.from_workspace(self.workspace)
        paths.create()

        # A workspace whose scripted font exists is the normal case; tests
        # that want the fallback path pass provide_font=False.
        if provide_font:
            source = find_system_font()
            if source is None and any(scene.get("text_overlays") for scene in scenes):
                self.skipTest("no system font available to stand in for the asset")
            if source is not None:
                target = self.workspace / "assets" / "fonts" / "handwriting.ttf"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

        for scene in script.scenes:
            if draw_images:
                artwork(self.workspace / scene.image_file)
                # Stage 3 runs before stage 4 in the real pipeline; providing
                # the prepared frame keeps this fixture honest about that and
                # avoids the "no prepared frame" warning path.
                artwork(paths.prepared_images_dir / f"scene_{scene.id:03d}.png")

        timeline = timeline_dict(scenes, fps=fps)
        (paths.output_dir / "timeline.json").write_text(
            json.dumps(timeline, ensure_ascii=False), encoding="utf-8"
        )
        return script, paths


def extract_frames(clip: Path, destination: Path) -> list[Path]:
    """Decode every frame of a clip to PNG, in presentation order."""
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", str(clip), str(destination / "f%04d.png")],
        check=True,
    )
    return sorted(destination.glob("f*.png"))


def dark_pixels(path: Path) -> int:
    """
    Count pixels darker than DARK_LUMA.  Only text can be that dark.

    Useful for "is the text there", but blind to partial opacity: black text
    at 50% over white is mid grey, which is not a dark pixel at all.  Use
    `darkest` for anything to do with a fade.
    """
    grey = Image.open(path).convert("L")
    return sum(1 for value in grey.getdata() if value < DARK_LUMA)


def darkest(path: Path) -> int:
    """Darkest pixel in the frame: 255 when nothing is drawn, 0 when the
    text is fully opaque."""
    return min(Image.open(path).convert("L").getdata())


def dark_centroid_x(path: Path) -> float | None:
    """Mean x of the dark pixels, which tracks text as it slides."""
    grey = Image.open(path).convert("L")
    width = grey.width
    values = list(grey.getdata())
    xs = [index % width for index, value in enumerate(values) if value < DARK_LUMA]
    if not xs:
        return None
    return sum(xs) / len(xs)


def frame_count(clip: Path) -> int:
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=nw=1:nk=1",
            str(clip),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


# --------------------------------------------------------------------------
# Frame planning (pure arithmetic)
# --------------------------------------------------------------------------


class TestFramePlan(unittest.TestCase):
    def test_frames_tile_the_timeline_without_gaps(self):
        scenes = [scene_dict(i, narration_s=3.3, pause_s=0.2) for i in range(1, 6)]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        # Cumulative boundaries: each scene starts where the last ended.
        for previous, current in zip(plan.scenes, plan.scenes[1:]):
            self.assertEqual(previous.end_frame, current.start_frame)

        self.assertEqual(plan.scenes[-1].end_frame, plan.total_frames)
        self.assertEqual(
            plan.total_frames, round(timeline["total_duration_s"] * FPS)
        )

    def test_rounding_does_not_accumulate_across_scenes(self):
        """
        The bug this guards against: rounding every scene to 2.0s at 10fps
        when each really lasts 2.04s would lose 0.4s over ten scenes, and a
        0.4s lip-sync error is not a rounding detail.
        """
        scenes = [scene_dict(i, narration_s=2.04, pause_s=0.0) for i in range(1, 11)]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        # 10 x 2.04s = 20.4s = 204 frames.  Rounding each scene on its own
        # would give 10 x 20 = 200 frames and lose 4 frames of sync.
        self.assertEqual(plan.total_frames, 204)
        self.assertNotEqual(plan.total_frames, 10 * round(2.04 * FPS))
        self.assertEqual(
            plan.total_frames, round(timeline["total_duration_s"] * FPS)
        )

    def test_rendered_frames_equal_total_plus_overlaps(self):
        """The invariant the whole no-drift claim rests on."""
        scenes = [
            scene_dict(1),
            scene_dict(2, transition={"type": "fade", "duration": 0.4}),
            scene_dict(3, transition={"type": "fade", "duration": 0.4}),
        ]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        self.assertEqual(
            plan.rendered_frames - plan.overlapped_frames, plan.total_frames
        )

    def test_transition_extends_the_outgoing_segment(self):
        scenes = [
            scene_dict(1, pause_s=0.5),
            scene_dict(2, transition={"type": "fade", "duration": 0.4}),
        ]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        first = plan.scenes[0]
        self.assertEqual(first.transition_after, "fade")
        self.assertEqual(first.transition_after_frames, 4)
        # The clip is longer than its slot, so the overlap costs nothing.
        self.assertEqual(
            first.segment_frames, first.narration_frames + first.pause_frames + 4
        )
        self.assertEqual(plan.scenes[1].segment_frames, plan.scenes[1].narration_frames + plan.scenes[1].pause_frames)
        self.assertEqual(plan.scenes[1].transition_after, None)

    def test_transition_is_clamped_to_the_pause(self):
        """
        A 3-second dissolve into a scene that only pauses 0.1s would eat
        2.9s of narration; it is shortened instead, and said so.
        """
        scenes = [
            scene_dict(1, pause_s=0.1),
            scene_dict(2, transition={"type": "fade", "duration": 3.0}),
        ]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        first = plan.scenes[0]
        self.assertEqual(first.transition_after_frames, 1)
        self.assertTrue(first.transition_clamped)
        self.assertIn("transition_clamped", {w.code for w in plan.warnings})
        self.assertEqual(
            plan.rendered_frames - plan.overlapped_frames, plan.total_frames
        )

    def test_cut_transitions_cost_nothing(self):
        scenes = [
            scene_dict(1, transition={"type": "cut", "duration": 0.0}),
            scene_dict(2, transition={"type": "cut", "duration": 0.0}),
        ]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        self.assertEqual(plan.overlapped_frames, 0)
        self.assertFalse(plan.has_transitions)
        self.assertEqual(plan.rendered_frames, plan.total_frames)

    def test_zero_length_scene_is_repaired_not_fatal(self):
        scenes = [scene_dict(1, narration_s=0.02, pause_s=0.0), scene_dict(2)]
        timeline = timeline_dict(scenes)
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        self.assertGreaterEqual(plan.scenes[0].segment_frames, 1)
        self.assertGreaterEqual(plan.scenes[0].pause_frames, 0)
        self.assertIn("scene_window_repaired", {w.code for w in plan.warnings})

    def test_empty_timeline_is_rejected(self):
        with self.assertRaises(ValueError):
            build_frame_plan({"scenes": []}, fps=FPS, width=320, height=180)

    def test_transition_offsets_land_on_scene_boundaries(self):
        scenes = [
            scene_dict(1),
            scene_dict(2, transition={"type": "fade", "duration": 0.4}),
        ]
        timeline = timeline_dict([dict(scene) for scene in scenes])
        plan = build_frame_plan(timeline, fps=FPS, width=320, height=180)

        chain = plan.transition_offsets()
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["scene_id"], 1)
        # The offset is the boundary itself: the transition runs through the
        # pause, so the next scene's narration still starts on time.
        self.assertEqual(
            round(chain[0]["offset_s"] * FPS), plan.scenes[0].end_frame
        )


# --------------------------------------------------------------------------
# Filtergraph construction (pure strings)
# --------------------------------------------------------------------------


class TestFiltergraph(unittest.TestCase):
    def test_zoompan_drives_progress_from_on_not_t(self):
        """
        `zoompan` has no `t` variable.  A graph built with `t` is accepted by
        ffmpeg and renders a completely static frame, so this asserts on the
        mechanism, not just on the output.
        """
        graph = build_zoompan_filter(
            motion={"enabled": True, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.08},
            frames=101,
            frame_size=(1920, 1080),
            fps=30,
        )
        self.assertIn("on/100", graph)
        self.assertNotIn("t/", graph)
        self.assertIn("d=101", graph)
        self.assertIn("s=1920x1080", graph)

    def test_zoom_reaches_its_end_scale_on_the_last_frame(self):
        graph = build_zoompan_filter(
            motion={"enabled": True, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.08},
            frames=101,
            frame_size=(1920, 1080),
            fps=30,
        )
        # 1 + (1.08 - 1) * on/100 -> 1.08 exactly when on == 100.
        self.assertIn("1+((1.08-1)*on/100)", graph)

    def test_pan_keeps_the_scale_and_slides_the_window(self):
        graph = build_zoompan_filter(
            motion={"enabled": True, "type": "pan_right", "start_scale": 1.1, "end_scale": 1.1},
            frames=51,
            frame_size=(1920, 1080),
            fps=30,
        )
        self.assertIn("z='1.1'", graph)
        self.assertIn("x='(iw-iw/zoom)*on/50'", graph)

    def test_disabled_motion_still_produces_a_graph(self):
        graph = build_zoompan_filter(
            motion={"enabled": False, "type": "zoom_in", "start_scale": 1.2, "end_scale": 1.4},
            frames=10,
            frame_size=(320, 180),
            fps=10,
        )
        self.assertIn("z='1'", graph)

    def test_every_overlay_is_gated_by_its_window(self):
        """
        `overlay` has no idea when a layer should be visible: without
        `enable`, a layer with no fade is composited for the whole clip.
        """
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(
                    path=Path("a.png"), start_s=0.5, end_s=2.5, animation="none"
                )
            ],
        )
        self.assertIn("enable='between(t,0.5,2.5)'", graph)

    def test_overlay_labels_are_fully_bracketed(self):
        """
        `[v0]_src` is not a label, it is a parse error, and it only shows up
        when ffmpeg is actually invoked.
        """
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(path=Path("a.png"), start_s=0.0, end_s=1.0),
                OverlayLayer(path=Path("b.png"), start_s=1.0, end_s=2.0),
            ],
        )
        for line in graph.splitlines():
            for token in line.split(","):
                self.assertNotIn("]_", token, f"malformed label in: {line}")

        self.assertIn("[0:v]", graph)
        self.assertIn("[1:v]", graph)
        self.assertIn("[2:v]", graph)
        self.assertIn("[v0]", graph)
        self.assertIn("[v1]", graph)
        self.assertIn("[out]", graph)

    def test_fades_are_applied_for_fading_animations(self):
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(
                    path=Path("a.png"),
                    start_s=1.0,
                    end_s=3.0,
                    animation="fade_in",
                    animation_duration_s=0.5,
                )
            ],
        )
        self.assertIn("fade=t=in:st=1:d=0.5:alpha=1", graph)
        self.assertIn("fade=t=out:st=2.5:d=0.5:alpha=1", graph)

    def test_no_fade_for_a_hard_on_layer(self):
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(path=Path("a.png"), start_s=1.0, end_s=3.0, animation="none")
            ],
        )
        self.assertNotIn("fade=", graph)

    def test_slide_animation_moves_the_layer(self):
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(
                    path=Path("a.png"),
                    start_s=1.0,
                    end_s=3.0,
                    animation="slide_in",
                    animation_duration_s=0.5,
                )
            ],
        )
        self.assertIn("overlay=x='if(lt(t,1),", graph)
        self.assertIn("-W+W*(t-1)/0.5", graph)

    def test_cut_in_layer_fades_out_only(self):
        """Used for the final step of a typewriter reveal."""
        graph = build_scene_filtergraph(
            frames=30,
            frame_size=(320, 180),
            fps=10,
            motion=None,
            overlays=[
                OverlayLayer(
                    path=Path("a.png"),
                    start_s=1.0,
                    end_s=3.0,
                    animation="cut_in",
                    animation_duration_s=0.5,
                )
            ],
        )
        self.assertIn("fade=t=out", graph)
        self.assertNotIn("fade=t=in", graph)

    def test_output_is_always_yuv420p(self):
        graph = build_scene_filtergraph(
            frames=10, frame_size=(320, 180), fps=10, motion=None, overlays=[]
        )
        self.assertTrue(graph.endswith("format=yuv420p[out]"))


# --------------------------------------------------------------------------
# Ken Burns metrics (pure arithmetic)
# --------------------------------------------------------------------------


class TestMotionInspection(unittest.TestCase):
    def test_zoom_travel_matches_the_visible_window_change(self):
        """1920 * (1/1.0 - 1/1.08) = 142.2px of edge travel."""
        report = inspect_motion(
            {"enabled": True, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.08},
            frames=479,
            frame_size=(1920, 1080),
        )
        self.assertAlmostEqual(report["travel_px"], 142.22, places=1)
        self.assertFalse(report["too_slow"])

    def test_pan_travel_matches_the_window_slide(self):
        """1920 * (1.10 - 1.0) = 192px across the frame."""
        report = inspect_motion(
            {"enabled": True, "type": "pan_right", "start_scale": 1.1, "end_scale": 1.1},
            frames=479,
            frame_size=(1920, 1080),
        )
        self.assertAlmostEqual(report["travel_px"], 192.0, places=1)
        self.assertEqual(report["axis"], "horizontal")

    def test_vertical_pan_uses_the_height(self):
        report = inspect_motion(
            {"enabled": True, "type": "pan_up", "start_scale": 1.1, "end_scale": 1.1},
            frames=100,
            frame_size=(1920, 1080),
        )
        self.assertAlmostEqual(report["travel_px"], 108.0, places=1)
        self.assertEqual(report["axis"], "vertical")

    def test_a_move_too_slow_to_see_is_flagged(self):
        """
        Under a quarter pixel per frame the integer positions repeat, so the
        still looks frozen however the script describes it.
        """
        report = inspect_motion(
            {"enabled": True, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.01},
            frames=1000,
            frame_size=(1920, 1080),
        )
        self.assertLess(report["travel_px_per_frame"], MIN_TRAVEL_PX_PER_FRAME)
        self.assertTrue(report["too_slow"])

    def test_disabled_motion_reports_no_travel(self):
        report = inspect_motion(
            {"enabled": False, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.4},
            frames=100,
            frame_size=(1920, 1080),
        )
        self.assertFalse(report["enabled"])
        self.assertEqual(report["travel_px"], 0.0)


# --------------------------------------------------------------------------
# Text layers (PIL only)
# --------------------------------------------------------------------------


class TestTextLayers(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_text_")
        self.dir = Path(self._tmp.name)
        from autovid.infrastructure.image.fonts import resolve_font

        self.font = resolve_font("missing.ttf", self.dir).path
        if self.font is None:
            self.skipTest("no system font available")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_colour_parsing(self):
        self.assertEqual(parse_hex_colour("#FF5733"), (255, 87, 51))
        self.assertEqual(parse_hex_colour("#F53"), (255, 85, 51))
        self.assertEqual(parse_hex_colour("bogus", default=(1, 2, 3)), (1, 2, 3))

    def test_centre_placement_is_horizontally_symmetric(self):
        layer = render_text_layer(
            text="HELLO",
            font_path=self.font,
            font_size=20,
            colour="#000000",
            stroke_colour="#000000",
            stroke_width=0,
            position="center",
            frame_size=FRAME,
            destination=self.dir / "centre.png",
        )
        image = Image.open(layer.path).getchannel("A")
        left, top, right, bottom = image.getbbox()
        self.assertAlmostEqual(
            (FRAME[0] - left) - right, 0, delta=2, msg="not centred on the frame"
        )

    def test_typewriter_prefixes_grow_from_a_fixed_left_edge(self):
        """
        Anchoring each prefix on the centre would make the growing text creep
        outwards from the middle; the left edge has to stay put.
        """
        layers = render_typewriter_layers(
            text="ABCDEFGH",
            font_path=self.font,
            font_size=20,
            colour="#000000",
            stroke_colour="#000000",
            stroke_width=0,
            position="center",
            frame_size=FRAME,
            steps=4,
            destination_dir=self.dir,
            stem="tw",
        )
        self.assertEqual(len(layers), 4)

        boxes = [Image.open(layer.path).getchannel("A").getbbox() for layer in layers]
        lefts = {box[0] for box in boxes}
        widths = [box[2] - box[0] for box in boxes]

        self.assertEqual(len(lefts), 1, f"left edge moved between prefixes: {lefts}")
        self.assertEqual(widths, sorted(widths))
        self.assertLess(widths[0], widths[-1])

    def test_typewriter_step_count_is_bounded_by_the_text(self):
        layers = render_typewriter_layers(
            text="AB",
            font_path=self.font,
            font_size=20,
            colour="#000000",
            stroke_colour="#000000",
            stroke_width=0,
            position="center",
            frame_size=FRAME,
            steps=TYPEWRITER_STEPS,
            destination_dir=self.dir,
            stem="short",
        )
        self.assertEqual(len(layers), 2)


# --------------------------------------------------------------------------
# Rendering (real ffmpeg)
# --------------------------------------------------------------------------


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")
class TestAssemblyRender(WorkspaceFixture):
    def test_exact_frame_counts_and_join_duration(self):
        """Every clip is exactly the planned number of frames, and the join
        is exactly the timeline, to the frame."""
        scenes = [
            scene_dict(1, narration_s=3.3, pause_s=0.2),
            scene_dict(2, narration_s=2.7, pause_s=0.4),
            scene_dict(3, narration_s=3.1, pause_s=0.3),
        ]
        script, paths = self.build(scenes)

        result = AssemblyStage(
            script, paths, preset="ultrafast", crf=30
        ).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(len(result.clips), 3)

        for clip, planned in zip(result.clips, result.plan.scenes):
            self.assertEqual(clip.frames, planned.segment_frames)
            self.assertEqual(frame_count(clip.clip), planned.segment_frames)
            self.assertAlmostEqual(
                probe_duration(clip.clip),
                planned.segment_frames / FPS,
                places=3,
            )

        self.assertAlmostEqual(
            probe_duration(result.preview), result.plan.total_duration_s, places=3
        )

    def test_transition_join_keeps_the_timeline(self):
        """
        The failure this guards against: a crossfade that overlaps two clips
        shortens the video by the transition length, so ten dissolves pull
        the picture four seconds ahead of the voice.
        """
        scenes = [
            scene_dict(1, narration_s=2.0, pause_s=0.5),
            scene_dict(
                2,
                narration_s=2.0,
                pause_s=0.5,
                transition={"type": "fade", "duration": 0.4},
            ),
            scene_dict(
                3,
                narration_s=2.0,
                pause_s=0.5,
                transition={"type": "slide_left", "duration": 0.4},
            ),
        ]
        script, paths = self.build(scenes)

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertGreater(result.plan.overlapped_frames, 0)
        self.assertAlmostEqual(
            probe_duration(result.preview), result.plan.total_duration_s, places=3
        )

    def test_cut_then_transition_join(self):
        """
        Regression: a cut *before* a transition.

        The joins have to share a timebase and a declared frame rate, and the
        two producers disagree -- a decoded clip arrives on its own stream
        timebase, while `concat` outputs 1/1000000 and reports its frame rate
        as 1/0.  A graph of cuts only, or of transitions only, is happy; the
        moment a concat result feeds an xfade, ffmpeg rejects it with "First
        input link main timebase do not match" and then "The inputs needs to
        be a constant frame rate".
        """
        scenes = [
            scene_dict(1, narration_s=1.5, pause_s=0.5),
            scene_dict(2, narration_s=1.5, pause_s=0.5),
            scene_dict(3, narration_s=1.5, pause_s=0.5),
            scene_dict(
                4,
                narration_s=1.5,
                pause_s=0.5,
                transition={"type": "fade", "duration": 0.4},
            ),
        ]
        script, paths = self.build(scenes)

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertEqual(len(result.plan.transition_offsets()), 1)
        self.assertAlmostEqual(
            probe_duration(result.preview), result.plan.total_duration_s, places=3
        )

    def test_transition_after_a_long_run_of_cuts(self):
        """A long concat chain feeding an xfade, which is the shape a real
        script has: cut, cut, cut ... fade."""
        scenes = [scene_dict(i, narration_s=1.0, pause_s=0.4) for i in range(1, 9)]
        scenes[-1] = scene_dict(
            8,
            narration_s=1.0,
            pause_s=0.4,
            transition={"type": "fade", "duration": 0.4},
        )
        script, paths = self.build(scenes)

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertAlmostEqual(
            probe_duration(result.preview), result.plan.total_duration_s, places=3
        )

    def test_second_run_reuses_every_clip(self):
        scenes = [scene_dict(1, narration_s=1.5), scene_dict(2, narration_s=1.5)]
        script, paths = self.build(scenes)

        first = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()
        self.assertEqual(first.report["totals"]["clips_rendered"], 2)

        second = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()
        self.assertEqual(second.report["totals"]["clips_rendered"], 0)
        self.assertEqual(second.report["totals"]["clips_cached"], 2)

    def test_changing_the_quality_re_renders(self):
        """A cache key that ignores encoder settings would silently serve
        clips the user did not ask for."""
        scenes = [scene_dict(1, narration_s=1.5)]
        script, paths = self.build(scenes)

        AssemblyStage(script, paths, preset="ultrafast", crf=30).run()
        result = AssemblyStage(script, paths, preset="ultrafast", crf=23).run()
        self.assertEqual(result.report["totals"]["clips_rendered"], 1)

    def test_dry_run_encodes_nothing(self):
        scenes = [scene_dict(1, narration_s=1.5), scene_dict(2, narration_s=1.5)]
        script, paths = self.build(scenes)

        result = AssemblyStage(script, paths, dry_run=True).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIsNone(result.preview)
        self.assertEqual(result.report["totals"]["clips_rendered"], 0)
        self.assertEqual(len(result.report["scenes"]), 2)
        self.assertFalse(any(paths.segments_dir.glob("*.mp4")))

    def test_missing_timeline_is_an_error_not_a_crash(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes)
        (paths.output_dir / "timeline.json").unlink()

        result = AssemblyStage(script, paths).run()

        self.assertEqual(result.status, "fail")
        self.assertIn("timeline_missing", {issue.code for issue in result.issues})

    def test_missing_image_is_reported_with_its_scene(self):
        scenes = [scene_dict(1)]
        script, paths = self.build(scenes, draw_images=False)

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "fail")
        issue = next(i for i in result.issues if i.code == "image_missing")
        self.assertEqual(issue.scene_id, 1)


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")
class TestRenderedContent(WorkspaceFixture):
    """
    Read the pixels back out.

    A filtergraph that renders a valid video with no text in it is the
    failure mode this class exists for; nothing above this line would catch
    it, because every artefact ffmpeg produces is well formed.
    """

    def _render(self, scenes: list[dict]):
        script, paths = self.build(scenes)
        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()
        self.assertEqual(result.status, "pass", result.issues)
        return result, paths

    def test_overlay_appears_inside_its_window_and_not_outside(self):
        scenes = [
            scene_dict(
                1,
                narration_s=3.0,
                pause_s=0.0,
                overlays=[overlay_dict(start_ms=500, end_ms=2500, animation="none")],
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")

        counts = [dark_pixels(frame) for frame in frames]
        # 0.5s -> frame 5, 2.5s -> frame 25 at 10fps.
        self.assertEqual(sum(counts[:5]), 0, "text visible before its window")
        self.assertGreater(counts[15], 0, "text missing inside its window")
        self.assertEqual(sum(counts[26:]), 0, "text still visible after its window")

    def test_fade_in_ramps_rather_than_snapping(self):
        """
        A still image is one frame at t=0, so `fade` has no timeline to act
        on unless the layer is looped into a real stream; when that is wrong
        the text never appears at all.

        Measured as the darkest pixel, not a count: at half opacity the text
        is mid grey, which a dark-pixel count cannot see at all.
        """
        scenes = [
            scene_dict(
                1,
                narration_s=3.0,
                pause_s=0.0,
                overlays=[overlay_dict(start_ms=500, end_ms=2500, animation_ms=800)],
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")

        # Fade in occupies 0.5s -> 1.3s, so frames 5..13.
        ramp = [darkest(frame) for frame in frames[6:14]]
        self.assertEqual(ramp, sorted(ramp, reverse=True), f"not a ramp: {ramp}")
        self.assertGreater(ramp[0], ramp[-1], "the text never faded in")
        self.assertLessEqual(ramp[-1], 5, "the text never reached full opacity")

    def test_ken_burns_actually_moves_the_picture(self):
        scenes = [
            scene_dict(
                1,
                narration_s=4.0,
                pause_s=0.0,
                ken_burns={
                    "enabled": True,
                    "type": "zoom_in",
                    "start_scale": 1.0,
                    "end_scale": 1.5,
                },
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")

        first = Image.open(frames[0]).convert("L")
        last = Image.open(frames[-1]).convert("L")
        difference = sum(
            abs(a - b) for a, b in zip(first.getdata(), last.getdata())
        ) / (first.width * first.height)
        self.assertGreater(
            difference, 1.0, "the first and last frames are the same picture"
        )

        # And the zoom really zooms in: the shape grows.
        def ink(image):
            return sum(1 for value in image.getdata() if value < 240)

        self.assertGreater(ink(last), ink(first))

    def test_disabled_motion_leaves_the_picture_still(self):
        scenes = [
            scene_dict(
                1,
                narration_s=2.0,
                pause_s=0.0,
                ken_burns={
                    "enabled": False,
                    "type": "zoom_in",
                    "start_scale": 1.0,
                    "end_scale": 1.5,
                },
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")

        first = Image.open(frames[0]).convert("L")
        last = Image.open(frames[-1]).convert("L")
        difference = sum(
            abs(a - b) for a, b in zip(first.getdata(), last.getdata())
        ) / (first.width * first.height)
        self.assertLess(difference, 1.0, "a disabled effect still moved the frame")

    def test_typewriter_reveals_more_text_over_time(self):
        scenes = [
            scene_dict(
                1,
                narration_s=4.0,
                pause_s=0.0,
                overlays=[
                    overlay_dict(
                        text="ABCDEFGHIJ",
                        start_ms=500,
                        end_ms=3500,
                        animation="typewriter",
                        animation_ms=1600,
                        size=20,
                    )
                ],
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")
        counts = [dark_pixels(frame) for frame in frames]

        # Sample the reveal window: 0.5s -> 2.1s at 10fps.
        reveal = [counts[i] for i in (7, 11, 15, 19)]
        self.assertEqual(reveal, sorted(reveal), f"reveal not monotonic: {reveal}")
        self.assertLess(reveal[0], reveal[-1], "the text never grew")
        self.assertGreater(reveal[-1], 0, "the text never appeared")

    def test_slide_in_moves_the_text_into_frame(self):
        scenes = [
            scene_dict(
                1,
                narration_s=4.0,
                pause_s=0.0,
                overlays=[
                    overlay_dict(
                        text="SLIDE",
                        start_ms=500,
                        end_ms=3500,
                        animation="slide_in",
                        animation_ms=1000,
                        size=20,
                    )
                ],
            )
        ]
        result, paths = self._render(scenes)
        frames = extract_frames(result.clips[0].clip, paths.cache_dir / "frames")

        # 0.5s -> 1.5s is the slide.  Frame 13 is mid-travel, frame 20 has
        # arrived; the text's centre moves rightwards between them.
        travelling = dark_centroid_x(frames[13])
        settled = dark_centroid_x(frames[20])
        self.assertIsNotNone(travelling, "the text was never visible while sliding")
        self.assertIsNotNone(settled, "the text never arrived")
        self.assertLess(
            travelling, settled, "the text did not travel rightwards"
        )
        self.assertGreater(
            settled, FRAME[0] * 0.3, "the settled text is not near the centre"
        )

    def test_missing_font_falls_back_and_says_so(self):
        """A font typo must degrade to the system font and be reported, not
        stop the render or silently drop the text."""
        scenes = [
            scene_dict(
                1,
                narration_s=2.0,
                pause_s=0.0,
                overlays=[overlay_dict(start_ms=100, end_ms=1500)],
            )
        ]
        script, paths = self.build(scenes, provide_font=False)
        if find_system_font() is None:
            self.skipTest("no system font to fall back to")

        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "pass", result.issues)
        self.assertIn("overlay_font_fallback", {i.code for i in result.issues})
        self.assertFalse(result.report["scenes"][0]["overlays"][0].get("dropped", False))

    def test_overlay_outside_the_clip_is_dropped_and_reported(self):
        """
        The script asks for text until 20s inside a 1s scene.  Clamping is
        right, but silently pretending it fits is not.
        """
        scenes = [
            scene_dict(
                1,
                narration_s=1.0,
                pause_s=0.0,
                overlays=[overlay_dict(start_ms=19000, end_ms=20000)],
            )
        ]
        script, paths = self.build(scenes)
        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertEqual(result.status, "pass", result.issues)
        plan = result.report["scenes"][0]["overlays"][0]
        self.assertTrue(plan["dropped"])
        self.assertIn("overlay_outside_clip", {i.code for i in result.issues})

    def test_minimum_overlay_window_is_honoured(self):
        """Below the readable floor the overlay is dropped, not rendered."""
        scenes = [
            scene_dict(
                1,
                narration_s=2.0,
                pause_s=0.0,
                overlays=[
                    overlay_dict(start_ms=0, end_ms=int(MIN_OVERLAY_SECONDS * 1000) - 100)
                ],
            )
        ]
        script, paths = self.build(scenes)
        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        self.assertTrue(result.report["scenes"][0]["overlays"][0]["dropped"])

    def test_typewriter_becomes_a_stack_of_spans(self):
        scenes = [
            scene_dict(
                1,
                narration_s=4.0,
                pause_s=0.0,
                overlays=[
                    overlay_dict(
                        text="ABCDEFGH",
                        animation="typewriter",
                        animation_ms=1500,
                        start_ms=500,
                        end_ms=3500,
                    )
                ],
            )
        ]
        result, paths = self._render(scenes)
        plan = result.report["scenes"][0]["overlays"][0]

        self.assertEqual(plan["animation"], "typewriter")
        self.assertEqual(plan["span_count"], 8)
        self.assertEqual(plan["window_s"], [0.5, 3.5])

    def test_overlay_window_is_clamped_to_the_clip(self):
        scenes = [
            scene_dict(
                1,
                narration_s=2.0,
                pause_s=0.0,
                overlays=[overlay_dict(start_ms=0, end_ms=9000)],
            )
        ]
        result, paths = self._render(scenes)
        self.assertEqual(
            result.report["scenes"][0]["overlays"][0]["window_s"], [0.0, 2.0]
        )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")
class TestAssemblyReport(WorkspaceFixture):
    def test_join_reports_planned_against_measured(self):
        """The delta that says whether the join kept the timeline belongs in
        the report, not only in a log line."""
        scenes = [
            scene_dict(1, narration_s=1.5),
            scene_dict(
                2,
                narration_s=1.5,
                transition={"type": "fade", "duration": 0.4},
            ),
        ]
        script, paths = self.build(scenes)
        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        join = result.report["join"]
        self.assertEqual(join["planned_duration_s"], result.plan.total_duration_s)
        self.assertIsNotNone(join["measured_duration_s"])
        self.assertLessEqual(abs(join["delta_s"]), 1 / FPS)

    def test_report_is_written_and_says_what_it_did(self):
        scenes = [
            scene_dict(
                1,
                narration_s=1.5,
                overlays=[overlay_dict(start_ms=200, end_ms=1200)],
            ),
            scene_dict(2, narration_s=1.5),
        ]
        script, paths = self.build(scenes)
        result = AssemblyStage(script, paths, preset="ultrafast", crf=30).run()

        report_path = paths.output_dir / "assembly_report.json"
        self.assertTrue(report_path.exists())
        report = read_json(report_path)

        self.assertEqual(report["stage"], "assembly")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["totals"]["scenes"], 2)
        self.assertEqual(report["totals"]["overlays"], 1)
        self.assertEqual(report["settings"]["preset"], "ultrafast")
        self.assertEqual(report["preview"]["has_audio"], False)
        self.assertEqual(report["preview"]["duration_s"], result.plan.total_duration_s)
        self.assertEqual(len(report["scenes"]), 2)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["errors"], [])

    def test_concat_path_escaping_handles_apostrophes(self):
        """A workspace under a name like "Bob's videos" must not break the
        concat demuxer, which treats a bare quote as a delimiter."""
        escaped = _escape_concat_path(Path("/tmp/Bob's videos/a.mp4"))
        self.assertIn(r"'\''", escaped)
        self.assertTrue(escaped.startswith("/"))
