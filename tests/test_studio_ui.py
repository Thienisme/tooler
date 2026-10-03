"""
The studio, exercised without a display.

Every test drives the real widgets through `QApplication` on the offscreen
platform: the point is that the wiring between canvas, inspector and store
holds, and that what the studio saves still validates.
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

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autovid.presentation.studio.assets import AssetLibrary  # noqa: E402
from autovid.presentation.studio.canvas import (  # noqa: E402
    MIME_BACKGROUND,
    MIME_CHARACTER,
    StageCanvas,
)
from autovid.presentation.studio.store import (  # noqa: E402
    Impact,
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
        path = self.library.resolve(
            "projects/demo_story_inside/images/backgrounds/village_gate.png"
        )
        self.assertIsNotNone(path)


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
        # At the peak the picture is pushed in further than at rest.
        canvas.set_progress(0.34 * 0.5)
        peak = canvas.impact_scale()[0]
        canvas.set_progress(1.0)
        self.assertGreater(peak, 1.0)
        self.assertAlmostEqual(canvas.impact_scale()[0], 1.0)

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

    def test_scrubber_is_disabled_without_motion(self) -> None:
        window = self.make_window()
        window.scene_list.setCurrentRow(0)
        window.inspector.motion_enabled.setChecked(False)
        window._build_scrubber()
        self.assertFalse(window.scrub.isEnabled())


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


if __name__ == "__main__":
    unittest.main()