import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.split_character_sheet import CELL_ORIGINS, CELL_SIZE, split_sheet


class SplitCharacterSheetTests(unittest.TestCase):
    def test_crops_six_fixed_cells_in_reading_order(self):
        colors = [
            (220, 20, 20),
            (20, 220, 20),
            (20, 20, 220),
            (220, 220, 20),
            (220, 20, 220),
            (20, 220, 220),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "sheet.png"
            output = root / "cutouts"
            sheet = Image.new("RGB", (1536, 1024), "white")
            draw = ImageDraw.Draw(sheet)
            for (left, top), color in zip(CELL_ORIGINS, colors):
                draw.rectangle(
                    (left, top, left + CELL_SIZE - 1, top + CELL_SIZE - 1),
                    fill=color,
                )
            sheet.save(source)

            paths = split_sheet(source, output)

            self.assertEqual(len(paths), 6)
            for path, color in zip(paths, colors):
                with Image.open(path) as crop:
                    self.assertEqual(crop.size, (CELL_SIZE, CELL_SIZE))
                    self.assertEqual(crop.getpixel((CELL_SIZE // 2, CELL_SIZE // 2))[:3], color)

    def test_rejects_wrong_sheet_ratio(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "wrong_ratio.png"
            Image.new("RGB", (100, 100), "white").save(source)

            with self.assertRaisesRegex(ValueError, "3:2 aspect ratio"):
                split_sheet(source, root / "cutouts")


if __name__ == "__main__":
    unittest.main()