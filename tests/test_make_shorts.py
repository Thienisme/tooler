"""
Tests for `tools/make_shorts.py` — turning `narration.shorts` into vertical
autovid workspaces.

The tool is a script rather than a package module, so it is imported by path
here; the build function it exposes is exercised directly, which is where the
actual behaviour lives (argv handling is the thin part).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
SRC_DIR = PROJECT_ROOT / "src"
for _path in (TOOLS_DIR, SRC_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "make_shorts", TOOLS_DIR / "make_shorts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_shorts = load_tool()


def write_image(path: Path, colour=(80, 90, 120)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), colour).save(path)
    return path


class FindShortsTest(unittest.TestCase):
    def test_reads_narration_shorts(self):
        data = {
            "narration": {
                "segments": [{"order": 1, "title": "Full", "text": "x"}],
                "shorts": [
                    {"order": 1, "title": "Hai", "text": "Nội dung hai."},
                    {"order": 2, "title": "Ba", "text": "Nội dung ba."},
                ],
            }
        }
        shorts = make_shorts.find_shorts(data)
        self.assertEqual([s["order"] for s in shorts], [1, 2])
        self.assertEqual(shorts[0]["title"], "Hai")

    def test_reads_top_level_shorts_and_bare_strings(self):
        shorts = make_shorts.find_shorts({"shorts": ["Một đoạn.", "Đoạn hai."]})
        self.assertEqual(len(shorts), 2)
        self.assertEqual(shorts[0]["text"], "Một đoạn.")
        self.assertEqual(shorts[0]["order"], 1)

    def test_drops_empty_entries(self):
        data = {"shorts": [{"text": "  "}, {"text": "Có nội dung."}]}
        shorts = make_shorts.find_shorts(data)
        self.assertEqual(len(shorts), 1)

    def test_missing_array_is_an_error(self):
        with self.assertRaises(make_shorts.ShortsInputError):
            make_shorts.find_shorts({"narration": {"segments": []}})


class BuildShortWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_shorts_")
        self.root = Path(self._tmp.name)
        self.images = [
            write_image(self.root / "art" / "01.png", (60, 70, 90)),
            write_image(self.root / "art" / "02.png", (90, 70, 60)),
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, short: dict, out_name: str = "short-01", **kwargs):
        return make_shorts.build_short_workspace(
            short,
            self.images,
            self.root / out_name,
            title="Tập thử",
            author="tester",
            voice="Minh Quân Pro",
            speed=1.0,
            music=None,
            music_volume=0.0,
            **kwargs,
        )

    def test_workspace_is_vertical_and_self_contained(self):
        short = {
            "order": 1,
            "title": "Cú twist",
            "text": (
                "Đêm đó trời mưa rất to. Một người bước vào căn hộ cũ. "
                "Ba ngày sau, không ai còn thấy anh ta nữa."
            ),
        }
        script_path, script = self.build(short)

        self.assertTrue(script_path.exists())
        self.assertEqual(script.video_metadata.resolution, "1080x1920")
        # The Short declares its own runtime envelope so the validator does
        # not flag it against the long format's 8-20 minutes.
        self.assertEqual(script.video_metadata.target_minutes, (0.1, 3.0))
        self.assertGreaterEqual(script.scene_count, 1)

        # Every scene's artwork is copied in, so the workspace stands alone.
        for scene in script.scenes:
            copied = self.root / "short-01" / "images" / (
                f"scene_{scene.id:03d}.png"
            )
            self.assertTrue(copied.exists(), copied)

    def test_ken_burns_is_stronger_for_a_narrow_frame(self):
        """The long format's gentle zoom crawls on a 1080px-wide frame, so a
        Short uses wider deltas to keep the image visibly moving."""
        short = {"order": 1, "title": "", "text": "Một câu duy nhất ở đây."}
        _path, script = self.build(short)
        motion = script.scenes[0].ken_burns
        self.assertEqual(motion.type, "zoom_in")
        self.assertGreaterEqual(motion.end_scale, 1.1)

    def test_empty_text_is_rejected(self):
        with self.assertRaises(make_shorts.ShortsInputError):
            self.build({"order": 9, "title": "", "text": "   "})


if __name__ == "__main__":
    unittest.main()
