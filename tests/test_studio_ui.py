"""
The studio, exercised without a display.

Every test drives the real widgets through `QApplication` on the offscreen
platform: the point is that the wiring between canvas, inspector and store
holds, and that what the studio saves still validates.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

from autovid.presentation.studio.assets import AssetLibrary  # noqa: E402
from autovid.presentation.studio.canvas import (  # noqa: E402
    MIME_BACKGROUND,
    MIME_CHARACTER,
    MIME_NARRATOR,
    StageCanvas,
)
from autovid.presentation.studio.window import AssetList  # noqa: E402
from autovid.presentation.studio.store import (  # noqa: E402
    Impact,
    Move,
    Placement,
    ProjectStore,
    TextOverlay,
)

_APP = QApplication.instance() or QApplication([])


def _png(path: Path, colour: tuple[int, int, int], size=(320, 180)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)


class StudioTestCase(unittest.TestCase):
    """A project with one scene, one background and two sprites."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_studio_")
        self.root = Path(self._tmp.name)
        self.project_dir = self.root / "video_a"
        _png(self.project_dir / "images/backgrounds/cong_lang.png", (120, 90, 60))
        _png(self.project_dir / "assets/characters/chi_pheo.png", (200, 40, 40))
        _png(self.project_dir / "assets/characters/thi_no.png", (40, 200, 40))
        self.script = self.project_dir / "script.json"
        self.script.write_text(
            json.dumps(
                {
                    "video_metadata": {
                        "title": "Video thu nghiem",
                        "author": "Buffy",
                        "resolution": "1920x1080",
                        "fps": 30,
                        "language": "vi",
                    },
                    "story_frame": {"enabled": False},
                    "scenes": [
                        {
                            "id": 1,
                            "text": "Mot",
                            "image_file": "images/backgrounds/cong_lang.png",
                            "characters": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.project = ProjectStore.open(self.script)
        self.library = AssetLibrary(self.project_dir, REPO_ROOT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_canvas(self) -> StageCanvas:
        canvas = StageCanvas(self.library)
        canvas.resize(800, 450)
        canvas.set_project(self.project)
        canvas.set_scene(self.project.scenes[0])
        return canvas


class AssetLibraryTest(StudioTestCase):

    def test_finds_backgrounds_and_characters(self) -> None:
        names = [asset.name for asset in self.library.backgrounds]
        self.assertIn("cong_lang", names)
        sprite_names = [asset.name for asset in self.library.characters]
        self.assertIn("chi_pheo", sprite_names)
        self.assertIn("thi_no", sprite_names)

    def test_resolves_workspace_relative_paths(self) -> None:
        path = self.library.resolve("images/backgrounds/cong_lang.png")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())

    def test_resolves_by_name_when_prefix_differs(self) -> None:
        """A script may carry a repo-prefixed path the workspace lacks."""
        # Pick a repo image that actually exists rather than naming one:
        # the demo project's backgrounds get re-cut, and a test that fails
        # because a picture was renamed tests the wrong thing.
        sample = next(
            (path for path in (REPO_ROOT / "projects").rglob("*.png")
             if path.is_file()),
            None,
        )
        self.assertIsNotNone(sample, "no repo image to resolve against")
        path = self.library.resolve(str(sample.relative_to(REPO_ROOT)))
        self.assertIsNotNone(path)

    # -- the storytellers ------------------------------------------------
    def test_narrators_come_from_the_registry(self) -> None:
        """The frame's cast is named by keys, not by sprite files."""
        keys = [narrator.key for narrator in self.library.narrators]
        self.assertTrue(keys, "the repo registry should list narrators")
        self.assertNotIn("_readme", keys)
        for narrator in self.library.narrators:
            self.assertTrue(narrator.image_file, narrator.key)

    def test_a_project_registry_adds_and_overrides_the_repos(self) -> None:
        registry = self.project_dir / "assets" / "characters" / "characters.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "_readme": ["notes for humans, never a character"],
                    "my_own": {"image_file": "assets/characters/chi_pheo.png"},
                    "story_host": {"image_file": "assets/characters/thi_no.png"},
                }
            ),
            encoding="utf-8",
        )
        self.library.invalidate()
        narrators = {item.key: item.image_file for item in self.library.narrators}
        self.assertIn("my_own", narrators)
        # The project's file wins for a key both define...
        self.assertEqual(narrators["story_host"], "assets/characters/thi_no.png")
        # ...while the repo's other storytellers still show.
        self.assertIn("ke_su", narrators)

    def test_an_entry_without_artwork_is_skipped(self) -> None:
        registry = self.project_dir / "assets" / "characters" / "characters.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"naked": {"poses": []}}), encoding="utf-8"
        )
        self.library.invalidate()
        self.assertNotIn("naked", [item.key for item in self.library.narrators])

    def test_a_malformed_registry_reads_as_empty_not_as_an_error(self) -> None:
        registry = self.project_dir / "assets" / "characters" / "characters.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text("{ not json", encoding="utf-8")
        self.library.invalidate()
        # The repo's registry still supplies its own narrators.
        self.assertTrue(self.library.narrators)

    def test_a_registry_image_resolves_to_a_file(self) -> None:
        narrator = next(
            (item for item in self.library.narrators
             if "narrator" in item.image_file),
            None,
        )
        if narrator is None:
            self.skipTest("this checkout ships no default narrator art")
        path = self.library.resolve(narrator.image_file)
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())


class CanvasTest(StudioTestCase):

    def test_drag_a_background_on(self) -> None:
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.image_file = ""
        mime = QMimeData()
        mime.setData(MIME_BACKGROUND, b"images/backgrounds/cong_lang.png")
        canvas.dropEvent(
            QDropEvent(QPointF(400, 225), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual(scene.image_file, "images/backgrounds/cong_lang.png")

    def test_dropping_a_character_places_its_feet_under_the_cursor(self) -> None:
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        mime = QMimeData()
        mime.setData(MIME_CHARACTER, b"assets/characters/chi_pheo.png")
        stage = canvas._stage_rect()
        target = QPointF(stage.left() + stage.width() * 0.25, stage.top() + stage.height() * 0.8)
        canvas.dropEvent(
            QDropEvent(target, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual(len(scene.characters), 1)
        placement = scene.characters[0]
        self.assertAlmostEqual(placement.x, 0.25, places=2)
        self.assertAlmostEqual(placement.y, 0.8, places=2)

    def test_a_dropped_character_arrives_with_a_fade(self) -> None:
        """Dragging art onto the stage must not make it pop into being."""
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        mime = QMimeData()
        mime.setData(MIME_CHARACTER, b"assets/characters/chi_pheo.png")
        stage = canvas._stage_rect()
        target = QPointF(stage.left() + stage.width() * 0.5,
                          stage.top() + stage.height() * 0.8)
        canvas.dropEvent(
            QDropEvent(target, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        )
        placement = scene.characters[0]
        self.assertEqual(placement.enter_type, "fade_in")
        self.assertGreater(placement.enter_duration_ms, 0)

    def test_dropping_a_narrator_names_the_frames_host(self) -> None:
        """The storyteller carries a registry key, and turns the frame on."""
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        frame = self.project.story_frame
        self.assertFalse(frame.enabled)

        mime = QMimeData()
        mime.setData(MIME_NARRATOR, b"ke_su")
        stage = canvas._stage_rect()
        canvas.dropEvent(
            QDropEvent(QPointF(stage.center()), Qt.CopyAction, mime,
                       Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual(frame.use, "ke_su")
        self.assertTrue(frame.enabled, "a narrator behind a dead frame draws nothing")
        self.assertTrue(frame.show_narrator)
        self.assertTrue(self.project.dirty)

    def test_dropping_a_narrator_leaves_the_layout_alone(self) -> None:
        """Picking a storyteller is not a request to redesign the panel."""
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        frame = self.project.story_frame
        frame.style = "tv_retro"
        frame.x, frame.width, frame.host_height = 0.1, 0.5, 0.33

        mime = QMimeData()
        mime.setData(MIME_NARRATOR, b"ke_cuoi")
        stage = canvas._stage_rect()
        canvas.dropEvent(
            QDropEvent(QPointF(stage.center()), Qt.CopyAction, mime,
                       Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual((frame.style, frame.x, frame.width, frame.host_height),
                         ("tv_retro", 0.1, 0.5, 0.33))

    def test_an_empty_narrator_drop_changes_nothing(self) -> None:
        from PySide6.QtGui import QDropEvent

        canvas = self.make_canvas()
        frame = self.project.story_frame
        before = (frame.use, frame.enabled)

        mime = QMimeData()
        mime.setData(MIME_NARRATOR, b"")
        stage = canvas._stage_rect()
        canvas.dropEvent(
            QDropEvent(QPointF(stage.center()), Qt.CopyAction, mime,
                       Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual((frame.use, frame.enabled), before)
        self.assertFalse(self.project.dirty)

    def test_the_chosen_narrator_resolves_to_its_artwork(self) -> None:
        canvas = self.make_canvas()
        canvas.choose_narrator("ke_su")
        path = canvas._narrator_path()
        self.assertIsNotNone(path)
        self.assertTrue(self.library.resolve(path).exists())

    def test_dragging_moves_the_character_and_saves_the_fraction(self) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.characters.append(
            Placement(image_file="assets/characters/chi_pheo.png", x=0.5, y=0.8, height=0.3)
        )
        canvas.select(0)
        before = scene.characters[0].x

        rect = canvas.sprite_rect(scene.characters[0])
        centre = rect.center()
        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                centre,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        stage = canvas._stage_rect()
        moved = centre + QPointF(stage.width() * 0.1, 0)
        canvas.mouseMoveEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseMove, moved, Qt.NoButton, Qt.LeftButton, Qt.NoModifier
            )
        )
        canvas.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                moved,
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )
        self.assertGreater(scene.characters[0].x, before)
        self.assertTrue(self.project.dirty)

    def test_double_click_removes_the_character(self) -> None:
        from PySide6.QtGui import QMouseEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.characters.append(
            Placement(image_file="assets/characters/thi_no.png", x=0.5, y=0.8)
        )
        centre = canvas.sprite_rect(scene.characters[0]).center()
        canvas.mouseDoubleClickEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonDblClick,
                centre,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(scene.characters, [])

    def test_wheel_resizes_the_selected_sprite(self) -> None:
        from PySide6.QtGui import QWheelEvent

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.characters.append(
            Placement(image_file="assets/characters/thi_no.png", height=0.3)
        )
        canvas.select(0)
        before = scene.characters[0].height
        canvas.wheelEvent(
            QWheelEvent(
                QPointF(100, 100),
                QPointF(100, 100),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.NoScrollPhase,
                False,
            )
        )
        self.assertGreater(scene.characters[0].height, before)

    def test_scene_can_opt_out_of_the_project_frame(self) -> None:
        canvas = self.make_canvas()
        self.project.story_frame.enabled = True
        self.assertTrue(canvas.frame_active())
        self.project.scenes[0].story_frame_enabled = False
        self.assertFalse(canvas.frame_active())

    def test_scene_without_frame_is_always_bare(self) -> None:
        canvas = self.make_canvas()
        self.project.story_frame.enabled = False
        self.assertFalse(canvas.frame_active())

    def test_painting_does_not_raise(self) -> None:
        from PySide6.QtGui import QPixmap

        canvas = self.make_canvas()
        canvas.resize(640, 360)
        self.project.story_frame.enabled = True
        self.project.story_frame.use = "ke_su"
        self.project.scenes[0].characters.append(
            Placement(image_file="assets/characters/chi_pheo.png")
        )
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())


class OverlayAndImpactTest(StudioTestCase):
    """Text overlays and the scene punch, on the canvas and in the store."""

    def test_overlay_is_drawn_and_shrunk_to_fit(self) -> None:
        from PySide6.QtGui import QPixmap

        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.overlays = [
            TextOverlay(text="Bai 1 - Cuoc gap o cong lang", font_size=90)
        ]
        canvas.resize(640, 360)
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())

    def test_impact_zoom_peaks_then_returns(self) -> None:
        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.impact = Impact(enabled=True, intensity=0.2, duration_ms=450)
        timeline = canvas.timeline()
        # The spike peaks a third of the way through its own window, which
        # starts at the punch-in's beat rather than at the top of the scene.
        canvas.set_progress(
            (timeline.impact_offset() + 0.34 * 0.45) / timeline.span
        )
        peak = canvas.impact_scale()[0]
        canvas.set_progress(1.0)
        self.assertGreater(peak, 1.0)
        self.assertAlmostEqual(canvas.impact_scale()[0], 1.0)

    def test_impact_waits_for_its_sentence(self) -> None:
        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        scene.text = "Cau mot. Cau hai."
        scene.impact = Impact(enabled=True, at_sentence=2, duration_ms=400)
        timeline = canvas.timeline()

        canvas.set_progress((timeline.impact_offset() - 0.2) / timeline.span)
        self.assertEqual(canvas.impact_scale()[0], 1.0)
        canvas.set_progress(
            (timeline.impact_offset() + 0.34 * 0.4) / timeline.span
        )
        self.assertGreater(canvas.impact_scale()[0], 1.0)

    def test_impact_is_off_by_default(self) -> None:
        canvas = self.make_canvas()
        canvas.set_progress(0.2)
        self.assertEqual(canvas.impact_scale()[0], 1.0)

    def test_impact_flash_is_returned_while_active(self) -> None:
        canvas = self.make_canvas()
        self.project.scenes[0].impact = Impact(
            enabled=True, flash="white", duration_ms=400
        )
        canvas.set_progress(0.1)
        self.assertIsNotNone(canvas.impact_scale()[2])

    def test_overlay_panel_adds_and_edits(self) -> None:
        from autovid.presentation.studio.overlays import OverlayPanel

        canvas = self.make_canvas()
        panel = OverlayPanel(canvas)
        panel.on_add()
        self.assertEqual(len(self.project.scenes[0].overlays), 1)

        panel.text_edit.setText("Tieu de moi")
        panel._on_text()
        panel.position.setCurrentText("top")
        panel.font_size.setValue(120)
        self.assertEqual(self.project.scenes[0].overlays[0].text, "Tieu de moi")
        self.assertEqual(self.project.scenes[0].overlays[0].position, "top")
        self.assertEqual(self.project.scenes[0].overlays[0].font_size, 120)

    def test_overlay_panel_pushes_the_end_past_the_start(self) -> None:
        from autovid.presentation.studio.overlays import OverlayPanel

        canvas = self.make_canvas()
        panel = OverlayPanel(canvas)
        panel.on_add()
        panel.start.setValue(2000)
        panel.end.setValue(2000)
        overlay = self.project.scenes[0].overlays[0]
        self.assertGreater(overlay.end_offset_ms, overlay.start_offset_ms)

    def test_overlay_panel_reorders(self) -> None:
        from autovid.presentation.studio.overlays import OverlayPanel

        canvas = self.make_canvas()
        panel = OverlayPanel(canvas)
        panel.on_add()
        panel.on_add()
        canvas.scene.overlays[0].text = "A"
        canvas.scene.overlays[1].text = "B"
        canvas.select_overlay(0)
        panel._move(1)
        self.assertEqual(
            [overlay.text for overlay in canvas.scene.overlays], ["B", "A"]
        )

    def test_impact_panel_edits_the_scene(self) -> None:
        from autovid.presentation.studio.overlays import ImpactPanel

        canvas = self.make_canvas()
        panel = ImpactPanel(canvas)
        panel.enabled.setChecked(True)
        panel.intensity.setValue(0.25)
        panel.shake.setValue(20)
        panel.flash.setCurrentText("white")
        impact = self.project.scenes[0].impact
        self.assertTrue(impact.enabled)
        self.assertAlmostEqual(impact.intensity, 0.25)
        self.assertEqual(impact.shake_px, 20)
        self.assertEqual(impact.flash, "white")

    def test_impact_panel_anchors_to_sentence_or_time(self) -> None:
        from autovid.presentation.studio.overlays import ImpactPanel

        canvas = self.make_canvas()
        panel = ImpactPanel(canvas)
        panel.enabled.setChecked(True)
        panel.anchor.setCurrentIndex(0)
        panel.at_sentence.setValue(3)
        self.assertEqual(self.project.scenes[0].impact.at_sentence, 3)

        panel.anchor.setCurrentIndex(1)
        panel.at_offset.setValue(1200)
        self.assertIsNone(self.project.scenes[0].impact.at_sentence)
        self.assertEqual(self.project.scenes[0].impact.at_offset_ms, 1200)


class NarratorFolderTest(StudioTestCase):
    """`assets/narrators/`: a picture dropped in is a storyteller, no JSON."""

    def _drop(self, folder: Path, name: str) -> str:
        """Put a real image in a narrator folder; return its script path."""
        folder.mkdir(parents=True, exist_ok=True)
        source = self.project_dir / "assets/characters/chi_pheo.png"
        shutil.copyfile(source, folder / name)
        return f"assets/narrators/{name}"

    def test_a_dropped_picture_shows_up_as_a_storyteller(self) -> None:
        relative = self._drop(
            self.project_dir / "assets/narrators", "co_giao.png"
        )
        self.library.invalidate()
        found = {item.image_file: item for item in self.library.narrators}
        self.assertIn(relative, found)
        # No registry entry describes it, so it is named by its picture.
        self.assertIsNone(found[relative].use)
        self.assertEqual(found[relative].key, "co_giao")

    def test_a_picture_the_registry_names_is_not_listed_twice(self) -> None:
        relative = self._drop(
            self.project_dir / "assets/narrators", "ke_su.png"
        )
        registry = self.project_dir / "assets/characters/characters.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"ke_su": {"image_file": relative}}), encoding="utf-8"
        )
        self.library.invalidate()
        images = [item.image_file for item in self.library.narrators]
        self.assertEqual(images.count(relative), 1)

    def test_the_projects_folder_wins_for_the_same_file_name(self) -> None:
        relative = self._drop(
            self.project_dir / "assets/narrators", "chi_pheo.png"
        )
        self.library.invalidate()
        item = next(
            (entry for entry in self.library.narrators
             if entry.image_file == relative),
            None,
        )
        self.assertIsNotNone(item)
        self.assertEqual(
            self.library.resolve(relative),
            self.project_dir / "assets/narrators/chi_pheo.png",
        )

    def test_picking_a_picture_names_the_frame_by_its_path(self) -> None:
        canvas = self.make_canvas()
        canvas.choose_narrator_image("assets/narrators/co_giao.png")
        frame = self.project.story_frame
        self.assertEqual(frame.image_file, "assets/narrators/co_giao.png")
        self.assertIsNone(frame.use)
        self.assertTrue(frame.enabled)
        self.assertTrue(frame.show_narrator)
        self.assertEqual(canvas._narrator_path(), "assets/narrators/co_giao.png")

    def test_a_key_and_a_picture_replace_each_other(self) -> None:
        """One narrator slot, two ways to name it: never both at once."""
        canvas = self.make_canvas()
        canvas.choose_narrator("ke_su")
        canvas.choose_narrator_image("assets/narrators/co_giao.png")
        self.assertIsNone(self.project.story_frame.use)

        canvas.choose_narrator("ke_su")
        self.assertEqual(self.project.story_frame.use, "ke_su")
        self.assertIsNone(self.project.story_frame.image_file)

    def test_the_payload_decides_key_or_picture(self) -> None:
        """A library row carries a key when the registry has one, else a path."""
        canvas = self.make_canvas()
        self._drop(self.project_dir / "assets/narrators", "co_giao.png")
        self.library.invalidate()

        self.assertEqual(canvas.choose_narrator_payload("ke_su"), "ke_su")
        self.assertEqual(self.project.story_frame.use, "ke_su")

        name = canvas.choose_narrator_payload("assets/narrators/co_giao.png")
        self.assertEqual(name, "co_giao")
        self.assertEqual(
            self.project.story_frame.image_file, "assets/narrators/co_giao.png"
        )

    def test_the_window_lists_the_folder_and_saves_a_valid_path(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script
        from autovid.presentation.studio.window import StudioWindow

        relative = self._drop(
            self.project_dir / "assets/narrators", "ong_thay.png"
        )
        window = StudioWindow()
        window.resize(1200, 800)
        window.open_project(self.script)

        rows = {
            window.narrator_list.item(i).data(Qt.UserRole)
            for i in range(window.narrator_list.count())
        }
        self.assertIn(relative, rows)

        item = next(
            window.narrator_list.item(i)
            for i in range(window.narrator_list.count())
            if window.narrator_list.item(i).data(Qt.UserRole) == relative
        )
        window._on_narrator_picked(item)
        self.assertEqual(
            window.current_project.story_frame.image_file, relative
        )
        window.on_save()

        written = json.loads(self.script.read_text(encoding="utf-8"))
        self.assertEqual(written["story_frame"]["image_file"], relative)
        try:
            parse_script(written)
        except ScriptSchemaError as error:
            self.fail(f"a storyteller picture wrote an invalid script: {error}")

        reopened = ProjectStore.open(self.script)
        self.assertEqual(reopened.story_frame.image_file, relative)


class PreviewTimelineTest(StudioTestCase):
    """The estimated clock the live preview runs on."""

    def _timeline(self, **changes):
        from autovid.presentation.studio.preview import SceneTimeline

        scene = self.project.scenes[0]
        for name, value in changes.items():
            setattr(scene, name, value)
        return SceneTimeline(scene, 1.0)

    def _character(self, scene, **kwargs) -> Placement:
        placement = Placement(
            image_file="assets/characters/chi_pheo.png", **kwargs
        )
        scene.characters.append(placement)
        return placement

    def test_a_short_scene_still_runs_long_enough_for_its_effects(self) -> None:
        # The fixture narrates "Mot": a fifth of a second of text would
        # leave an entrance nowhere to happen.
        self.assertAlmostEqual(self._timeline().span, 3.0)

    def test_the_clock_follows_the_narration_at_the_reading_rate(self) -> None:
        self.assertAlmostEqual(self._timeline(text="x" * 700).span, 50.0)

    def test_a_scene_pause_extends_the_clock(self) -> None:
        self.assertAlmostEqual(self._timeline(pause_after_ms=2000).span, 5.0)

    def test_sentences_share_the_estimate_by_their_length(self) -> None:
        timeline = self._timeline(text="Bon chu. Hai.")
        windows = timeline.windows
        self.assertEqual(len(windows), 2)
        share = timeline.usable_s * len("Bon chu.") / (
            len("Bon chu.") + len("Hai.")
        )
        self.assertAlmostEqual(windows[0][0], 0.0)
        self.assertAlmostEqual(windows[0][1], share)
        self.assertAlmostEqual(windows[-1][1], timeline.usable_s)

    def test_a_character_waits_for_its_sentence_and_fades_in(self) -> None:
        timeline = self._timeline(text="Cau mot. Cau hai.")
        placement = self._character(
            self.project.scenes[0],
            at_sentence=2,
            enter_type="fade_in",
            enter_duration_ms=400,
        )
        start = timeline.windows[1][0]

        self.assertIsNone(timeline.actor_state(placement, start - 0.1, (0.2, 0.3)))
        entering = timeline.actor_state(placement, start + 0.2, (0.2, 0.3))
        self.assertAlmostEqual(entering.opacity, 0.5)
        settled = timeline.actor_state(placement, start + 0.4, (0.2, 0.3))
        self.assertAlmostEqual(settled.opacity, 1.0)

    def test_a_fly_in_starts_off_stage_and_lands_on_its_mark(self) -> None:
        timeline = self._timeline()
        placement = self._character(
            self.project.scenes[0],
            x=0.5,
            enter_type="fly_in",
            enter_from="left",
            enter_duration_ms=400,
        )
        opening = timeline.actor_state(placement, 0.0, (0.2, 0.3))
        # Clear of the left edge: the sprite's centre is off the frame.
        self.assertAlmostEqual(opening.x + opening.dx, -0.1)
        self.assertAlmostEqual(
            timeline.actor_state(placement, 0.4, (0.2, 0.3)).dx, 0.0
        )

    def test_a_drop_falls_in_and_bounces(self) -> None:
        timeline = self._timeline()
        placement = self._character(
            self.project.scenes[0],
            y=0.8,
            enter_type="drop_bounce",
            enter_duration_ms=600,
        )
        self.assertAlmostEqual(
            timeline.actor_state(placement, 0.0, (0.2, 0.3)).dy, -0.8
        )
        landed = timeline.actor_state(placement, 0.6 * 0.65, (0.2, 0.3))
        self.assertAlmostEqual(landed.dy, 0.0)
        self.assertLess(
            timeline.actor_state(placement, 0.6 * 0.75, (0.2, 0.3)).dy, 0.0
        )

    def test_an_exit_clears_the_stage_by_the_end_of_the_cue(self) -> None:
        timeline = self._timeline()
        placement = self._character(
            self.project.scenes[0],
            enter_type="none",
            exit_type="fly_out",
            exit_to="right",
            exit_duration_ms=500,
        )
        standing = timeline.actor_state(placement, timeline.span - 0.5, (0.2, 0.3))
        self.assertAlmostEqual(standing.dx, 0.0)
        leaving = timeline.actor_state(placement, timeline.span - 0.25, (0.2, 0.3))
        self.assertGreater(leaving.dx, 0.0)

    def test_the_walk_carries_the_character_to_each_stop(self) -> None:
        timeline = self._timeline()
        placement = self._character(
            self.project.scenes[0],
            x=0.2,
            enter_type="none",
            moves=[Move(x=0.8, at_sentence=1, duration_ms=1000)],
        )
        self.assertAlmostEqual(timeline.walk_position(placement, 0.0)[0], 0.2)
        self.assertAlmostEqual(timeline.walk_position(placement, 1.0)[0], 0.8)
        midway = timeline.walk_position(placement, 0.5)[0]
        self.assertGreater(midway, 0.2)
        self.assertLess(midway, 0.8)

    def test_a_stop_turns_the_character_when_it_begins(self) -> None:
        timeline = self._timeline(text="Cau mot. Cau hai.")
        placement = self._character(
            self.project.scenes[0],
            enter_type="none",
            moves=[Move(x=0.8, at_sentence=2, duration_ms=800, flip=True)],
        )
        start = timeline.windows[1][0]
        self.assertFalse(timeline.walk_position(placement, start - 0.05)[1])
        self.assertTrue(timeline.walk_position(placement, start)[1])

    def test_the_preview_parks_after_everyone_has_arrived(self) -> None:
        timeline = self._timeline(text="Cau mot. Cau hai.")
        scene = self.project.scenes[0]
        early = self._character(
            scene, at_sentence=1, enter_type="fade_in", enter_duration_ms=300
        )
        late = self._character(
            scene, at_sentence=2, enter_type="fade_in", enter_duration_ms=500
        )

        design = timeline.design_seconds(scene.characters)
        for placement in (early, late):
            state = timeline.actor_state(placement, design, (0.2, 0.3))
            self.assertIsNotNone(state)
            self.assertAlmostEqual(state.opacity, 1.0)

    def test_the_park_frame_splits_the_difference_when_staging_overlaps(self) -> None:
        timeline = self._timeline(text="Cau mot. Cau hai.")
        scene = self.project.scenes[0]
        # The first leaves as sentence 1 ends, the second arrives at 2.
        leaving = self._character(
            scene,
            at_sentence=1,
            for_sentences=1,
            exit_type="fade_out",
            exit_duration_ms=300,
        )
        arriving = self._character(
            scene, at_sentence=2, enter_type="fade_in", enter_duration_ms=500
        )

        design = timeline.design_seconds(scene.characters)
        _, leave_end = timeline.cue_window(leaving)
        arrive_start, _ = timeline.cue_window(arriving)
        self.assertAlmostEqual(
            design, ((arrive_start + 0.5) + (leave_end - 0.3)) / 2.0
        )
        self.assertLessEqual(design, timeline.span)

    def test_the_punch_in_waits_for_its_sentence(self) -> None:
        timeline = self._timeline(
            text="Cau mot. Cau hai.",
            impact=Impact(enabled=True, at_sentence=2),
        )
        self.assertAlmostEqual(timeline.impact_offset(), timeline.windows[1][0])

    def test_a_caption_shows_only_inside_its_own_window(self) -> None:
        timeline = self._timeline()
        overlay = TextOverlay(
            text="Tieu de",
            start_offset_ms=1000,
            end_offset_ms=3000,
            animation="none",
        )
        self.assertIsNone(timeline.overlay_state(overlay, 0.5))
        self.assertIsNotNone(timeline.overlay_state(overlay, 2.0))
        self.assertIsNone(timeline.overlay_state(overlay, 3.5))

    def test_a_typewriter_reveals_its_caption(self) -> None:
        timeline = self._timeline()
        overlay = TextOverlay(
            text="ABCDEFGH",
            start_offset_ms=0,
            end_offset_ms=3000,
            animation="typewriter",
            animation_duration_ms=400,
        )
        self.assertEqual(timeline.overlay_state(overlay, 0.2).text, "ABCD")
        self.assertEqual(timeline.overlay_state(overlay, 0.5).text, "ABCDEFGH")

    def test_a_caption_fades_out_before_it_goes(self) -> None:
        timeline = self._timeline()
        overlay = TextOverlay(
            text="Tieu de",
            start_offset_ms=0,
            end_offset_ms=2000,
            animation="fade_in",
            animation_duration_ms=400,
        )
        self.assertLess(timeline.overlay_state(overlay, 1.9).opacity, 1.0)
        self.assertIsNone(timeline.overlay_state(overlay, 2.1))


class PreviewIdleTest(StudioTestCase):
    """
    The sway a standing character keeps up, as the preview draws it.

    The preview used to draw the entrance, the exit and the walk and
    nothing else, so a character dragged onto the stage stood perfectly
    still no matter which idle it was given -- the motion existed in the
    render and nowhere the author could see it.
    """

    def _timeline(self):
        from autovid.presentation.studio.preview import SceneTimeline

        return SceneTimeline(self.project.scenes[0], 1.0)

    def _standing(self, **kwargs) -> tuple:
        fields = {
            "image_file": "assets/characters/chi_pheo.png",
            "enter_type": "none",
            "exit_type": "none",
        }
        fields.update(kwargs)
        placement = Placement(**fields)
        self.project.scenes[0].characters.append(placement)
        return placement, self._timeline()

    def _cycle(self, placement, timeline) -> list[float]:
        """The rotation over one whole idle cycle, start to finish."""
        period = max(float(placement.idle_period_s), 0.2)
        steps = 40
        return [
            timeline.actor_state(placement, period * step / steps).rotation
            for step in range(steps + 1)
        ]

    def test_a_still_character_with_no_idle_does_not_move(self) -> None:
        placement, timeline = self._standing(
            idle_type="none", idle_amplitude_px=4
        )
        self.assertEqual(timeline.actor_state(placement, 1.0).rotation, 0.0)

    def test_a_lean_sways_thirty_degrees_each_way(self) -> None:
        placement, timeline = self._standing(
            idle_type="lean", idle_amplitude_px=30, idle_period_s=2.6
        )
        angles = self._cycle(placement, timeline)

        self.assertAlmostEqual(angles[0], 0.0, places=9)
        self.assertAlmostEqual(max(angles), 30.0, places=6)
        self.assertAlmostEqual(min(angles), -30.0, places=6)
        # Upright again at the end of the cycle: a sway that never returns to
        # centre would be a lean the character cannot hold.
        self.assertAlmostEqual(angles[-1], 0.0, delta=1.0)

    def test_a_lean_never_moves_the_character_off_its_mark(self) -> None:
        placement, timeline = self._standing(
            idle_type="lean", idle_amplitude_px=30, idle_period_s=2.6
        )
        xs = {
            round(timeline.actor_state(placement, t / 10.0).x, 9)
            for t in range(0, 30)
        }
        self.assertEqual(len(xs), 1)

    def test_every_positional_idle_actually_moves_in_the_preview(self) -> None:
        for kind in ("bob", "sway", "bob_sway", "shake", "talk"):
            with self.subTest(idle=kind):
                placement, timeline = self._standing(
                    idle_type=kind,
                    idle_amplitude_px=16,
                    idle_period_s=0.8,
                )
                offsets = {
                    (state.x, state.dy)
                    for state in (
                        timeline.actor_state(placement, step / 20.0)
                        for step in range(0, 60)
                    )
                }
                self.assertGreater(
                    len(offsets),
                    1,
                    f"{kind} draws a perfectly still character",
                )

    def test_a_tilt_still_starts_upright_and_stays_on_one_side(self) -> None:
        placement, timeline = self._standing(
            idle_type="tilt", idle_amplitude_px=8, idle_period_s=2.0
        )
        angles = self._cycle(placement, timeline)
        self.assertAlmostEqual(angles[0], 0.0, places=9)
        self.assertAlmostEqual(max(angles), 8.0, places=6)
        self.assertGreaterEqual(min(angles), -1e-9)

    def test_an_entrance_spin_owns_the_angle_for_its_length(self) -> None:
        spinning, timeline = self._standing(
            idle_type="lean",
            idle_amplitude_px=30,
            idle_period_s=2.6,
            enter_type="spin_in",
            enter_duration_ms=800,
        )
        bare, _ = self._standing(
            idle_type="none",
            enter_type="spin_in",
            enter_duration_ms=800,
        )

        # Mid-spin the two are the same rotation: a whole turn owns the
        # angle outright, and adding a sway underneath would tilt the spin.
        self.assertAlmostEqual(
            timeline.actor_state(spinning, 0.16).rotation,
            timeline.actor_state(bare, 0.16).rotation,
            places=9,
        )
        self.assertLess(timeline.actor_state(bare, 0.16).rotation, -90.0)
        # Once the spin is over the sway comes back.
        self.assertNotEqual(
            timeline.actor_state(spinning, 1.4).rotation, 0.0
        )

    def test_the_sway_is_mirrored_onto_the_drawn_position(self) -> None:
        placement, timeline = self._standing(
            idle_type="sway", idle_amplitude_px=20, idle_period_s=1.0
        )
        rest = Placement(
            image_file=placement.image_file,
            enter_type="none",
            exit_type="none",
        )
        self.project.scenes[0].characters.append(rest)
        moving = timeline.actor_state(placement, 0.25).x
        still = timeline.actor_state(rest, 0.25).x
        self.assertAlmostEqual(moving - still, 20.0 / 1920.0, places=9)


class PreviewCanvasTest(StudioTestCase):
    """The stage drawn at a moment of the scene, not only at rest."""

    def _character(self, scene, **kwargs) -> Placement:
        placement = Placement(
            image_file="assets/characters/chi_pheo.png", **kwargs
        )
        scene.characters.append(placement)
        return placement

    def test_the_scrub_carries_the_sprite_along_its_walk(self) -> None:
        canvas = self.make_canvas()
        placement = self._character(
            self.project.scenes[0],
            x=0.2,
            enter_type="none",
            moves=[Move(x=0.8, at_sentence=1, duration_ms=1000)],
        )
        timeline = canvas.timeline()

        canvas.set_progress(0.0)
        before = canvas.sprite_rect(placement).center().x()
        canvas.set_progress(
            (timeline.move_start(placement.moves[0]) + 0.5) / timeline.span
        )
        during = canvas.sprite_rect(placement).center().x()
        canvas.set_progress(1.0)
        after = canvas.sprite_rect(placement).center().x()

        self.assertGreater(during, before)
        self.assertGreater(after, during)

    def test_a_loaded_scene_opens_on_a_settled_frame(self) -> None:
        """Time zero is an empty stage while characters fly in; the studio
        parks just after the entrances so a dragged-in sprite shows."""
        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        placement = self._character(
            scene, enter_type="fade_in", enter_duration_ms=400
        )

        canvas.set_scene(scene)
        timeline = canvas.timeline()
        state = timeline.actor_state(
            placement, timeline.seconds(canvas.progress), (0.2, 0.3)
        )
        self.assertIsNotNone(state)
        self.assertAlmostEqual(state.opacity, 1.0)

    def test_the_stage_paints_at_every_point_of_the_scene(self) -> None:
        from PySide6.QtGui import QPixmap

        canvas = self.make_canvas()
        self._character(
            self.project.scenes[0],
            enter_type="fly_in",
            enter_from="left",
            enter_duration_ms=400,
            moves=[Move(x=0.8, at_sentence=1, duration_ms=800)],
            exit_type="spin_out",
            exit_to="right",
            exit_duration_ms=400,
        )
        canvas.resize(640, 360)
        for progress in (0.0, 0.05, 0.2, 0.5, 0.9, 1.0):
            canvas.set_progress(progress)
            pixmap = QPixmap(canvas.size())
            canvas.render(pixmap)
            self.assertFalse(pixmap.isNull(), progress)


class MultiSelectTest(StudioTestCase):

    def _stage_with_three(self) -> StageCanvas:
        canvas = self.make_canvas()
        scene = self.project.scenes[0]
        for name, x in (("chi_pheo", 0.3), ("thi_no", 0.5), ("ly_cuong", 0.7)):
            if (self.project_dir / f"assets/characters/{name}.png").exists() or True:
                scene.characters.append(
                    Placement(image_file=f"assets/characters/{name}.png", x=x, y=0.8)
                )
        return canvas

    def test_shift_click_adds_to_the_selection(self) -> None:
        from PySide6.QtGui import QMouseEvent

        canvas = self._stage_with_three()
        first = canvas.sprite_rect(canvas.scene.characters[0]).center()
        second = canvas.sprite_rect(canvas.scene.characters[1]).center()

        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                first,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        canvas.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                first,
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )
        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                second,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.ShiftModifier,
            )
        )
        # The first click stays the one the inspector edits; the shift
        # click joins the group rather than replacing it.
        self.assertEqual(canvas.selected, 0)
        self.assertIn(1, canvas.also_selected)
        self.assertTrue(canvas.is_selected(0))
        self.assertTrue(canvas.is_selected(1))

    def test_select_all_covers_the_whole_cast(self) -> None:
        canvas = self._stage_with_three()
        canvas.select_all()
        self.assertEqual(canvas.selected, 0)
        self.assertEqual(canvas.also_selected, {1, 2})

    def test_dragging_moves_every_selected_character(self) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        canvas = self._stage_with_three()
        canvas.select_all()
        scene = canvas.scene
        before = [placement.x for placement in scene.characters]

        centre = canvas.sprite_rect(scene.characters[0]).center()
        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                centre,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        stage = canvas._stage_rect()
        moved = centre + QPointF(stage.width() * 0.05, 0)
        canvas.mouseMoveEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseMove, moved, Qt.NoButton, Qt.LeftButton, Qt.NoModifier
            )
        )
        canvas.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                moved,
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )
        for placement, old in zip(scene.characters, before):
            self.assertGreater(placement.x, old)

    def test_shift_click_off_the_same_character_deselects_it(self) -> None:
        canvas = self._stage_with_three()
        canvas.select(0)
        canvas.toggle_selection(0)
        self.assertIsNone(canvas.selected)
        self.assertEqual(canvas.also_selected, set())


class WalkPanelTest(StudioTestCase):
    """The walk panel and the route the canvas draws for it."""

    def _canvas_with_character(self) -> StageCanvas:
        canvas = self.make_canvas()
        self.canvas = canvas
        self.project.scenes[0].characters.append(
            Placement(
                image_file="assets/characters/chi_pheo.png", x=0.2, y=0.8, height=0.3
            )
        )
        canvas.select(0)
        return canvas

    def _panel(self) -> "WalkPanel":
        from autovid.presentation.studio.walk import WalkPanel

        return WalkPanel(self.canvas)

    def _timeline(self) -> "SceneTimeline":
        from autovid.presentation.studio.preview import SceneTimeline

        return SceneTimeline(self.project.scenes[0], 1.0)

    def test_adding_a_stop_extends_the_route(self) -> None:
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        moves = self.project.scenes[0].characters[0].moves
        self.assertEqual(len(moves), 1)
        # The new stop starts ahead of where the character already stands.
        self.assertGreater(moves[0].x, 0.2)

    def test_editing_a_stop_reaches_the_store(self) -> None:
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.x_spin.setValue(700)
        panel.duration.setValue(1200)
        panel.anchor.setCurrentIndex(1)
        panel.offset.setValue(2500)

        move = self.project.scenes[0].characters[0].moves[0]
        self.assertAlmostEqual(move.x, 0.7)
        self.assertEqual(move.duration_ms, 1200)
        self.assertIsNone(move.at_sentence)
        self.assertEqual(move.at_offset_ms, 2500)

    def test_stops_can_be_reordered_and_removed(self) -> None:
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.on_add()
        moves = self.project.scenes[0].characters[0].moves
        moves[0].x = 0.3
        moves[1].x = 0.8

        canvas.select_move(0)
        panel._move(1)
        self.assertEqual([m.x for m in moves], [0.8, 0.3])

        canvas.select_move(0)
        panel.on_remove()
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].x, 0.3)

    def test_selecting_a_stop_on_the_stage(self) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        canvas = self._canvas_with_character()
        from autovid.presentation.studio.walk import WalkPanel

        panel = WalkPanel(canvas)
        panel.on_add()
        move = canvas.current().moves[0]
        stage = canvas._stage_rect()
        marker = QPointF(
            stage.left() + move.x * stage.width(),
            stage.top() + 0.8 * stage.height(),
        )
        canvas.selected_move = None
        canvas.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                marker,
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        self.assertEqual(canvas.selected_move, 0)

    def test_the_route_is_drawn(self) -> None:
        from PySide6.QtGui import QPixmap

        canvas = self._canvas_with_character()
        from autovid.presentation.studio.walk import WalkPanel

        panel = WalkPanel(canvas)
        panel.on_add()
        panel.on_add()
        canvas.resize(640, 360)
        pixmap = QPixmap(canvas.size())
        canvas.render(pixmap)
        self.assertFalse(pixmap.isNull())

    def test_a_walk_saved_by_the_studio_is_valid(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script
        from autovid.presentation.studio.window import StudioWindow

        window = StudioWindow()
        window.resize(1200, 800)
        window.open_project(self.script)
        if not window.current_project.scenes[0].characters:
            window._on_character_picked(window.character_list.item(0))

        window.canvas.select(0)
        window.walk_panel.on_add()
        window.walk_panel.on_add()
        window.on_save()

        raw = json.loads(self.script.read_text(encoding="utf-8"))
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"studio wrote an invalid script.json: {error}")
        moves = raw["scenes"][0]["characters"][0]["moves"]
        self.assertEqual(len(moves), 2)
        self.assertIn("at_sentence", moves[0])

    def test_a_walk_comes_back_when_reopened(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].characters.append(
            Placement(
                image_file="assets/characters/chi_pheo.png",
                moves=[Move(x=0.6, at_sentence=2, duration_ms=900)],
            )
        )
        ProjectStore.save(self.project)
        reopened = ProjectStore.open(self.script)
        moves = reopened.scenes[0].characters[0].moves
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].at_sentence, 2)
        self.assertEqual(moves[0].duration_ms, 900)

    def test_the_pace_control_reaches_the_store(self) -> None:
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.duration.setValue(2000)
        panel.speed.setValue(0.5)

        move = self.project.scenes[0].characters[0].moves[0]
        self.assertAlmostEqual(move.speed, 0.5)
        # ...and the walk really does take twice as long over it.
        self.assertEqual(move.travel_ms, 4000)

    def test_the_lean_control_reaches_the_store(self) -> None:
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.sway.setValue(25)

        self.assertEqual(self.project.scenes[0].characters[0].moves[0].sway_deg, 25.0)

    def test_the_list_shows_the_real_duration_not_the_authored_one(self) -> None:
        """Speed changes the walk's length, so the row has to show that."""
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.duration.setValue(2000)
        panel.speed.setValue(0.5)
        row = panel.list.item(0).text()
        self.assertIn("4000ms", row)
        self.assertIn("0.5x", row)

    def test_the_pace_label_shows_the_pace_that_was_stored(self) -> None:
        """The row must not re-round the pace the spin box already rounded.

        A stored 0.25 shown as "0.2x" reads as though the panel quietly
        changed the walk while the author was typing.
        """
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.speed.setValue(0.25)
        row = panel.list.item(0).text()
        self.assertIn("0.25x", row)
        self.assertEqual(
            self.project.scenes[0].characters[0].moves[0].speed, 0.25
        )

    def test_the_travel_hint_follows_the_selected_stop(self) -> None:
        """The hint must not keep showing the previous stop's duration."""
        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        panel.duration.setValue(2000)
        panel.speed.setValue(0.5)
        panel.on_add()
        # A new stop arrives at a deliberately unhurried pace, so this one is
        # set back to 1.0 to give the hint something to switch away from.
        panel.speed.setValue(1.0)
        panel.list.setCurrentRow(1)
        panel.reload()
        self.assertEqual(panel.travel_hint.text(), "")
        panel.list.setCurrentRow(0)
        panel.reload()
        self.assertIn("4000", panel.travel_hint.text())

    def test_a_quick_spot_button_sets_the_destination(self) -> None:
        from autovid.presentation.studio.walk import QUICK_SPOTS

        canvas = self._canvas_with_character()
        panel = self._panel()
        panel.on_add()
        for label, value in QUICK_SPOTS:
            with self.subTest(spot=label):
                panel._set_x(value)
                self.assertAlmostEqual(
                    self.project.scenes[0].characters[0].moves[0].x, value, places=3
                )

    def test_the_quick_spot_buttons_are_labelled_for_what_they_set(self) -> None:
        """The whole point of the quick spots is not having to read numbers."""
        from PySide6.QtWidgets import QPushButton

        from autovid.presentation.studio.walk import QUICK_SPOTS

        canvas = self._canvas_with_character()
        panel = self._panel()
        self.assertEqual(
            [button.text() for button in panel.spots.findChildren(QPushButton)],
            [label for label, _ in QUICK_SPOTS],
        )

    def test_speed_and_lean_survive_a_save_and_reopen(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].characters.append(
            Placement(
                image_file="assets/characters/chi_pheo.png",
                moves=[
                    Move(
                        x=0.6, at_sentence=2, duration_ms=900,
                        speed=0.5, sway_deg=22.0,
                    )
                ],
            )
        )
        ProjectStore.save(self.project)
        move = ProjectStore.open(self.script).scenes[0].characters[0].moves[0]
        self.assertAlmostEqual(move.speed, 0.5)
        self.assertAlmostEqual(move.sway_deg, 22.0)
        self.assertEqual(move.travel_ms, 1800)

    def test_an_untouched_walk_is_written_without_the_new_keys(self) -> None:
        """A project written before this feature must not gain empty keys."""
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].characters.append(
            Placement(
                image_file="assets/characters/chi_pheo.png",
                moves=[Move(x=0.6, at_sentence=2, duration_ms=900)],
            )
        )
        ProjectStore.save(self.project)
        raw = json.loads(self.script.read_text(encoding="utf-8"))
        move = raw["scenes"][0]["characters"][0]["moves"][0]
        self.assertNotIn("speed", move)
        self.assertNotIn("sway_deg", move)

    def test_the_preview_leans_the_way_the_character_walks(self) -> None:
        """The Studio has to show the walk the renderer will draw."""
        canvas = self._canvas_with_character()
        placement = self.project.scenes[0].characters[0]
        placement.moves.append(
            Move(x=0.8, at_sentence=1, duration_ms=1000, sway_deg=20.0)
        )
        timeline = self._timeline()
        _, _, lean = timeline.walk_pose(placement, 0.5)
        self.assertGreater(lean, 0.0, "walking right should lean right")
        placement.moves[-1].x = 0.05
        _, _, lean_left = timeline.walk_pose(placement, 0.5)
        self.assertLess(lean_left, 0.0, "walking left should lean left")

    def test_the_preview_stands_upright_outside_the_walk(self) -> None:
        canvas = self._canvas_with_character()
        placement = self.project.scenes[0].characters[0]
        placement.moves.append(
            Move(x=0.8, at_sentence=1, duration_ms=500, sway_deg=20.0)
        )
        self.assertAlmostEqual(self._timeline().walk_pose(placement, 30.0)[2], 0.0)


class WindowTest(StudioTestCase):

    def make_window(self):
        from autovid.presentation.studio.window import StudioWindow

        window = StudioWindow()
        window.resize(1400, 860)
        window.open_project(self.script)
        return window

    def test_opens_a_project_and_fills_the_panels(self) -> None:
        window = self.make_window()
        self.assertEqual(window.current_project.name, "video_a")
        self.assertEqual(window.scene_list.count(), 1)
        self.assertGreater(window.background_list.count(), 0)
        self.assertEqual(window.title_field.text(), "Video thu nghiem")

    def test_adding_a_scene_renumbers_and_selects_it(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        self.assertEqual(len(window.current_project.scenes), 2)
        self.assertEqual([scene.id for scene in window.current_project.scenes], [1, 2])
        self.assertEqual(window.scene_list.currentRow(), 1)

    def test_the_last_scene_cannot_be_deleted(self) -> None:
        """Checked without the modal dialog, which would block the test run."""
        window = self.make_window()
        self.assertFalse(window.can_delete_scene())
        window.on_add_scene()
        self.assertTrue(window.can_delete_scene())

    def test_deleting_a_scene_keeps_the_ids_contiguous(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        window.on_add_scene()
        window.scene_list.setCurrentRow(1)
        window.can_delete_scene()
        del window.current_project.scenes[1]
        window.current_project.renumber()
        self.assertEqual(
            [scene.id for scene in window.current_project.scenes], [1, 2]
        )

    def test_reordering_swaps_and_renumbers(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        window.scene_list.setCurrentRow(0)
        window.on_move_scene_down()
        self.assertEqual(
            [scene.id for scene in window.current_project.scenes], [1, 2]
        )

    def test_picking_a_background_from_the_library(self) -> None:
        window = self.make_window()
        item = window.background_list.item(0)
        window._on_background_picked(item)
        self.assertTrue(window.current_project.scenes[0].image_file)

    def test_picking_a_character_adds_it_to_the_scene(self) -> None:
        window = self.make_window()
        item = window.character_list.item(0)
        window._on_character_picked(item)
        self.assertEqual(len(window.current_project.scenes[0].characters), 1)

    def test_the_storytellers_are_listed_too(self) -> None:
        """The third list is the frame's cast, named by registry keys."""
        window = self.make_window()
        self.assertGreater(window.narrator_list.count(), 0)
        keys = [
            window.narrator_list.item(row).data(Qt.UserRole)
            for row in range(window.narrator_list.count())
        ]
        self.assertNotIn(None, keys)
        self.assertTrue(all(isinstance(key, str) and key for key in keys))
        self.assertNotIn("_readme", keys)

    def test_picking_a_storyteller_makes_it_the_frames_host(self) -> None:
        window = self.make_window()
        item = window.narrator_list.item(0)
        key = item.data(Qt.UserRole)
        window._on_narrator_picked(item)

        frame = window.current_project.story_frame
        self.assertEqual(frame.use, key)
        self.assertTrue(frame.enabled)
        self.assertTrue(frame.show_narrator)
        # The frame panel follows, or its checkbox would keep saying off.
        self.assertTrue(window.frame_panel.enabled.isChecked())
        self.assertIn(key, window.status.text())

    def test_a_storyteller_round_trips_through_the_saved_script(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        window = self.make_window()
        window._on_narrator_picked(window.narrator_list.item(0))
        window.on_save()

        raw = json.loads(self.script.read_text(encoding="utf-8"))
        self.assertEqual(
            raw["story_frame"]["use"],
            window.current_project.story_frame.use,
        )
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"studio wrote an invalid script.json: {error}")

    def test_every_library_row_drags_as_the_mime_the_canvas_reads(self) -> None:
        """
        The drag has to carry the stage's own mime, or the drop is ignored.

        A stock QListWidget hands over `x-qabstractitemmodeldatalist`,
        which is why dragging from the library used to do nothing.
        """
        window = self.make_window()
        expected = (
            (window.background_list, MIME_BACKGROUND),
            (window.character_list, MIME_CHARACTER),
            (window.narrator_list, MIME_NARRATOR),
        )
        for widget, mime_type in expected:
            with self.subTest(mime=mime_type):
                self.assertEqual(widget.mime_type, mime_type)
                item = widget.item(0)
                self.assertIsNotNone(item)
                mime = widget.row_mime(item)
                self.assertTrue(mime.hasFormat(mime_type))
                payload = bytes(mime.data(mime_type)).decode("utf-8")
                self.assertEqual(payload, str(item.data(Qt.UserRole)))
                self.assertNotIn("application/x-qabstractitemmodeldatalist",
                                 mime.formats())

    def test_a_row_mime_drops_onto_the_stage(self) -> None:
        """The payload the drag carries is the one the drop consumes."""
        from PySide6.QtGui import QDropEvent

        window = self.make_window()
        window.canvas.select(0) if window.canvas.scene is not None else None
        key = window.narrator_list.item(0).data(Qt.UserRole)
        mime = window.narrator_list.row_mime(window.narrator_list.item(0))
        stage = window.canvas._stage_rect()
        window.canvas.dropEvent(
            QDropEvent(QPointF(stage.center()), Qt.CopyAction, mime,
                       Qt.LeftButton, Qt.NoModifier)
        )
        self.assertEqual(window.current_project.story_frame.use, key)

    def test_an_asset_list_drag_starts_with_the_row_mime(self) -> None:
        """`startDrag` must ask for our mime, not the model's default blob."""
        captured = {}

        class _FakeDrag:
            def __init__(self, parent=None):
                captured["parent"] = parent

            def setMimeData(self, mime):  # noqa: N802
                captured["mime"] = mime

            def setPixmap(self, pixmap):
                captured["pixmap"] = pixmap

            def exec(self, actions):
                captured["actions"] = actions
                return actions

        import autovid.presentation.studio.window as window_module

        window = self.make_window()
        widget = window.character_list
        widget.setCurrentRow(0)
        real = window_module.QDrag
        window_module.QDrag = _FakeDrag
        try:
            widget.startDrag(Qt.CopyAction)
        finally:
            window_module.QDrag = real

        self.assertIn("mime", captured)
        mime = captured["mime"]
        self.assertTrue(mime.hasFormat(MIME_CHARACTER))
        payload = bytes(mime.data(MIME_CHARACTER)).decode("utf-8")
        self.assertEqual(payload, widget.item(0).data(Qt.UserRole))

    def test_an_asset_list_drag_with_nothing_selected_does_nothing(self) -> None:
        window = self.make_window()
        widget = window.character_list
        widget.clearSelection()
        # No selection: no QDrag is constructed at all, so this returns
        # instead of raising on a missing item.
        widget.startDrag(Qt.CopyAction)

    def test_saving_writes_a_valid_script(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        window.on_save()

        raw = json.loads(self.script.read_text(encoding="utf-8"))
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"studio wrote an invalid script.json: {error}")

    def test_saving_twice_keeps_a_backup(self) -> None:
        window = self.make_window()
        window.on_save()
        window.current_project.title = "Doi tieu de"
        window.on_save()
        backup = self.script.with_suffix(".bak")
        self.assertTrue(backup.exists())
        self.assertEqual(
            json.loads(backup.read_text(encoding="utf-8"))["video_metadata"]["title"],
            "Video thu nghiem",
        )

    def test_inspector_edits_reach_the_scene(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        inspector = window.inspector
        inspector.enter_type.setCurrentText("drop_bounce")
        inspector.enter_duration.setValue(650)
        inspector.idle_type.setCurrentText("bob_sway")
        inspector.size.setValue(0.42)

        placement = window.current_project.scenes[0].characters[0]
        self.assertEqual(placement.enter_type, "drop_bounce")
        self.assertEqual(placement.enter_duration_ms, 650)
        self.assertEqual(placement.idle_type, "bob_sway")
        self.assertAlmostEqual(placement.height, 0.42)

    def test_picking_an_effect_without_a_length_gets_one(self) -> None:
        """A zero-length entrance renders as nothing; fill the default in."""
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        placement = window.current_project.scenes[0].characters[0]

        inspector = window.inspector
        inspector.enter_type.setCurrentText("none")
        inspector.enter_duration.setValue(0)
        self.assertEqual(placement.enter_duration_ms, 0)

        inspector.enter_type.setCurrentText("fly_in")
        self.assertEqual(placement.enter_type, "fly_in")
        self.assertGreater(placement.enter_duration_ms, 0)
        self.assertEqual(inspector.enter_duration.value(),
                         placement.enter_duration_ms)

    def test_a_length_the_author_set_is_kept_when_switching_effect(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        inspector = window.inspector
        inspector.enter_duration.setValue(900)
        inspector.enter_type.setCurrentText("fly_in")
        self.assertEqual(
            window.current_project.scenes[0].characters[0].enter_duration_ms,
            900,
        )

    def test_a_picked_idle_gets_an_amplitude_you_can_see(self) -> None:
        """Four pixels of nothing is what made the idle look broken."""
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        inspector = window.inspector
        placement = window.current_project.scenes[0].characters[0]
        self.assertEqual(placement.idle_amplitude_px, 4)

        inspector.idle_type.setCurrentText("bob_sway")
        self.assertEqual(placement.idle_type, "bob_sway")
        self.assertEqual(placement.idle_amplitude_px, 16)
        self.assertAlmostEqual(placement.idle_period_s, 2.4)
        self.assertEqual(inspector.idle_amplitude.value(), 16)

    def test_a_leaning_idle_shows_its_amplitude_in_degrees(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        inspector = window.inspector
        placement = window.current_project.scenes[0].characters[0]

        inspector.idle_type.setCurrentText("lean")
        self.assertEqual(placement.idle_amplitude_px, 30)
        self.assertIn("°", inspector.idle_amplitude.suffix())
        # Forty-five degrees is the schema's ceiling for a lean, so the spin
        # box stops there rather than writing a value the parser rejects.
        self.assertEqual(inspector.idle_amplitude.maximum(), 45)

        inspector.idle_type.setCurrentText("sway")
        self.assertEqual(inspector.idle_amplitude.suffix(), " px")
        self.assertEqual(inspector.idle_amplitude.maximum(), 120)
        # Thirty is now thirty pixels rather than thirty degrees: the number
        # is kept because the author has now seen it and owns it, and it is
        # still a sway you can see.
        self.assertEqual(placement.idle_amplitude_px, 30)

    def test_an_amplitude_the_author_tuned_survives_a_type_switch(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        inspector = window.inspector
        placement = window.current_project.scenes[0].characters[0]

        inspector.idle_type.setCurrentText("bob")
        inspector.idle_amplitude.setValue(40)
        inspector.idle_type.setCurrentText("sway")
        self.assertEqual(placement.idle_amplitude_px, 40)

    def test_reopening_a_lean_shows_degrees_again(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        window.inspector.idle_type.setCurrentText("lean")
        window.inspector.idle_amplitude.setValue(24)

        inspector = window.inspector
        inspector.reload()
        self.assertEqual(inspector.idle_type.currentText(), "lean")
        self.assertIn("°", inspector.idle_amplitude.suffix())
        self.assertEqual(inspector.idle_amplitude.value(), 24)

    def test_a_saved_lean_survives_a_reopen(self) -> None:
        window = self.make_window()
        window._on_character_picked(window.character_list.item(0))
        window.inspector.idle_type.setCurrentText("lean")
        window.on_save()

        from autovid.domain.script import parse_script

        raw = json.loads(self.script.read_text(encoding="utf-8"))
        idle = parse_script(raw).scenes[0].characters[0].idle
        self.assertEqual(idle.type, "lean")
        self.assertEqual(idle.amplitude_px, 30)

    def test_scene_controls_reach_the_scene(self) -> None:
        window = self.make_window()
        inspector = window.inspector
        inspector.motion_type.setCurrentText("pan_left")
        inspector.transition_type.setCurrentText("dissolve")
        inspector.transition_duration.setValue(1.25)

        scene = window.current_project.scenes[0]
        self.assertEqual(scene.motion.type, "pan_left")
        self.assertTrue(scene.motion.enabled)
        self.assertEqual(scene.transition.type, "dissolve")
        self.assertAlmostEqual(scene.transition.duration, 1.25)

    def test_frame_override_selects_the_scenes_look(self) -> None:
        window = self.make_window()
        window.inspector.frame_override.setCurrentIndex(2)  # "Khong khung"
        self.assertIs(window.current_project.scenes[0].story_frame_enabled, False)
        window.inspector.frame_override.setCurrentIndex(1)  # "Co khung"
        self.assertIs(window.current_project.scenes[0].story_frame_enabled, True)
        window.inspector.frame_override.setCurrentIndex(0)  # inherit
        self.assertIsNone(window.current_project.scenes[0].story_frame_enabled)

    def test_frame_panel_toggles_the_project_wide_frame(self) -> None:
        window = self.make_window()
        window.frame_panel.enabled.setChecked(True)
        self.assertTrue(window.current_project.story_frame.enabled)
        window.frame_panel.enabled.setChecked(False)
        self.assertFalse(window.current_project.story_frame.enabled)

    def test_frame_panel_warns_when_the_panel_crowds_the_host(self) -> None:
        window = self.make_window()
        window.frame_panel.enabled.setChecked(True)
        window.frame_panel.frame_width.setValue(0.95)
        self.assertIn("khong bi che", window.frame_panel.warning.text())

    def test_metadata_fields_reach_the_project(self) -> None:
        window = self.make_window()
        window.title_field.setText("Moi tieu de")
        window._on_metadata_changed()
        self.assertEqual(window.current_project.title, "Moi tieu de")

        window.resolution_box.setCurrentText("1280x720")
        self.assertEqual(window.current_project.resolution, (1280, 720))

    def test_scrubber_drives_the_ken_burns_progress(self) -> None:
        window = self.make_window()
        window.scene_list.setCurrentRow(0)
        self.assertTrue(window.scrub.isEnabled())
        window.scrub.setValue(100)
        self.assertAlmostEqual(window.canvas.progress, 1.0)

    def test_the_scrubber_stays_usable_without_ken_burns(self) -> None:
        """The clock runs entrances, walks and captions too, so a scene
        that stands perfectly still still has something to scrub through."""
        window = self.make_window()
        window.scene_list.setCurrentRow(0)
        window.inspector.motion_enabled.setChecked(False)
        window._build_scrubber()
        self.assertTrue(window.scrub.isEnabled())

    def test_play_runs_the_scene_and_stops_at_the_end(self) -> None:
        window = self.make_window()
        window.scene_list.setCurrentRow(0)

        window.play_button.click()
        self.assertTrue(window._play_timer.isActive())
        self.assertEqual(window.play_button.text(), "Dung")

        # Drive the clock by hand: the point is the animation, not sitting
        # through three real seconds of timer while the test runs.
        for _ in range(1000):
            window._on_play_tick()
            if not window._play_timer.isActive():
                break
        self.assertFalse(window._play_timer.isActive())
        self.assertAlmostEqual(window.canvas.progress, 1.0)
        self.assertEqual(window.play_button.text(), "Chay")

    def test_play_carries_the_scrubber_with_it(self) -> None:
        window = self.make_window()
        window.scene_list.setCurrentRow(0)
        window.canvas.set_progress(0.0)

        window.start_play()
        window._on_play_tick()
        window.pause_play()

        self.assertGreater(window.canvas.progress, 0.0)
        self.assertGreater(window.scrub.value(), 0)

    def test_changing_scene_stops_the_preview(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        window.start_play()
        self.assertTrue(window._play_timer.isActive())

        window.scene_list.setCurrentRow(0)
        self.assertFalse(window._play_timer.isActive())

    def test_playing_a_finished_scene_starts_over(self) -> None:
        window = self.make_window()
        window.scene_list.setCurrentRow(0)
        window.canvas.set_progress(1.0)

        window.start_play()
        self.assertEqual(window.canvas.progress, 0.0)
        self.assertEqual(window.scrub.value(), 0)
        window.pause_play()


class AssetListTest(unittest.TestCase):
    """The library row, in isolation: what its drag hands the stage."""

    def _list(self, mime: str) -> AssetList:
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        widget = AssetList(mime)
        item = QListWidgetItem("chi pheo")
        item.setData(Qt.UserRole, "assets/characters/chi_pheo.png")
        widget.addItem(item)
        return widget

    def test_the_row_mime_names_its_format_and_payload(self) -> None:
        widget = self._list(MIME_CHARACTER)
        mime = widget.row_mime(widget.item(0))
        self.assertEqual(mime.formats(), [MIME_CHARACTER])
        self.assertEqual(
            bytes(mime.data(MIME_CHARACTER)).decode("utf-8"),
            "assets/characters/chi_pheo.png",
        )

    def test_the_row_mime_carries_a_registry_key_for_a_narrator(self) -> None:
        widget = self._list(MIME_NARRATOR)
        widget.item(0).setData(Qt.UserRole, "ke_su")
        mime = widget.row_mime(widget.item(0))
        self.assertEqual(bytes(mime.data(MIME_NARRATOR)).decode("utf-8"), "ke_su")

    def test_drag_is_enabled_and_the_stage_is_the_only_target(self) -> None:
        from PySide6.QtWidgets import QAbstractItemView

        widget = self._list(MIME_BACKGROUND)
        self.assertTrue(widget.dragEnabled())
        self.assertEqual(widget.dragDropMode(), QAbstractItemView.DragOnly)


class SchemaRoundTripTest(StudioTestCase):
    """What the studio writes has to satisfy the real schema."""

    def _assert_valid(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        raw = json.loads(self.script.read_text(encoding="utf-8"))
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"studio wrote an invalid script.json: {error}")

    def test_overlays_and_impact_survive_a_save(self) -> None:
        from autovid.presentation.studio.window import StudioWindow

        window = StudioWindow()
        window.resize(1200, 800)
        window.open_project(self.script)

        window.overlay_panel.on_add()
        # The window holds its own Project instance; the fixture's copy is
        # a separate load of the same file.
        overlay = window.current_project.scenes[0].overlays[0]
        overlay.text = "Bai 1 - Cuoc gap"
        overlay.position = "top"
        overlay.font_size = 96
        overlay.start_offset_ms = 500
        overlay.end_offset_ms = 4500

        window.impact_panel.enabled.setChecked(True)
        window.impact_panel.intensity.setValue(0.18)
        window.impact_panel.shake.setValue(14)
        window.impact_panel.flash.setCurrentText("white")

        window.on_save()
        self._assert_valid()

        written = json.loads(self.script.read_text(encoding="utf-8"))
        scene = written["scenes"][0]
        self.assertEqual(scene["text_overlays"][0]["text"], "Bai 1 - Cuoc gap")
        self.assertEqual(scene["text_overlays"][0]["position"], "top")
        self.assertEqual(scene["impact"]["shake_px"], 14)
        self.assertEqual(scene["impact"]["flash"], "white")

    def test_opened_again_the_overlays_come_back(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].overlays = [
            TextOverlay(text="Tieu de", position="bottom_right", font_size=64)
        ]
        self.project.scenes[0].impact = Impact(
            enabled=True, at_sentence=2, intensity=0.2, shake_px=8, flash="black"
        )
        ProjectStore.save(self.project)

        reopened = ProjectStore.open(self.script)
        scene = reopened.scenes[0]
        self.assertEqual(len(scene.overlays), 1)
        self.assertEqual(scene.overlays[0].position, "bottom_right")
        self.assertTrue(scene.impact.enabled)
        self.assertEqual(scene.impact.at_sentence, 2)
        self.assertEqual(scene.impact.flash, "black")

    def test_an_empty_overlay_is_dropped(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].overlays = [TextOverlay(text="   ")]
        ProjectStore.save(self.project)
        reopened = ProjectStore.open(self.script)
        self.assertEqual(reopened.scenes[0].overlays, [])

    def test_absent_impact_means_no_punch(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        ProjectStore.save(self.project)
        reopened = ProjectStore.open(self.script)
        self.assertFalse(reopened.scenes[0].impact.enabled)

    def test_impact_offset_is_written_when_not_by_sentence(self) -> None:
        from autovid.presentation.studio.store import ProjectStore

        self.project.scenes[0].impact = Impact(
            enabled=True, at_sentence=None, at_offset_ms=900
        )
        ProjectStore.save(self.project)
        written = json.loads(self.script.read_text(encoding="utf-8"))
        self.assertEqual(written["scenes"][0]["impact"]["at_offset_ms"], 900)
        self.assertNotIn("at_sentence", written["scenes"][0]["impact"])


class ScenePanelTest(StudioTestCase):
    """
    The scene summary table: one row per scene, one column per field.

    It is the author's read-only view of the whole script, so what matters
    is that it says the same thing the scenes say -- and keeps saying it
    after the project is edited underneath it.
    """

    def _panel(self, scenes=None, frame=None):
        from autovid.presentation.studio.scenepanel import ScenePanel

        panel = ScenePanel()
        if scenes is not None:
            panel.set_project_scenes(scenes, frame)
        else:
            panel.set_project(self.project)
        return panel

    def _cell(self, panel, row, column):
        return panel.item(row, column).text()

    def _headers(self, panel):
        return [
            panel.horizontalHeaderItem(c).text()
            for c in range(panel.columnCount())
        ]

    def test_one_row_per_scene_and_named_columns(self) -> None:
        from autovid.presentation.studio.store import Scene

        self.project.scenes = [
            Scene(id=1, text="Mot", image_file="images/backgrounds/cong_lang.png"),
            Scene(id=2, text="Hai"),
            Scene(id=3, text="Ba"),
        ]
        panel = self._panel()
        self.assertEqual(panel.rowCount(), 3)
        self.assertEqual(
            self._headers(panel),
            [
                "Hinh",
                "Noi dung thoai",
                "Chu de them",
                "Style",
                "Punch-up",
                "Nhan vat",
                "Khung hinh",
            ],
        )

    def test_the_column_names_survive_a_repaint(self) -> None:
        """`clear()` takes the header labels with it, so they must be put back."""
        from autovid.presentation.studio.store import Scene

        panel = self._panel()
        self.assertTrue(all(self._headers(panel)))
        self.project.scenes = [Scene(id=1, text="Mot"), Scene(id=2, text="Hai")]
        panel.set_project_dirty(self.project)
        self.assertTrue(all(self._headers(panel)), "headers vanished on repaint")

    def test_it_shows_each_scene_s_narration_and_picture(self) -> None:
        from autovid.presentation.studio.store import Scene

        self.project.scenes = [
            Scene(
                id=1,
                text="Chuyen mot",
                image_file="images/backgrounds/cong_lang.png",
            ),
            Scene(id=2, text="Chuyen hai", image_file="images/backgrounds/cong_lang.png"),
        ]
        panel = self._panel()
        self.assertEqual(self._cell(panel, 0, 1), "Chuyen mot")
        self.assertEqual(self._cell(panel, 1, 1), "Chuyen hai")
        self.assertEqual(self._cell(panel, 0, 0), "cong_lang.png")

    def test_a_scene_with_nothing_in_it_says_so_rather_than_going_blank(self) -> None:
        from autovid.presentation.studio.store import Scene

        panel = self._panel([Scene(id=1, text="", image_file="")], None)
        self.assertEqual(self._cell(panel, 0, 0), "(chua chon nen)")
        self.assertTrue(self._cell(panel, 0, 1))
        self.assertEqual(self._cell(panel, 0, 2), "(chua them chu de)")
        self.assertEqual(self._cell(panel, 0, 5), "(khong co)")

    def test_captions_and_cast_are_summarised(self) -> None:
        from autovid.presentation.studio.store import Placement, Scene, TextOverlay

        scene = Scene(
            id=1,
            text="Mot",
            overlays=[
                TextOverlay(text="Chu de 1", position="top"),
                TextOverlay(text="Chu de 2", position="bottom"),
            ],
            characters=[
                Placement(image_file="assets/characters/chi_pheo.png"),
                Placement(image_file="assets/characters/thi_no.png"),
            ],
        )
        panel = self._panel([scene], None)
        self.assertEqual(self._cell(panel, 0, 2), "Chu de 1 Chu de 2")
        self.assertEqual(
            self._cell(panel, 0, 5), "1.chi_pheo.png, 2.thi_no.png"
        )

    def test_the_frame_column_follows_the_project_and_the_opt_out(self) -> None:
        """The frame is project-wide; a scene only carries the opt-out."""
        from autovid.presentation.studio.store import Scene, StoryFrame

        framed = StoryFrame(enabled=True, use="ke_su")
        plain = [Scene(id=1, text="Mot"), Scene(id=2, text="Hai", story_frame_enabled=False)]
        panel = self._panel(plain, framed)
        self.assertIn("ke_su", self._cell(panel, 0, 6))
        self.assertEqual(self._cell(panel, 1, 6), "khong")

        # With the whole frame switched off, no scene draws it.
        panel.set_project_scenes(plain, StoryFrame(enabled=False, use="ke_su"))
        self.assertEqual(self._cell(panel, 0, 6), "khong")

    def test_the_frame_column_does_not_invent_a_host(self) -> None:
        from autovid.presentation.studio.store import Scene, StoryFrame

        panel = self._panel([Scene(id=1, text="Mot")], StoryFrame(enabled=True))
        self.assertEqual(self._cell(panel, 0, 6), "on")

    def test_the_table_is_read_only(self) -> None:
        from PySide6.QtWidgets import QAbstractItemView

        panel = self._panel()
        self.assertEqual(panel.editTriggers(), QAbstractItemView.NoEditTriggers)

    def test_a_row_maps_back_to_the_scene_it_describes(self) -> None:
        from autovid.presentation.studio.store import Scene

        first, second = Scene(id=1, text="Mot"), Scene(id=2, text="Hai")
        panel = self._panel([first, second], None)
        self.assertEqual(panel.scene_row(first), 0)
        self.assertEqual(panel.scene_row(second), 1)
        self.assertEqual(panel.scene_row(Scene(id=9, text="Khong co")), -1)
        self.assertEqual(panel.scene_row(None), -1)

        panel.selectRow(1)
        self.assertIs(panel.selected_scene(), second)

    def test_nothing_selected_is_none_rather_than_an_error(self) -> None:
        from autovid.presentation.studio.store import Scene

        panel = self._panel([Scene(id=1, text="Mot")], None)
        panel.clearSelection()
        panel.setCurrentCell(0, 0)
        panel.setCurrentCell(-1, -1)
        self.assertIsNone(panel.selected_scene())

    def test_selecting_a_scene_that_is_not_shown_clears_the_selection(self) -> None:
        from autovid.presentation.studio.store import Scene

        shown = Scene(id=1, text="Mot")
        panel = self._panel([shown], None)
        panel.selectRow(0)
        panel.select_scene(Scene(id=9, text="Khong co"))
        self.assertIsNone(panel.selected_scene())
        self.assertEqual(panel.selectedItems(), [])

    def test_the_panel_repaints_after_an_edit_and_keeps_its_place(self) -> None:
        from autovid.presentation.studio.store import Scene

        self.project.scenes = [
            Scene(id=1, text="Mot"),
            Scene(id=2, text="Hai"),
        ]
        panel = self._panel()
        panel.select_scene(self.project.scenes[1])

        self.project.scenes[1].text = "Hai da sua"
        self.project.scenes.append(Scene(id=3, text="Ba"))
        panel.set_project_dirty(self.project)

        self.assertEqual(panel.rowCount(), 3)
        self.assertEqual(self._cell(panel, 1, 1), "Hai da sua")
        # The same scene is still the highlighted one, not a different row.
        self.assertIs(panel.selected_scene(), self.project.scenes[1])

    def test_an_empty_project_paints_an_empty_table(self) -> None:
        from autovid.presentation.studio.store import Project

        panel = self._panel()
        panel.set_project(None)
        self.assertEqual(panel.rowCount(), 0)


class ScenePanelWindowTest(StudioTestCase):
    """The panel as the author meets it: inside the window, beside the list."""

    def make_window(self):
        from autovid.presentation.studio.window import StudioWindow

        window = StudioWindow()
        window.resize(1400, 900)
        window.open_project(self.script)
        return window

    def test_opening_a_project_fills_the_panel(self) -> None:
        window = self.make_window()
        self.assertEqual(
            window.scene_panel.rowCount(), len(window.current_project.scenes)
        )

    def test_the_panel_follows_the_scene_list(self) -> None:
        window = self.make_window()
        window.on_add_scene()

        for row in range(window.scene_list.count()):
            window.scene_list.setCurrentRow(row)
            self.assertIs(
                window.scene_panel.selected_scene(),
                window.current_project.scenes[row],
            )
            self.assertEqual(window.scene_panel.scene_row(window.current_project.scenes[row]), row)

    def test_adding_a_scene_adds_a_row(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        self.assertEqual(window.scene_panel.rowCount(), 2)

    def test_deleting_a_scene_drops_its_row(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        window.scene_list.setCurrentRow(1)
        window.on_delete_scene()
        self.assertEqual(window.scene_panel.rowCount(), 1)

    def test_reordering_moves_the_row_with_the_scene(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        # The added scene starts blank; give it narration we can look for.
        window.current_project.scenes[1].text = "Hai"
        window.scene_panel.set_project_dirty(window.current_project)

        window.scene_list.setCurrentRow(1)
        window.on_move_scene_up()

        # scene 2 is now first, so its narration leads the table.
        self.assertEqual(window.scene_panel.item(0, 1).text(), "Hai")
        self.assertEqual(window.scene_panel.item(1, 1).text(), "Mot")

    def test_editing_the_stage_refreshes_the_row(self) -> None:
        window = self.make_window()
        window.current_project.scenes[0].text = "Doan thoai vua sua"
        window._on_canvas_changed()
        self.assertEqual(window.scene_panel.item(0, 1).text(), "Doan thoai vua sua")

    def _click_row(self, window, row: int) -> None:
        """A real mouse click on that row, the way the author picks one."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        panel = window.scene_panel
        spot = panel.visualItemRect(panel.item(row, 0)).center()
        QTest.mouseClick(panel.viewport(), Qt.LeftButton, Qt.NoModifier, spot)

    def test_clicking_a_row_opens_that_scene(self) -> None:
        window = self.make_window()
        window.on_add_scene()
        window.scene_list.setCurrentRow(0)

        self._click_row(window, 1)
        self.assertIs(window.canvas.scene, window.current_project.scenes[1])
        self.assertIs(window.scene_panel.selected_scene(), window.current_project.scenes[1])
        self.assertEqual(window.scene_list.currentRow(), 1)

    def test_repainting_the_table_does_not_switch_scene(self) -> None:
        """Only a click asks for a new scene; edits must not move the author."""
        window = self.make_window()
        window.on_add_scene()
        window.scene_list.setCurrentRow(1)
        open_scene = window.canvas.scene

        window._on_canvas_changed()
        window._on_inspector_changed()
        window._on_panel_changed()
        window._on_frame_changed()
        window._refresh_scenes()
        window.scene_panel.set_project_dirty(window.current_project)

        self.assertIs(window.canvas.scene, open_scene)

    def test_changing_the_frame_keeps_the_scene_you_are_on(self) -> None:
        """The frame is project-wide, so editing it used to jump to scene 1."""
        window = self.make_window()
        window.on_add_scene()
        window.scene_list.setCurrentRow(1)
        open_scene = window.canvas.scene

        window._on_frame_changed()
        self.assertIs(window.canvas.scene, open_scene)

    def test_a_row_out_of_range_names_no_scene(self) -> None:
        from autovid.presentation.studio.scenepanel import ScenePanel

        panel = ScenePanel()
        panel.set_project_scenes([])
        self.assertIsNone(panel.scene_at(0))
        self.assertIsNone(panel.scene_at(-1))

    def test_the_table_emits_scene_picked_only_on_a_click(self) -> None:
        from autovid.presentation.studio.scenepanel import ScenePanel

        panel = ScenePanel()
        panel.set_project(self.project)
        heard = []
        panel.scene_picked.connect(heard.append)

        panel.selectRow(0)
        panel.set_project_dirty(self.project)
        self.assertEqual(heard, [], "a repaint asked for a scene change")

        panel._on_cell_clicked(0, 0)
        self.assertEqual(len(heard), 1)
        self.assertIs(heard[0], self.project.scenes[0])

if __name__ == "__main__":
    unittest.main()