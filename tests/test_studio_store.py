"""The studio store must never lose or invent script.json fields."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autovid.presentation.studio.store import (  # noqa: E402
    Motion,
    Placement,
    ProjectStore,
    Scene,
    StoryFrame,
)


def full_script() -> dict:
    return {
        "video_metadata": {
            "title": "Ke chuyen Vu Dai",
            "author": "Buffy",
            "resolution": "1920x1080",
            "fps": 30,
            "language": "vi",
            "target_minutes": [8.0, 20.0],
        },
        "tts_config": {"engine": "vieneu", "voice": "Minh Quan Pro"},
        "audio_config": {"master_volume": -14.0},
        "pacing": {"auto_pause": {"enabled": True}},
        "story_frame": {
            "enabled": True,
            "use": "ke_su",
            "style": "tv_retro",
            "x": 0.02,
            "y": 0.1,
            "width": 0.58,
            "height": 0.8,
        },
        "scenes": [
            {
                "id": 1,
                "text": "Scene mot",
                "image_file": "projects/demo_story_inside/images/backgrounds/village_gate.png",
                "transition_in": {"type": "fade", "duration": 0.6},
                "ken_burns": {
                    "enabled": True,
                    "type": "zoom_in",
                    "start_scale": 1.0,
                    "end_scale": 1.12,
                },
                "characters": [
                    {
                        "image_file": "projects/demo_story_inside/assets/characters/chi_pheo.png",
                        "x": 0.44,
                        "y": 0.74,
                        "height": 0.3,
                        "flip": True,
                        "enter": {
                            "type": "slide_in",
                            "from": "left",
                            "duration_ms": 500,
                        },
                        "idle": {"type": "bob_sway", "amplitude_px": 5, "period_s": 1.9},
                        "exit": {"type": "fade_out", "duration_ms": 350},
                    }
                ],
            }
        ],
    }


class StoreTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_store_")
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "script.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, data: dict) -> Path:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return self.path

    # -- loading ---------------------------------------------------------
    def test_loads_every_field(self) -> None:
        project = ProjectStore.open(self.write(full_script()))
        self.assertEqual(project.title, "Ke chuyen Vu Dai")
        self.assertEqual(project.resolution, (1920, 1080))
        self.assertEqual(project.fps, 30)
        self.assertTrue(project.story_frame.enabled)
        self.assertEqual(project.story_frame.style, "tv_retro")
        self.assertEqual(len(project.scenes), 1)

        scene = project.scenes[0]
        self.assertEqual(scene.motion.type, "zoom_in")
        self.assertEqual(scene.motion.end_scale, 1.12)
        self.assertEqual(scene.transition.type, "fade")
        self.assertEqual(scene.characters[0].flip, True)
        self.assertEqual(scene.characters[0].enter_type, "slide_in")
        self.assertEqual(scene.characters[0].enter_from, "left")

    def test_missing_file_yields_a_blank_project(self) -> None:
        project = ProjectStore.open(self.dir / "moi" / "script.json")
        self.assertEqual(len(project.scenes), 1)
        self.assertFalse(project.story_frame.enabled)

    def test_a_character_without_an_enter_block_gets_a_usable_one(self) -> None:
        """No `enter` key means the parser's default, not a dead effect."""
        data = full_script()
        data["scenes"][0]["characters"][0].pop("enter")
        project = ProjectStore.open(self.write(data))
        placement = project.scenes[0].characters[0]
        self.assertEqual(placement.enter_type, "fade_in")
        self.assertGreater(placement.enter_duration_ms, 0)

    def test_an_effect_asked_for_without_a_length_gets_the_default(self) -> None:
        data = full_script()
        data["scenes"][0]["characters"][0]["enter"] = {"type": "fly_in"}
        project = ProjectStore.open(self.write(data))
        placement = project.scenes[0].characters[0]
        self.assertEqual(placement.enter_type, "fly_in")
        self.assertGreater(placement.enter_duration_ms, 0)

    def test_an_explicit_none_stays_silent(self) -> None:
        data = full_script()
        data["scenes"][0]["characters"][0]["enter"] = {"type": "none"}
        project = ProjectStore.open(self.write(data))
        self.assertEqual(project.scenes[0].characters[0].enter_type, "none")

    def test_partial_document_does_not_crash(self) -> None:
        self.path.write_text('{"scenes": [{"id": 1}]}', encoding="utf-8")
        project = ProjectStore.open(self.path)
        self.assertEqual(project.fps, 30)
        self.assertEqual(project.resolution, (1920, 1080))
        self.assertEqual(project.scenes[0].motion.type, "zoom_in")

    def test_scene_ids_are_renumbered_contiguously(self) -> None:
        data = full_script()
        data["scenes"] = [
            {"id": 9, "text": "a", "image_file": "a.png"},
            {"id": 4, "text": "b", "image_file": "b.png"},
            {"id": 77, "text": "c", "image_file": "c.png"},
        ]
        project = ProjectStore.open(self.write(data))
        self.assertEqual([scene.id for scene in project.scenes], [1, 2, 3])

    def test_character_without_image_is_dropped(self) -> None:
        data = full_script()
        data["scenes"][0]["characters"] = [{"x": 0.5}, {"image_file": "a.png"}]
        project = ProjectStore.open(self.write(data))
        self.assertEqual(len(project.scenes[0].characters), 1)

    # -- saving ----------------------------------------------------------
    def test_round_trip_preserves_audio_settings(self) -> None:
        original = full_script()
        project = ProjectStore.open(self.write(original))
        ProjectStore.save(project)

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["tts_config"], original["tts_config"])
        self.assertEqual(written["audio_config"], original["audio_config"])
        self.assertEqual(written["pacing"], original["pacing"])

    def test_round_trip_preserves_scene_content(self) -> None:
        project = ProjectStore.open(self.write(full_script()))
        ProjectStore.save(project)
        written = json.loads(self.path.read_text(encoding="utf-8"))

        scene = written["scenes"][0]
        self.assertEqual(scene["text"], "Scene mot")
        self.assertEqual(scene["ken_burns"]["end_scale"], 1.12)
        self.assertEqual(scene["transition_in"]["type"], "fade")
        character = scene["characters"][0]
        self.assertTrue(character["flip"])
        self.assertEqual(character["enter"]["from"], "left")
        self.assertEqual(character["exit"]["type"], "fade_out")

    def test_save_keeps_the_previous_file_as_backup(self) -> None:
        self.write(full_script())
        project = ProjectStore.open(self.path)
        project.title = "Tieu de moi"
        ProjectStore.save(project)

        backup = self.path.with_suffix(".bak")
        self.assertTrue(backup.exists())
        restored = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(restored["video_metadata"]["title"], "Ke chuyen Vu Dai")

    def test_saved_document_passes_the_real_schema(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        project = ProjectStore.open(self.write(full_script()))
        ProjectStore.save(project)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"studio wrote an invalid script.json: {error}")

    def test_blank_project_saves_valid(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        project = ProjectStore.blank(self.path)
        project.scenes[0].image_file = "images/backgrounds/village_gate.png"
        project.scenes[0].characters = [
            Placement(image_file="assets/characters/chi_pheo.png", x=0.4)
        ]
        ProjectStore.save(project)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        try:
            parse_script(raw)
        except ScriptSchemaError as error:
            self.fail(f"blank project saved invalid: {error}")


class MotionTest(unittest.TestCase):

    def test_zero_travel_is_widened(self) -> None:
        motion = Motion.from_dict(
            {"enabled": True, "type": "zoom_in", "start_scale": 1.0, "end_scale": 1.0}
        )
        self.assertNotAlmostEqual(motion.start_scale, motion.end_scale)

    def test_disabled_motion_is_left_alone(self) -> None:
        motion = Motion.from_dict(
            {"enabled": False, "type": "none", "start_scale": 1.0, "end_scale": 1.0}
        )
        self.assertEqual(motion.scale_at(0.5), 1.0)

    def test_scale_interpolates(self) -> None:
        motion = Motion(enabled=True, type="zoom_in", start_scale=1.0, end_scale=1.2)
        self.assertAlmostEqual(motion.scale_at(0.5), 1.1)

    def test_pan_keeps_a_constant_scale(self) -> None:
        """A pan slides the window, so equal scales are correct here."""
        motion = Motion.from_dict(
            {"enabled": True, "type": "pan_right", "start_scale": 1.1, "end_scale": 1.1}
        )
        self.assertAlmostEqual(motion.start_scale, motion.end_scale)


class SceneTest(unittest.TestCase):

    def test_story_frame_opt_out_is_preserved(self) -> None:
        scene = Scene.from_dict({"id": 1, "story_frame": False})
        self.assertIs(scene.story_frame_enabled, False)
        self.assertIs(scene.to_dict()["story_frame"], False)

    def test_inherit_sends_no_flag(self) -> None:
        scene = Scene.from_dict({"id": 1})
        self.assertIsNone(scene.story_frame_enabled)
        self.assertNotIn("story_frame", scene.to_dict())

    def test_a_narrator_picture_round_trips_beside_a_registry_key(self) -> None:
        """`story_frame` names its narrator either by key or by picture."""
        frame = StoryFrame.from_dict(
            {"enabled": True, "image_file": "assets/narrators/co_giao.png"}
        )
        self.assertIsNone(frame.use)
        self.assertEqual(frame.image_file, "assets/narrators/co_giao.png")
        self.assertEqual(
            frame.to_dict()["image_file"], "assets/narrators/co_giao.png"
        )

        keyed = StoryFrame.from_dict({"enabled": True, "use": "ke_su"})
        self.assertEqual(keyed.use, "ke_su")
        self.assertIsNone(keyed.image_file)
        self.assertNotIn("image_file", keyed.to_dict())


class ProjectTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_proj_")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolution_round_trips_through_text(self) -> None:
        project = ProjectStore.blank(Path("script.json"))
        project.resolution = (1080, 1920)
        document = project.to_dict()
        self.assertEqual(document["video_metadata"]["resolution"], "1080x1920")
        reopened = ProjectStore.open(
            _write_json(Path(self._tmp.name) / "s.json", document)
        )
        self.assertEqual(reopened.resolution, (1080, 1920))


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()