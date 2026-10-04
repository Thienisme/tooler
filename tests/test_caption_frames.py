"""Captions that wrap, and the boxes they sit in.

A caption is a caption until somebody is talking: the box is what turns text
into a scene.  These tests hold the three things that could go wrong --
wrapping that runs off the frame, a box that does not fit inside it, and a
typewriter reveal that re-centres itself on every keystroke -- plus the
studio controls that pick all of it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image, ImageFont  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autovid.application.assembly import _overlay_key  # noqa: E402
from autovid.domain.script import (  # noqa: E402
    ScriptSchemaError,
    parse_script,
)
from autovid.infrastructure.image.fonts import (  # noqa: E402
    fit_overlay_font_size,
    measure_block,
    wrap_lines,
)
from autovid.infrastructure.video.text import (  # noqa: E402
    TEXT_FRAMES,
    bubble_inset,
    layout_text,
    render_text_layer,
    render_typewriter_layers,
    tail_direction,
)

_APP = QApplication.instance() or QApplication([])

FONT = REPO_ROOT / "assets/fonts/BeVietnamPro-Regular.ttf"
FRAME_SIZE = (1280, 720)


def _script(overlay: dict) -> dict:
    return {
        "video_metadata": {
            "title": "t",
            "author": "Buffy",
            "resolution": "1280x720",
            "fps": 30,
        },
        "tts_config": {"voice": "test", "speed": 1.0},
        "scenes": [
            {
                "id": 1,
                "text": "Mot.",
                "image_file": (
                    "projects/demo_story_inside/images/backgrounds/image.png"
                ),
                "text_overlays": [overlay],
            }
        ],
    }


def _alpha_box(path: Path) -> tuple[int, int, int, int]:
    """The box everything drawn on a layer covers."""
    with Image.open(path) as image:
        return image.getbbox() or (0, 0, 0, 0)


class CaptionFrameSchemaTest(unittest.TestCase):
    def test_a_caption_gets_no_box_by_default(self):
        script = parse_script(_script({"text": "Oi"}))
        overlay = script.scenes[0].text_overlays[0]
        self.assertEqual(overlay.frame, "none")
        self.assertEqual(overlay.frame_fill, "#FFFFFF")
        self.assertEqual(overlay.frame_stroke, "#101010")

    def test_every_box_is_accepted(self):
        for kind in TEXT_FRAMES:
            with self.subTest(frame=kind):
                script = parse_script(
                    _script({"text": "Oi", "frame": kind, "frame_fill": "#FFEE99"})
                )
                overlay = script.scenes[0].text_overlays[0]
                self.assertEqual(overlay.frame, kind)
                self.assertEqual(overlay.frame_fill, "#FFEE99")

    def test_a_box_that_does_not_exist_is_rejected(self):
        with self.assertRaises(ScriptSchemaError):
            parse_script(_script({"text": "Oi", "frame": "comic"}))


class WrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.font = ImageFont.truetype(str(FONT), 72)

    def test_short_text_stays_on_one_line(self):
        self.assertEqual(
            wrap_lines("mot", self.font, 900), ["mot"]
        )

    def test_long_text_wraps_inside_the_room(self):
        lines = wrap_lines("mot hai", self.font, 150)
        self.assertEqual(lines, ["mot", "hai"])
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 150)

    def test_a_word_longer_than_a_line_is_broken(self):
        lines = wrap_lines("a" * 60, self.font, 300)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 300)

    def test_a_word_so_long_it_cannot_fit_still_terminates(self):
        """A one-pixel room must not hang a 13 minute render."""
        for room in (1, 2, 5, 17):
            with self.subTest(room=room):
                lines = wrap_lines("mot hai ba", self.font, room)
                self.assertTrue(lines)

    def test_intentional_line_breaks_survive_the_width(self):
        lines = wrap_lines("mot hai\nmot ba", self.font, 900)
        self.assertEqual(lines, ["mot hai", "mot ba"])

    def test_the_stroke_counts_against_the_width(self):
        wide = wrap_lines("mot hai", self.font, 150, stroke_width=0)
        narrow = wrap_lines("mot hai", self.font, 150, stroke_width=12)
        self.assertGreaterEqual(len(narrow), len(wide))

    def test_a_block_is_taller_than_one_line_when_it_wraps(self):
        single = layout_text("mot", self.font)
        wrapped = layout_text("mot hai", self.font, max_width=150)
        self.assertLess(single.height, single.line_height)
        self.assertGreater(wrapped.height, single.height + wrapped.line_height / 2)

    def test_the_measurement_matches_what_is_drawn(self):
        """The fit report and the renderer must agree on the block's size."""
        block = layout_text(
            "mot hai", self.font, stroke_width=3, max_width=150
        )
        width, height = measure_block(
            "mot hai", FONT, 72, max_width=150, stroke_width=3
        )
        self.assertEqual(width, block.width)
        self.assertEqual(height, block.height)

    def test_a_caption_that_wraps_no_longer_says_it_was_shrunk(self):
        """Wrapping is the answer; shrinking the type is the fallback."""
        text = "mot hai"
        size = fit_overlay_font_size(
            text, FONT, requested_size=72, position="center",
            frame_size=FRAME_SIZE, stroke_width=3,
        )
        self.assertEqual(size, 72)


class BubbleRenderTest(unittest.TestCase):
    """The pixels, not the plan: what the renderer actually draws."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_bubble_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.common = {
            # Short enough to stay on one line, so the plain caption and the
            # boxed one wrap identically and the comparison is about the box.
            "text": "Bai 3 - cong lang",
            "font_path": FONT,
            "font_size": 72,
            "colour": "#111111",
            "stroke_colour": "#000000",
            "stroke_width": 3,
            "position": "top_left",
            "frame_size": FRAME_SIZE,
            "max_width": 900,
        }

    def render(self, kind: str, **extra) -> Path:
        destination = self.out / f"{kind}.png"
        render_text_layer(
            **self.common,
            destination=destination,
            frame=kind,
            frame_fill=extra.pop("frame_fill", "#FFFFFF"),
            frame_stroke=extra.pop("frame_stroke", "#101010"),
            **extra,
        )
        return destination

    def test_a_box_reserves_its_own_padding(self):
        """A boxed caption wraps narrower, so the box never clips the words."""
        plain = layout_text("Bai 3 - cong lang", ImageFont.truetype(str(FONT), 72))
        boxed_width = 900 - 2 * bubble_inset(72, "speech")
        boxed = layout_text(
            "Bai 3 - cong lang o trong",
            ImageFont.truetype(str(FONT), 72),
            max_width=boxed_width,
        )
        self.assertLessEqual(boxed.width, boxed_width)
        self.assertGreater(plain.width, 0)

    def test_a_bubble_is_wider_and_taller_than_its_words(self):
        plain = _alpha_box(self.render("none"))
        speech = _alpha_box(self.render("speech"))
        self.assertGreater(
            speech[2] - speech[0], plain[2] - plain[0]
        )
        self.assertGreater(speech[3] - speech[1], plain[3] - plain[1])

    def test_the_three_boxes_are_different_shapes(self):
        """Otherwise the choice is a lie and the catalogue is decoration."""
        speech = _alpha_box(self.render("speech"))
        thought = _alpha_box(self.render("thought"))
        shout = _alpha_box(self.render("shout"))
        self.assertNotEqual(speech, thought)
        self.assertNotEqual(speech, shout)
        self.assertNotEqual(thought, shout)
        # A starburst reaches past the box it is cut out of; a rounded box
        # does not.
        self.assertGreater(shout[2] - shout[0], speech[2] - speech[0])
        self.assertGreater(shout[3] - shout[1], speech[3] - speech[1])

    def test_the_tail_points_away_from_the_nearest_edge(self):
        self.assertEqual(tail_direction("top"), "up")
        self.assertEqual(tail_direction("bottom"), "down")
        self.assertEqual(tail_direction("top_left"), "up")
        self.assertEqual(tail_direction("center"), "down")

    def test_the_tail_is_drawn_below_a_caption_near_the_bottom(self):
        near_bottom = dict(self.common, position="bottom")
        destination = self.out / "tail.png"
        render_text_layer(
            **near_bottom, destination=destination, frame="speech"
        )
        left, top, right, bottom = _alpha_box(destination)
        # The block itself sits above the tail, so there is ink lower than
        # the words: the bubble plus a point.
        self.assertGreater(bottom, FRAME_SIZE[1] * 0.6)

    def test_a_bubble_stays_inside_the_frame(self):
        for kind in ("speech", "thought", "shout"):
            with self.subTest(frame=kind):
                destination = self.out / f"edge_{kind}.png"
                render_text_layer(
                    **dict(self.common, position="bottom_left"),
                    destination=destination,
                    frame=kind,
                )
                left, top, right, bottom = _alpha_box(destination)
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(right, FRAME_SIZE[0])
                self.assertLessEqual(bottom, FRAME_SIZE[1])

    def test_the_padding_is_reserved_for_the_box(self):
        self.assertEqual(bubble_inset(72, "none"), 0)
        self.assertGreater(bubble_inset(72, "speech"), 20)
        self.assertGreater(
            bubble_inset(72, "shout"), bubble_inset(72, "speech")
        )

    def test_the_fill_is_the_colour_that_was_asked_for(self):
        destination = self.out / "yellow.png"
        render_text_layer(
            **self.common,
            destination=destination,
            frame="speech",
            frame_fill="#FFE680",
        )
        with Image.open(destination) as image:
            colours = {
                pixel[:3]
                for pixel in image.convert("RGBA").getdata()
                if pixel[3] > 200
            }
        self.assertIn((255, 230, 128), colours)


class TypewriterBlockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_tw_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _layers(self, **extra) -> list:
        return render_typewriter_layers(
            text="mot hai ba",
            font_path=FONT,
            font_size=72,
            colour="#111111",
            stroke_colour="#000000",
            stroke_width=3,
            position="top_left",
            frame_size=FRAME_SIZE,
            max_width=900,
            steps=5,
            destination_dir=self.out,
            stem="tw",
            **extra,
        )

    def test_the_reveal_ends_on_the_whole_caption(self):
        layers = self._layers()
        self.assertEqual(len(layers), 5)
        last = _alpha_box(layers[-1].path)
        plain = self.out / "plain.png"
        render_text_layer(
            text="mot hai ba",
            font_path=FONT,
            font_size=72,
            colour="#111111",
            stroke_colour="#000000",
            stroke_width=3,
            position="top_left",
            frame_size=FRAME_SIZE,
            max_width=900,
            destination=plain,
        )
        self.assertEqual(last, _alpha_box(plain))

    def test_the_caption_grows(self):
        layers = self._layers()
        widths = [
            _alpha_box(layer.path)[2] - _alpha_box(layer.path)[0]
            for layer in layers
        ]
        self.assertEqual(widths, sorted(widths))
        self.assertGreater(widths[-1], widths[0])

    def test_the_caption_does_not_move_while_it_is_typed(self):
        """Re-centring on every keystroke reads as a wobble, not a reveal."""
        layers = self._layers()
        tops = {_alpha_box(layer.path)[1] for layer in layers}
        self.assertEqual(len(tops), 1)

    def test_a_bubble_is_drawn_whole_from_the_first_keystroke(self):
        """A box that grows as it is typed reads as being written."""
        layers = self._layers(frame="speech")
        first = _alpha_box(layers[0].path)
        last = _alpha_box(layers[-1].path)
        self.assertEqual(first[1], last[1])
        self.assertEqual(first[0], last[0])


class OverlayCacheKeyTest(unittest.TestCase):
    """A retuned box must not reuse the last one's pixels."""

    def _key(self, overlay: dict) -> str:
        scene = parse_script(_script(overlay)).scenes[0]
        return _overlay_key(scene, 0, FONT, FRAME_SIZE, 72)

    def test_two_different_boxes_are_two_different_layers(self):
        plain = self._key({"text": "Oi"})
        speech = self._key({"text": "Oi", "frame": "speech"})
        thought = self._key({"text": "Oi", "frame": "thought"})
        self.assertNotEqual(plain, speech)
        self.assertNotEqual(speech, thought)

    def test_the_box_colours_are_part_of_the_layer(self):
        white = self._key({"text": "Oi", "frame": "speech", "frame_fill": "#FFFFFF"})
        yellow = self._key({"text": "Oi", "frame": "speech", "frame_fill": "#FFE680"})
        self.assertNotEqual(white, yellow)

    def test_the_same_box_is_the_same_layer(self):
        first = self._key({"text": "Oi", "frame": "speech"})
        second = self._key({"text": "Oi", "frame": "speech"})
        self.assertEqual(first, second)


class FreePositionTest(unittest.TestCase):
    """A caption the author put somewhere themselves, rather than in a corner."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_free_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _render(self, position_xy, **extra) -> tuple:
        destination = self.out / f"free_{id(position_xy)}.png"
        render_text_layer(
            text="Ong lang",
            font_path=FONT,
            font_size=72,
            colour="#111111",
            stroke_colour="#000000",
            stroke_width=3,
            position="bottom",
            frame_size=FRAME_SIZE,
            destination=destination,
            max_width=900,
            position_xy=position_xy,
            **extra,
        )
        return destination

    def test_a_caption_has_no_free_position_by_default(self):
        script = parse_script(_script({"text": "Oi"}))
        overlay = script.scenes[0].text_overlays[0]
        self.assertIsNone(overlay.x)
        self.assertIsNone(overlay.y)

    def test_a_caption_may_name_where_it_sits(self):
        script = parse_script(_script({"text": "Oi", "x": 0.25, "y": 0.75}))
        overlay = script.scenes[0].text_overlays[0]
        self.assertEqual(overlay.x, 0.25)
        self.assertEqual(overlay.y, 0.75)

    def test_half_a_position_is_refused(self):
        """One coordinate is not a place; it is a typo."""
        for payload in ({"x": 0.3}, {"y": 0.4}):
            with self.subTest(payload=payload):
                with self.assertRaises(ScriptSchemaError):
                    parse_script(_script({"text": "Oi", **payload}))

    def test_a_position_outside_the_frame_is_refused(self):
        for payload in ({"x": 1.4, "y": 0.5}, {"x": 0.5, "y": -0.2}):
            with self.subTest(payload=payload):
                with self.assertRaises(ScriptSchemaError):
                    parse_script(_script({"text": "Oi", **payload}))

    def test_the_caption_is_centred_on_the_coordinates_it_names(self):
        path = self._render((0.25, 0.75))
        left, top, right, bottom = _alpha_box(path)
        centre_x = (left + right) / 2
        centre_y = (top + bottom) / 2
        self.assertAlmostEqual(centre_x, 0.25 * FRAME_SIZE[0], delta=4)
        self.assertAlmostEqual(centre_y, 0.75 * FRAME_SIZE[1], delta=4)

    def test_two_positions_are_two_different_layers(self):
        scene = parse_script(
            _script({"text": "Oi", "x": 0.25, "y": 0.25})
        ).scenes[0]
        first = _overlay_key(scene, 0, FONT, FRAME_SIZE, 72)
        scene = parse_script(
            _script({"text": "Oi", "x": 0.75, "y": 0.25})
        ).scenes[0]
        second = _overlay_key(scene, 0, FONT, FRAME_SIZE, 72)
        self.assertNotEqual(first, second)

    def test_a_placed_caption_stays_inside_the_frame(self):
        path = self._render((0.5, 0.5), frame="speech")
        left, top, right, bottom = _alpha_box(path)
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(right, FRAME_SIZE[0])
        self.assertLessEqual(bottom, FRAME_SIZE[1])


class StudioFreePositionTest(unittest.TestCase):
    """The panel and the drag, which is where a free position comes from."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_freeui_")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        project_dir = root / "video_a"
        (project_dir / "images/backgrounds").mkdir(parents=True)
        Image.new("RGB", (320, 180), (90, 90, 90)).save(
            project_dir / "images/backgrounds/cong_lang.png"
        )
        script = project_dir / "script.json"
        script.write_text(
            json.dumps(
                {
                    "video_metadata": {
                        "title": "t",
                        "resolution": "1920x1080",
                        "fps": 30,
                    },
                    "scenes": [
                        {
                            "id": 1,
                            "text": "Mot",
                            "image_file": "images/backgrounds/cong_lang.png",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        from autovid.presentation.studio.assets import AssetLibrary
        from autovid.presentation.studio.canvas import StageCanvas
        from autovid.presentation.studio.overlays import OverlayPanel
        from autovid.presentation.studio.store import ProjectStore, TextOverlay

        self.project = ProjectStore.open(script)
        self.canvas = StageCanvas(AssetLibrary(project_dir, REPO_ROOT))
        self.canvas.resize(800, 450)
        self.canvas.set_project(self.project)
        self.canvas.set_scene(self.project.scenes[0])
        self.project.scenes[0].overlays.append(TextOverlay(text="Ong lang"))
        self.canvas.select_overlay(0)
        self.canvas.progress = 0.5
        self.panel = OverlayPanel(self.canvas)
        self.panel.reload()

    def overlay(self):
        return self.project.scenes[0].overlays[0]

    def test_the_coordinates_start_locked(self):
        self.assertFalse(self.panel.free_position.isChecked())
        self.assertIsNone(self.overlay().x)
        self.assertIsNone(self.overlay().y)

    def test_ticking_the_box_gives_the_caption_a_position(self):
        self.panel.free_position.setChecked(True)
        self.assertIsNotNone(self.overlay().x)
        self.assertIsNotNone(self.overlay().y)
        self.assertTrue(self.panel.free_x.isEnabled())

    def test_unticking_it_gives_the_position_back(self):
        self.panel.free_position.setChecked(True)
        self.panel.free_position.setChecked(False)
        self.assertIsNone(self.overlay().x)
        self.assertIsNone(self.overlay().y)

    def test_a_caption_with_no_free_position_saves_no_coordinate(self):
        from autovid.presentation.studio.store import TextOverlay

        self.assertNotIn("x", TextOverlay(text="Oi").to_dict())

    def test_a_placed_caption_saves_its_coordinates(self):
        from autovid.presentation.studio.store import TextOverlay

        self.panel.free_position.setChecked(True)
        self.panel.free_x.setValue(0.33)
        self.panel.free_y.setValue(0.66)
        data = self.overlay().to_dict()
        self.assertAlmostEqual(data["x"], 0.33, places=4)
        self.assertAlmostEqual(data["y"], 0.66, places=4)
        restored = TextOverlay.from_dict(data)
        self.assertAlmostEqual(restored.x, 0.33, places=4)
        self.assertAlmostEqual(restored.y, 0.66, places=4)

    def test_the_caption_can_be_found_and_grabbed(self):
        stage = self.canvas._stage_rect()
        layout = self.canvas.overlay_layout(
            self.overlay(), stage, self._overlay_state()
        )
        self.assertIsNotNone(layout)
        _font, _lines, box, _padding, _line_height = layout
        self.assertEqual(
            self.canvas.overlay_at(box.center()), 0
        )
        self.assertIsNone(self.canvas.overlay_at(QPointF(1, 1)))

    def test_dragging_the_caption_moves_it(self):
        from PySide6.QtCore import QPointF

        stage = self.canvas._stage_rect()
        layout = self.canvas.overlay_layout(
            self.overlay(), stage, self._overlay_state()
        )
        assert layout is not None
        _font, _lines, box, _padding, _line_height = layout
        self.canvas.select_overlay(0)
        self.canvas._drag_overlay = 0
        self.canvas._grab_offset = (0.0, 0.0)
        before = self.overlay().x
        self.canvas._move_overlay(
            QPointF(box.center().x() + 80, box.center().y() + 40)
        )
        # The drag is what gives an anchored caption a free position.
        self.assertIsNone(before)
        self.assertIsNotNone(self.overlay().x)
        self.assertGreater(self.overlay().x, 0.5)
        # And the caption followed the cursor rather than teleporting to it.
        moved = self.canvas.overlay_layout(
            self.overlay(), stage, self._overlay_state()
        )
        assert moved is not None
        self.assertAlmostEqual(
            moved[2].center().x(), box.center().x() + 80, delta=2
        )

    def _overlay_state(self):
        from autovid.presentation.studio.preview import OverlayState

        return OverlayState(text="Ong lang", opacity=1.0, dx=0.0)


class StudioCaptionBoxTest(unittest.TestCase):
    """The panel: the box is offered, and picking it reaches the store."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_boxui_")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        project_dir = self.root / "video_a"
        (project_dir / "images/backgrounds").mkdir(parents=True)
        Image.new("RGB", (320, 180), (90, 90, 90)).save(
            project_dir / "images/backgrounds/cong_lang.png"
        )
        script = project_dir / "script.json"
        script.write_text(
            json.dumps(
                {
                    "video_metadata": {
                        "title": "t",
                        "resolution": "1920x1080",
                        "fps": 30,
                    },
                    "scenes": [
                        {
                            "id": 1,
                            "text": "Mot",
                            "image_file": "images/backgrounds/cong_lang.png",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        from autovid.presentation.studio.assets import AssetLibrary
        from autovid.presentation.studio.canvas import StageCanvas
        from autovid.presentation.studio.overlays import OverlayPanel
        from autovid.presentation.studio.store import ProjectStore, TextOverlay

        self.project = ProjectStore.open(script)
        self.canvas = StageCanvas(AssetLibrary(project_dir, REPO_ROOT))
        self.canvas.resize(800, 450)
        self.canvas.set_project(self.project)
        self.canvas.set_scene(self.project.scenes[0])
        self.project.scenes[0].overlays.append(TextOverlay(text="Chu moi"))
        self.canvas.select_overlay(0)
        self.panel = OverlayPanel(self.canvas)
        self.panel.reload()

    def test_every_box_is_offered(self):
        offered = {
            self.panel.frame.itemText(index)
            for index in range(self.panel.frame.count())
        }
        self.assertEqual(offered, set(TEXT_FRAMES))

    def test_choosing_a_box_reaches_the_store(self):
        self.panel.frame.setCurrentText("thought")
        self.assertEqual(
            self.project.scenes[0].overlays[0].frame, "thought"
        )

    def test_a_caption_saved_without_a_box_grows_no_key(self):
        from autovid.presentation.studio.store import ProjectStore, TextOverlay

        self.assertNotIn("frame", TextOverlay(text="Chu").to_dict())

    def test_a_caption_saved_with_a_box_keeps_its_colours(self):
        from autovid.presentation.studio.store import TextOverlay

        self.panel.frame.setCurrentText("speech")
        self.panel.reload()
        data = self.project.scenes[0].overlays[0].to_dict()
        self.assertEqual(data["frame"], "speech")
        self.assertIn("frame_fill", data)
        self.assertIn("frame_stroke", data)
        # And it survives a round trip through the file format.
        self.assertEqual(
            TextOverlay.from_dict(data).frame, "speech"
        )

    def test_the_canvas_draws_more_ink_for_a_box(self):
        """The preview has to show the box, not just remember it."""
        overlay = self.project.scenes[0].overlays[0]
        overlay.position = "center"
        self.canvas.progress = 0.5

        def picture() -> bytes:
            self.canvas.repaint()
            image = self.canvas.grab().toImage()
            return bytes(image.constBits())

        plain = picture()
        overlay.frame = "speech"
        with_box = picture()
        self.assertNotEqual(plain, with_box)


if __name__ == "__main__":
    unittest.main()