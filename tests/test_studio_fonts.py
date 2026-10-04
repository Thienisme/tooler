"""The caption fonts: the bundle, the catalogue and the picker that uses them.

Two things are being held here on purpose.

**Every face must render Vietnamese.**  Google Fonts splits each family into
subsets, and eight of the first fourteen candidates this project tried could
not draw a single one of `ớ ề ữ` -- they render them as blank boxes that
look like a typo rather than like a missing font.  A face that fails this
test is replaced, not excused.

**The preview must draw the same face the render will.**  The canvas used to
paint every caption in the system font, so a caption chosen for its
handwriting was judged on a face nobody would ever see.
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
sys.path.insert(0, str(REPO_ROOT / "tools"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from autovid.domain.fonts import (  # noqa: E402
    CAPTION_FONTS,
    DEFAULT_FONT_KEY,
    FONT_BY_KEY,
    FONT_BY_PATH,
    FONT_MOODS,
    font_for,
    font_reference,
    fonts_by_mood,
)
from autovid.domain.script import parse_script  # noqa: E402
from autovid.infrastructure.image.fonts import resolve_font  # noqa: E402
from autovid.presentation.studio.assets import AssetLibrary  # noqa: E402
from autovid.presentation.studio.canvas import StageCanvas  # noqa: E402
from autovid.presentation.studio.overlays import OverlayPanel  # noqa: E402
from autovid.presentation.studio.store import ProjectStore, TextOverlay  # noqa: E402

from check_font_coverage import missing_glyphs  # noqa: E402

_APP = QApplication.instance() or QApplication([])

MOOD_KEYS = {key for key, _label in FONT_MOODS}


def _script_with(overlay: dict) -> dict:
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
                "image_file": "projects/demo_story_inside/images/backgrounds/image.png",
                "text_overlays": [overlay],
            }
        ],
    }


class FontCatalogueTest(unittest.TestCase):
    """The bundle itself, which no amount of wiring can repair."""

    def test_every_face_is_shipped_with_its_licence(self):
        """A font without its licence is a font the repo may not ship."""
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                self.assertTrue(
                    (REPO_ROOT / font.path).is_file(),
                    f"{font.key} names {font.path}, which is not in the repo",
                )
                if font.licence is None:
                    continue
                self.assertTrue(
                    (REPO_ROOT / "assets/fonts/licenses" / font.licence).is_file(),
                    f"{font.key} claims {font.licence}, which is not in the repo",
                )

    def test_every_fetched_face_is_shipped_with_a_licence(self):
        """Anything this project fetched carries its terms; `handwriting` did not."""
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                if font.key == "handwriting":
                    # The project's own face, older than this catalogue.  It
                    # is not claimed here, and pretending otherwise would be
                    # a licence the repo cannot show.
                    self.assertIsNone(font.licence)
                    continue
                self.assertIsNotNone(font.licence)

    def test_no_licence_file_is_left_for_a_font_we_dropped(self):
        """A licence for a font that is no longer shipped is a loose promise."""
        shipped = {
            font.licence for font in CAPTION_FONTS if font.licence
        }
        present = {
            path.name
            for path in (REPO_ROOT / "assets/fonts/licenses").glob("*.txt")
        }
        self.assertEqual(present - shipped, set())

    def test_every_face_can_draw_the_vietnamese_alphabet(self):
        """The whole reason this catalogue is short and this test exists."""
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                gaps = missing_glyphs(REPO_ROOT / font.path)
                self.assertEqual(
                    gaps,
                    set(),
                    f"{font.label} cannot draw {''.join(sorted(gaps))}",
                )

    def test_keys_and_paths_are_unique(self):
        keys = [font.key for font in CAPTION_FONTS]
        paths = [font.path for font in CAPTION_FONTS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(paths), len(set(paths)))

    def test_every_face_belongs_to_a_declared_mood(self):
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                self.assertIn(font.mood, MOOD_KEYS)

    def test_every_mood_has_at_least_one_face(self):
        """A mood with nothing in it is an empty group in the picker."""
        for key, _label, faces in fonts_by_mood():
            with self.subTest(mood=key):
                self.assertTrue(faces, "the picker would show an empty group")

    def test_the_default_face_is_in_the_catalogue(self):
        self.assertIn(DEFAULT_FONT_KEY, FONT_BY_KEY)

    def test_a_key_resolves_to_the_bundled_file(self):
        self.assertEqual(
            font_reference("bangers"),
            FONT_BY_KEY["bangers"].path,
        )

    def test_something_that_is_not_a_key_is_left_alone(self):
        """Old scripts name a path, and that path is theirs."""
        self.assertEqual(
            font_reference("assets/fonts/handwriting.ttf"),
            "assets/fonts/handwriting.ttf",
        )

    def test_a_face_is_findable_by_key_or_by_path(self):
        face = font_for("pacifico")
        self.assertIsNotNone(face)
        self.assertIs(font_for(face.path), face)
        self.assertIsNone(font_for("khong-co-that.ttf"))


class FontResolutionTest(unittest.TestCase):
    """Key to file, from anywhere in the project, without a fallback."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_fonts_")
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_a_bundled_face_resolves_from_an_empty_workspace(self):
        """The fonts live in the repo, not in the project being edited."""
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                resolution = resolve_font(font.key, self.workspace)
                self.assertFalse(resolution.used_fallback)
                self.assertEqual(
                    resolution.path, REPO_ROOT / font.path
                )

    def test_a_raw_path_still_resolves(self):
        resolution = resolve_font(
            "assets/fonts/handwriting.ttf", self.workspace
        )
        self.assertFalse(resolution.used_fallback)
        self.assertTrue(str(resolution.path).endswith("handwriting.ttf"))

    def test_an_unknown_font_falls_back_to_a_system_face(self):
        resolution = resolve_font("khong-co-that.ttf", self.workspace)
        self.assertTrue(resolution.used_fallback)
        self.assertEqual(resolution.requested, "khong-co-that.ttf")

    def test_a_caption_with_no_font_gets_the_default_face(self):
        script = parse_script(_script_with({"text": "Oi"}))
        self.assertEqual(
            script.scenes[0].text_overlays[0].font, DEFAULT_FONT_KEY
        )

    def test_a_caption_may_name_a_key_or_a_path(self):
        for reference in ("lobster", "assets/fonts/handwriting.ttf"):
            with self.subTest(font=reference):
                script = parse_script(
                    _script_with({"text": "Oi", "font": reference})
                )
                self.assertEqual(
                    script.scenes[0].text_overlays[0].font, reference
                )

    def test_a_keyed_caption_survives_a_save_and_a_reopen(self):
        raw = _script_with({"text": "Oi", "font": "bangers"})
        path = self.workspace / "script.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        store = ProjectStore.open(path)
        store.scenes[0].overlays.append(TextOverlay(text="Oi", font="bangers"))
        ProjectStore.save(store)

        script = parse_script(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(script.scenes[0].text_overlays[0].font, "bangers")


class CaptionFontPickerTest(unittest.TestCase):
    """The studio: the choice is offered, and the preview honours it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_fontui_")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project_dir = self.root / "video_a"
        (self.project_dir / "images/backgrounds").mkdir(parents=True)
        from PIL import Image

        Image.new("RGB", (320, 180), (90, 90, 90)).save(
            self.project_dir / "images/backgrounds/cong_lang.png"
        )
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
        self.project = ProjectStore.open(script)
        self.library = AssetLibrary(self.project_dir, REPO_ROOT)
        self.canvas = StageCanvas(self.library)
        self.canvas.resize(800, 450)
        self.canvas.set_project(self.project)
        self.canvas.set_scene(self.project.scenes[0])

    def _panel(self) -> OverlayPanel:
        self.project.scenes[0].overlays.append(TextOverlay(text="Chu moi"))
        self.canvas.select_overlay(0)
        panel = OverlayPanel(self.canvas)
        panel.reload()
        return panel

    def test_the_picker_offers_every_bundled_face(self):
        panel = self._panel()
        offered = {
            panel.font.itemData(index)
            for index in range(panel.font.count())
        }
        for font in CAPTION_FONTS:
            with self.subTest(font=font.key):
                self.assertIn(font.key, offered)

    def test_the_picker_groups_the_faces_by_mood(self):
        """The text says which mood a face is for, not just its name."""
        panel = self._panel()
        texts = [
            panel.font.itemText(index)
            for index in range(panel.font.count())
            if panel.font.itemData(index)
        ]
        for _key, label in FONT_MOODS:
            self.assertTrue(
                any(text.startswith(label) for text in texts),
                f"no face is labelled {label}",
            )

    def test_choosing_a_face_writes_its_key_not_a_path(self):
        panel = self._panel()
        panel.font.setCurrentIndex(panel.font.findData("pacifico"))
        self.assertEqual(
            self.project.scenes[0].overlays[0].font, "pacifico"
        )

    def test_a_font_outside_the_catalogue_still_shows_its_own_name(self):
        """Every project written before the catalogue named a raw path."""
        self.project.scenes[0].overlays.append(
            TextOverlay(text="Chu cu", font="assets/fonts/handwriting.ttf")
        )
        self.canvas.select_overlay(0)
        panel = OverlayPanel(self.canvas)
        panel.reload()
        self.assertEqual(panel.font.currentData(), "assets/fonts/handwriting.ttf")

    def test_the_preview_draws_the_chosen_face(self):
        """Not the system font: the preview has to be the render's preview."""
        font = self.canvas.caption_font("pacifico", 48)
        self.assertIn("Pacifico", font.family())

    def test_the_preview_falls_back_for_a_font_it_cannot_read(self):
        font = self.canvas.caption_font("khong-co-that.ttf", 48)
        self.assertNotIn("khong-co-that", font.family())
        self.assertEqual(font.pixelSize(), 48)

    def test_two_faces_are_actually_different_families(self):
        """Otherwise the picker is a lie and the catalogue is a decoration."""
        comic = self.canvas.caption_font("bangers", 48)
        brush = self.canvas.caption_font("indie-flower", 48)
        self.assertNotEqual(comic.family(), brush.family())


if __name__ == "__main__":
    unittest.main()