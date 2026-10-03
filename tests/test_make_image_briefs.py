from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from make_image_briefs import write_contact_sheet  # noqa: E402


class ContactSheetTests(unittest.TestCase):
    def test_contact_sheet_shows_thumbnails_status_and_scene_copy(self):
        with tempfile.TemporaryDirectory(prefix="image-review-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            image_path = workspace / "images" / "scene 01.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (640, 360), "teal").save(image_path)
            output = workspace / "image_contact_sheet.html"
            rows = [
                {
                    "id": 1,
                    "reference": "images/scene 01.png",
                    "prompt": None,
                    "seconds": 12.5,
                    "excerpt": "Lời kể <script> được escape.",
                },
                {
                    "id": 2,
                    "reference": None,
                    "prompt": "Cảnh chưa có ảnh",
                    "seconds": 8.0,
                    "excerpt": "Chưa có file ảnh.",
                },
            ]
            inventory = [
                {
                    "reference": "images/scene 01.png",
                    "exists": True,
                    "resolved": image_path,
                    "size": (640, 360),
                    "upscaled": True,
                    "scenes": 4,
                }
            ]

            write_contact_sheet(
                rows,
                inventory,
                title="Tập thử <b>",
                resolution="1920x1080",
                output=output,
            )

            page = output.read_text(encoding="utf-8")
            self.assertIn("Duyệt ảnh: Tập thử &lt;b&gt;", page)
            self.assertIn("scene%2001.png", page)
            self.assertIn("Ảnh nhỏ hơn khung", page)
            self.assertIn("Dùng lại 4 scene", page)
            self.assertIn("Chưa có ảnh cho scene này", page)
            self.assertIn("Lời kể &lt;script&gt; được escape.", page)
            self.assertNotIn("Lời kể <script>", page)


if __name__ == "__main__":
    unittest.main()