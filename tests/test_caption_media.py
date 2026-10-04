"""A caption can be a picture: the schema, the plan, the pixels, the panel.

An image overlay is the one place the pipeline stops baking anything -- the
file goes into ffmpeg as itself -- which is the whole appeal and also the
place where two things can quietly go wrong: the GIF stops looping (the
`gif` demuxer has no `-framerate`, so `-loop 1` does not apply to it), and
the picture is squashed instead of being scaled by its own shape.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autovid.application.issues import IssueCollector  # noqa: E402
from autovid.application.assembly import (  # noqa: E402
    _image_origin,
    _image_span,
)
from autovid.infrastructure.ffmpeg import FFMPEG, ffmpeg_available  # noqa: E402
from autovid.infrastructure.video.filters import (  # noqa: E402
    OverlayLayer,
    build_scene_filtergraph,
)
from autovid.domain.script import (  # noqa: E402
    ScriptSchemaError,
    parse_script,
)

_APP = QApplication.instance() or QApplication([])

FRAME = (320, 180)


def _script(overlay: dict) -> dict:
    return {
        "video_metadata": {
            "title": "t",
            "author": "Buffy",
            "resolution": "320x180",
            "fps": 10,
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


class ImageOverlaySchemaTest(unittest.TestCase):
    def test_a_picture_may_replace_the_words(self):
        script = parse_script(_script({"text": "", "image_file": "meme.png"}))
        overlay = script.scenes[0].text_overlays[0]
        self.assertEqual(overlay.image_file, "meme.png")
        self.assertEqual(overlay.text, "")

    def test_a_picture_and_words_together_are_allowed(self):
        script = parse_script(
            _script({"text": "Khi gap ho", "image_file": "meme.png"})
        )
        overlay = script.scenes[0].text_overlays[0]
        self.assertEqual(overlay.text, "Khi gap ho")
        self.assertEqual(overlay.image_file, "meme.png")

    def test_neither_words_nor_a_picture_is_refused(self):
        with self.assertRaises(ScriptSchemaError):
            parse_script(_script({"image_file": None}))

    def test_the_height_is_a_share_of_the_frame(self):
        script = parse_script(
            _script({"text": "", "image_file": "meme.png", "image_height": 0.4})
        )
        self.assertAlmostEqual(
            script.scenes[0].text_overlays[0].image_height, 0.4
        )

    def test_a_height_outside_the_frame_is_refused(self):
        for value in (0.0, -1.0, 2.0):
            with self.subTest(value=value):
                with self.assertRaises(ScriptSchemaError):
                    parse_script(
                        _script(
                            {
                                "text": "",
                                "image_file": "meme.png",
                                "image_height": value,
                            }
                        )
                    )


class ImageOverlayPlanTest(unittest.TestCase):
    """The plan: where it lands, how big, and what it was built out of."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_imgplan_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.issues = IssueCollector()
        self.still = self.out / "still.png"
        Image.new("RGBA", (80, 40), (255, 0, 0, 255)).save(self.still)
        self.gif = self.out / "anim.gif"
        frames = [
            Image.new("RGBA", (80, 40), colour)
            for colour in (
                (255, 0, 0, 255),
                (0, 0, 255, 255),
                (0, 255, 0, 255),
                (255, 0, 0, 255),
            )
        ]
        frames[0].save(
            self.gif,
            save_all=True,
            append_images=frames[1:],
            duration=120,
            loop=0,
        )

    def _overlay(self, **fields):
        """One overlay, with the parts every caption has to have."""
        from autovid.domain.script import TextOverlay

        base = {
            "text": "",
            "font": "assets/fonts/handwriting.ttf",
            "font_size": 40,
            "color": "#FFFFFF",
            "stroke_color": "#000000",
            "stroke_width": 2,
            "position": "center",
            "start_offset_ms": 0,
            "end_offset_ms": 1000,
            "animation": "fade_in",
            "animation_duration_ms": 200,
        }
        base.update(fields)
        return TextOverlay(**base)

    def _span(self, overlay):
        spans = _image_span(
            overlay=overlay,
            frame_size=FRAME,
            workspace=self.out,
            issues=self.issues,
            start_s=0.0,
            end_s=1.0,
            window=0.2,
            scene_id=1,
        )
        return spans[0] if spans else None

    def test_a_picture_keeps_its_own_shape(self):
        """Width follows the file: nobody wants a caption squashed."""
        span = self._span(
            self._overlay(image_file=str(self.still), image_height=0.5)
        )
        self.assertIsNotNone(span)
        _x, _y, width, height = span.box
        self.assertEqual(height, int(FRAME[1] * 0.5))
        self.assertEqual(width, height * 2)

    def test_a_picture_lands_on_the_position_it_names(self):
        bottom = self._span(
            self._overlay(image_file=str(self.still), position="bottom")
        )
        top = self._span(
            self._overlay(image_file=str(self.still), position="top")
        )
        self.assertGreater(bottom.box[1], top.box[1])
        self.assertEqual(bottom.box[0], top.box[0])

    def test_a_placed_picture_is_centred_on_its_coordinates(self):
        span = self._span(
            self._overlay(
                image_file=str(self.still), x=0.25, y=0.75,
                image_height=0.5,
            )
        )
        x, y, width, height = span.box
        self.assertAlmostEqual(
            x + width / 2, 0.25 * FRAME[0], delta=2
        )
        self.assertAlmostEqual(
            y + height / 2, 0.75 * FRAME[1], delta=2
        )

    def test_a_gif_is_marked_animated_and_a_still_is_not(self):
        self.assertTrue(
            self._span(self._overlay(image_file=str(self.gif))).animated
        )
        self.assertFalse(
            self._span(self._overlay(image_file=str(self.still))).animated
        )

    def test_a_picture_is_its_own_kind_of_layer(self):
        span = self._span(self._overlay(image_file=str(self.still)))
        self.assertEqual(span.media, "image")
        self.assertEqual(span.path, self.still)

    def test_a_missing_picture_is_dropped_and_reported(self):
        span = self._span(
            self._overlay(image_file="khong-co-that.png")
        )
        self.assertIsNone(span)
        codes = {issue.code for issue in self.issues.issues}
        self.assertIn("overlay_image_missing", codes)

    def test_a_file_that_is_not_an_image_is_reported(self):
        broken = self.out / "broken.png"
        broken.write_bytes(b"not an image at all")
        span = self._span(self._overlay(image_file=str(broken)))
        self.assertIsNone(span)
        codes = {issue.code for issue in self.issues.issues}
        self.assertIn("overlay_image_unreadable", codes)

    def test_a_typewriter_reveal_becomes_a_fade_for_a_picture(self):
        span = self._span(
            self._overlay(image_file=str(self.still), animation="typewriter")
        )
        self.assertEqual(span.animation, "fade_in")

    def test_the_window_is_the_one_the_author_asked_for(self):
        spans = _image_span(
            overlay=self._overlay(image_file=str(self.still)),
            frame_size=FRAME,
            workspace=self.out,
            issues=self.issues,
            start_s=1.5,
            end_s=3.0,
            window=0.2,
            scene_id=1,
        )
        self.assertAlmostEqual(spans[0].start_s, 1.5)
        self.assertAlmostEqual(spans[0].end_s, 3.0)


@unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")
class ImageOverlayRenderTest(unittest.TestCase):
    """Encode it and look at the picture, which is the only real proof."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_imgrender_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.background = self.out / "bg.png"
        Image.new("RGB", FRAME, (20, 20, 20)).save(self.background)
        self.still = self.out / "still.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(self.still)
        self.gif = self.out / "anim.gif"
        frames = [
            Image.new("RGBA", (64, 64), colour)
            for colour in (
                (255, 0, 0, 255),
                (0, 0, 255, 255),
                (255, 0, 0, 255),
                (0, 0, 255, 255),
            )
        ]
        frames[0].save(
            self.gif,
            save_all=True,
            append_images=frames[1:],
            duration=120,
            loop=0,
        )

    def render(self, layer: OverlayLayer, name: str) -> list[Path]:
        graph = build_scene_filtergraph(
            frames=12, frame_size=FRAME, fps=10,
            motion=None, overlays=[layer],
        )
        inputs = ["-i", str(self.background)]
        if layer.animated:
            inputs += ["-stream_loop", "-1", "-i", str(layer.path)]
        else:
            inputs += [
                "-loop", "1", "-framerate", "10", "-t", "1.2",
                "-i", str(layer.path),
            ]
        video = self.out / name
        result = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
             *inputs, "-filter_complex", graph, "-map", "[out]",
             "-frames:v", "12", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-pix_fmt", "yuv420p", "-r", "10", "-an",
             str(video)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0, f"{name} did not render:\n{result.stderr[-800:]}"
        )
        folder = self.out / name.replace(".", "_")
        folder.mkdir()
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video), str(folder / "f%03d.png")],
            check=True,
        )
        return sorted(folder.glob("*.png"))

    def _centre(self, shot: Path) -> tuple[int, int, int]:
        with Image.open(shot) as image:
            return image.convert("RGB").getpixel((FRAME[0] // 2, FRAME[1] // 2))

    def test_a_picture_lands_on_the_frame(self):
        shots = self.render(
            OverlayLayer(
                path=self.still, start_s=0.2, end_s=1.0, media="image",
                box=(128, 58, 64, 64),
            ),
            "still.mp4",
        )
        self.assertEqual(self._centre(shots[6])[0], 253)

    def test_a_gif_actually_moves(self):
        shots = self.render(
            OverlayLayer(
                path=self.gif, start_s=0.2, end_s=1.0, media="image",
                box=(128, 58, 64, 64), animated=True,
            ),
            "anim.mp4",
        )
        colours = {self._centre(shot) for shot in shots[2:]}
        self.assertGreater(
            len(colours), 1, "the GIF rendered as one frozen frame"
        )

    def test_a_picture_stays_off_before_and_after_its_window(self):
        shots = self.render(
            OverlayLayer(
                # A window long enough to actually hold the picture:
                # 0.4s of fade in, 0.2s held, 0.2s of fade out.
                path=self.still, start_s=0.4, end_s=1.0, media="image",
                box=(128, 58, 64, 64),
            ),
            "window.mp4",
        )
        # The overlay is a red square on a dark plate; "no red at all" is
        # the assertion, not an exact pixel value -- the encoder is free to
        # nudge the background by a level.
        for shot in (shots[0], shots[-1]):
            with self.subTest(shot=shot.name):
                self.assertLess(self._centre(shot)[0], 80)
        # ...and it is there while its window is open.  Frame 7 is t=0.7,
        # by which point the 0.2s fade-in has finished.
        self.assertGreater(self._centre(shots[7])[0], 200)


class StudioImageOverlayTest(unittest.TestCase):
    """The panel: paste a file, set its height, and it is on the stage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_imgui_")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project_dir = root / "video_a"
        (self.project_dir / "images/backgrounds").mkdir(parents=True)
        (self.project_dir / "assets/memes").mkdir(parents=True)
        Image.new("RGB", (320, 180), (90, 90, 90)).save(
            self.project_dir / "images/backgrounds/cong_lang.png"
        )
        self.meme = self.project_dir / "assets/memes/oh_no.png"
        Image.new("RGBA", (64, 32), (255, 0, 0, 255)).save(self.meme)
        script = self.project_dir / "script.json"
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
        self.canvas = StageCanvas(AssetLibrary(self.project_dir, REPO_ROOT))
        self.canvas.resize(800, 450)
        self.canvas.set_project(self.project)
        self.canvas.set_scene(self.project.scenes[0])
        self.project.scenes[0].overlays.append(TextOverlay(text="Chu moi"))
        self.canvas.select_overlay(0)
        self.canvas.progress = 0.5
        self.panel = OverlayPanel(self.canvas)
        self.panel.reload()

    def overlay(self):
        return self.project.scenes[0].overlays[0]

    def test_pasting_a_path_reaches_the_store(self):
        self.panel.image_edit.setText("assets/memes/oh_no.png")
        self.panel.image_edit.editingFinished.emit()
        self.assertEqual(self.overlay().image_file, "assets/memes/oh_no.png")

    def test_the_height_reaches_the_store(self):
        self.panel.image_edit.setText("assets/memes/oh_no.png")
        self.panel.image_edit.editingFinished.emit()
        self.panel.image_height.setValue(45)
        self.assertAlmostEqual(self.overlay().image_height, 0.45)

    def test_a_caption_with_no_picture_saves_no_path(self):
        from autovid.presentation.studio.store import TextOverlay

        self.assertNotIn("image_file", TextOverlay(text="Oi").to_dict())

    def test_a_pasted_picture_round_trips_through_the_file(self):
        from autovid.presentation.studio.store import TextOverlay

        self.panel.image_edit.setText("assets/memes/oh_no.png")
        self.panel.image_edit.editingFinished.emit()
        self.panel.image_height.setValue(40)
        data = self.overlay().to_dict()
        self.assertEqual(data["image_file"], "assets/memes/oh_no.png")
        restored = TextOverlay.from_dict(data)
        self.assertEqual(restored.image_file, "assets/memes/oh_no.png")
        self.assertAlmostEqual(restored.image_height, 0.40)

    def test_the_stage_draws_the_picture(self):
        self.panel.image_edit.setText("assets/memes/oh_no.png")
        self.panel.image_edit.editingFinished.emit()

        def picture() -> bytes:
            self.canvas.repaint()
            return bytes(self.canvas.grab().toImage().constBits())

        without = picture()
        self.overlay().image_file = None
        with_image = picture()
        self.assertNotEqual(without, with_image)

    def test_the_picture_keeps_its_shape_on_the_stage(self):
        self.panel.image_edit.setText("assets/memes/oh_no.png")
        self.panel.image_edit.editingFinished.emit()
        self.panel.image_height.setValue(30)

        from autovid.presentation.studio.preview import OverlayState

        state = OverlayState(text="Chu moi", opacity=1.0)
        layout = self.canvas.overlay_layout(
            self.overlay(), self.canvas._stage_rect(), state
        )
        self.assertIsNotNone(layout)
        box = layout[2]
        self.assertAlmostEqual(
            box.width() / box.height(), 2.0, places=1
        )


if __name__ == "__main__":
    unittest.main()