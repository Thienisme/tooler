"""
Tests for `tools/auto_direct.py` — automatic character staging.

The tool is a script rather than a package module, so it is imported by
path here, like the other tool tests.  The behaviour worth pinning down is
the pure staging logic: slot assignment, the overlap gap, face-off flip,
sentence staggering, registry expansion, and what a dry run leaves alone.

Run with:
    python -m unittest tests.test_auto_direct
"""

from __future__ import annotations

import copy
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
SRC_DIR = PROJECT_ROOT / "src"
# Unconditional front inserts: the editable install already has src at the
# end of sys.path, so a guarded insert would leave the repo-root autovid.py
# shadowing the package under unittest (cwd = project root).
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(SRC_DIR))


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "auto_direct", TOOLS_DIR / "auto_direct.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auto_direct = load_tool()


REGISTRY = {
    "my_host": {
        "image_file": "assets/characters/my_host_01.png",
        "talk": ["assets/characters/my_host_02.png"],
        "poses": ["assets/characters/my_host_04.png"],
        "auto_pose_s": 2.5,
    }
}

EMPTY_SCRIPT = {"scenes": []}


def scene(scene_id: int, text: str, characters: list) -> dict:
    return {
        "id": scene_id,
        "text": text,
        "image_file": f"images/scene_{scene_id:03d}.png",
        "characters": characters,
    }


class RegistryTest(unittest.TestCase):
    def test_use_key_wins(self):
        key, entry = auto_direct.resolve_registry_entry(
            {"use": "my_host", "image_file": "other.png"}, REGISTRY
        )
        self.assertEqual(key, "my_host")
        self.assertEqual(entry["image_file"], "assets/characters/my_host_01.png")

    def test_resting_image_is_matched(self):
        key, _entry = auto_direct.resolve_registry_entry(
            {"image_file": "assets/characters/my_host_01.png"}, REGISTRY
        )
        self.assertEqual(key, "my_host")

    def test_unknown_sprite_resolves_to_nothing(self):
        key, entry = auto_direct.resolve_registry_entry(
            {"image_file": "assets/solo.png"}, REGISTRY
        )
        self.assertIsNone(key)
        self.assertEqual(entry, {})

    def test_merge_fills_gaps_only(self):
        merged = auto_direct.merge_registry_entry(
            {"image_file": "assets/characters/my_host_01.png", "idle": {"type": "shake"}},
            REGISTRY["my_host"],
        )
        # The script's own idle wins; the rest of the frame set rides in.
        self.assertEqual(merged["idle"], {"type": "shake"})
        self.assertEqual(merged["talk"], ["assets/characters/my_host_02.png"])
        self.assertEqual(merged["auto_pose_s"], 2.5)


class SlotAssignmentTest(unittest.TestCase):
    def test_two_unplaced_characters_take_the_first_free_slots(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai. Câu ba.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["left"])
        self.assertEqual(cast[1]["x"], auto_direct.SLOT_X["center"])
        # Defaults are never invented where the schema has one.
        self.assertNotIn("y", cast[0])

    def test_x_pinned_character_keeps_its_spot(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai.",
                    [
                        {"image_file": "assets/a.png", "x": 0.4},
                        {"image_file": "assets/b.png"},
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], 0.4)
        # 0.30 is closer than MIN_SLOT_GAP to the pinned 0.4, so the free
        # cast skips to the next slot.
        self.assertGreaterEqual(
            abs(cast[1]["x"] - 0.4), auto_direct.MIN_SLOT_GAP
        )

    def test_stage_full_staggers_by_sentence(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai. Câu ba. Câu bốn.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                        {"image_file": "assets/c.png"},
                        {"image_file": "assets/d.png"},
                    ],
                )
            ]
        }
        directed, notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        # The first three fill the slots; the fourth gets its own sentence.
        self.assertNotIn("at_sentence", cast[0])
        self.assertNotIn("at_sentence", cast[1])
        self.assertNotIn("at_sentence", cast[2])
        self.assertEqual(cast[3]["at_sentence"], 4)
        self.assertEqual(cast[3]["for_sentences"], 1)
        self.assertTrue(any("staggered" in note for note in notes))

    def test_stagger_is_clamped_to_the_sentence_count(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Chỉ một câu.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                        {"image_file": "assets/c.png"},
                        {"image_file": "assets/d.png"},
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[3]["at_sentence"], 1)

    def test_solo_character_takes_centre_stage(self):
        script = {
            "scenes": [
                scene(1, "Một câu.", [{"image_file": "assets/a.png"}])
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["center"])

    def test_solo_inside_story_frame_takes_centre_slot(self):
        script = {
            "story_frame": {"use": "ke_su"},
            "scenes": [
                scene(1, "Một câu.", [{"image_file": "assets/a.png"}])
            ],
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(
            cast[0]["x"], auto_direct.STORY_FRAME_SLOTS["center"]
        )

    def test_story_frame_tightens_slots(self):
        script = {
            "story_frame": {"use": "ke_su"},
            "scenes": [
                scene(1, "Câu một. Câu hai.", [
                    {"image_file": "assets/a.png"},
                    {"image_file": "assets/b.png"},
                ])
            ],
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], auto_direct.STORY_FRAME_SLOTS["left"])
        self.assertEqual(cast[1]["x"], auto_direct.STORY_FRAME_SLOTS["center"])

    def test_scene_level_story_frame_false_uses_full_slots(self):
        script = {
            "story_frame": {"use": "ke_su"},
            "scenes": [
                dict(
                    scene(1, "Câu một. Câu hai.", [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                    ]),
                    story_frame=False,
                )
            ],
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["left"])


class FaceOffTest(unittest.TestCase):
    def test_pair_flips_towards_each_other(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                    ],
                )
            ]
        }
        directed, notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertFalse(cast[0]["flip"])
        self.assertTrue(cast[1]["flip"])
        self.assertTrue(any("face-off" in note for note in notes))

    def test_explicit_flip_is_never_overridden(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png", "flip": False},
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertFalse(cast[1]["flip"])

    def test_single_character_is_not_flipped(self):
        script = {
            "scenes": [
                scene(1, "Một câu.", [{"image_file": "assets/a.png"}])
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertNotIn("flip", cast[0])


class ReflowTest(unittest.TestCase):
    def test_reflow_strips_placement_and_restages(self):
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai. Câu ba.",
                    [
                        {
                            "image_file": "assets/a.png",
                            "x": 0.9,
                            "y": 0.5,
                            "at_sentence": 3,
                            "preset": "boing",
                            "enter": {"type": "drop_bounce"},
                        },
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY, reflow=True)
        cast = directed["scenes"][0]["characters"]
        # Placement re-derived (solo -> centre); overrides untouched.
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["center"])
        self.assertNotIn("at_sentence", cast[0])
        self.assertEqual(cast[0]["preset"], "boing")
        self.assertEqual(cast[0]["enter"], {"type": "drop_bounce"})

    def test_fill_mode_keeps_pinned_timing_but_fills_position(self):
        """The author pinned *when* the cue appears but not *where*: the
        timing survives untouched, the position is still the tool's gap
        to fill."""
        script = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai.",
                    [
                        {"image_file": "assets/a.png", "at_sentence": 2}
                    ],
                )
            ]
        }
        directed, _notes = auto_direct.direct_script(script, REGISTRY)
        cast = directed["scenes"][0]["characters"]
        self.assertEqual(cast[0]["at_sentence"], 2)
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["center"])
        self.assertNotIn("y", cast[0])


class WriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="auto_direct_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _script_path(self, payload: dict) -> Path:
        path = self.root / "script.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def _run_cli(self, *argv: str) -> str:
        buffer = io.StringIO()
        old_argv, sys.argv = sys.argv, ["auto_direct.py", *argv]
        with contextlib.redirect_stdout(buffer):
            auto_direct.main()
        sys.argv = old_argv
        return buffer.getvalue()

    def test_dry_run_writes_nothing(self):
        payload = {
            "scenes": [
                scene(1, "Một câu.", [{"image_file": "assets/a.png"}])
            ]
        }
        path = self._script_path(payload)
        original = path.read_text(encoding="utf-8")

        output = self._run_cli(str(path), "--dry-run")
        self.assertIn("DRY RUN", output)
        self.assertIn("slot center", output)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_real_run_writes_staging(self):
        payload = {
            "scenes": [
                scene(
                    1,
                    "Câu một. Câu hai.",
                    [
                        {"image_file": "assets/a.png"},
                        {"image_file": "assets/b.png"},
                    ],
                )
            ]
        }
        path = self._script_path(payload)

        output = self._run_cli(str(path))
        self.assertIn("WROTE", output)

        staged = json.loads(path.read_text(encoding="utf-8"))
        cast = staged["scenes"][0]["characters"]
        self.assertEqual(cast[0]["x"], auto_direct.SLOT_X["left"])
        self.assertTrue(cast[1]["flip"])

    def test_scene_without_cast_is_untouched(self):
        payload = {"scenes": [scene(1, "Một câu.", [])]}
        directed, notes = auto_direct.direct_script(copy.deepcopy(payload), REGISTRY)
        self.assertEqual(directed["scenes"][0]["characters"], [])
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
