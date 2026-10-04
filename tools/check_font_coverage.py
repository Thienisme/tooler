"""Report which bundled fonts can actually render the Vietnamese alphabet.

Run this after `tools/fetch_fonts.py` and before adding a family to
`domain/fonts.py`:

    python tools/check_font_coverage.py

A face that cannot draw `ớ` or `ữ` is not a Vietnamese font, however good it
looks in the Latin sample on the Google Fonts site -- it renders every one of
those letters as a blank .notdef box, which in a caption reads as a spelling
mistake rather than as a missing font.  Eight of the first fourteen families
tried here failed exactly that way, which is why the check exists at all.

A glyph counts as missing when it draws pixel-for-pixel the same as U+E000, a
private-use code point no font defines.  That compares against the font's own
"this character is not in me" drawing rather than against a hard-coded shape,
so it works whatever the font uses for .notdef -- an empty box, a hollow
rectangle or nothing at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autovid.domain.fonts import CAPTION_FONTS

# Every letter Vietnamese adds to the Latin alphabet, plus the crossed
# `đ`, which is a different letter rather than a diacritic.
VIETNAMESE = (
    "áàảãạăằắẳẵặâầấẩẫậéèẻẽẹêềếểễệ"
    "íìỉĩịóòỏõọôồốổỗộơớờởỡợúùủũụưừứửữự"
    "ýỳỷỹỵđĐ"
)
NOT_DEFINED = "\ue000"
PROBE_SIZE = 40


def _drawing(font: ImageFont.FreeTypeFont, character: str) -> bytes:
    """The pixels one character produces, as comparable bytes."""
    canvas = Image.new("L", (PROBE_SIZE * 2, PROBE_SIZE * 2), 0)
    ImageDraw.Draw(canvas).text((4, 4), character, font=font, fill=255)
    return canvas.tobytes()


def missing_glyphs(font_path: Path, text: str = VIETNAMESE) -> set[str]:
    """The characters in `text` this font cannot draw."""
    font = ImageFont.truetype(str(font_path), PROBE_SIZE)
    blank = _drawing(font, NOT_DEFINED)
    return {character for character in text if _drawing(font, character) == blank}


def main() -> int:
    workspace = Path(__file__).resolve().parent.parent
    failures = 0
    for font in CAPTION_FONTS:
        path = workspace / font.path
        if not path.is_file():
            print(f"{font.key:16} MISSING FILE {font.path}")
            failures += 1
            continue
        gaps = missing_glyphs(path)
        verdict = "ok" if not gaps else f"MISSING {len(gaps)}"
        print(f"{font.key:16} {verdict:12} {font.label}")
        if gaps:
            failures += 1
            print(f"{'':16} cannot draw {''.join(sorted(gaps))}")

    own = workspace / "assets" / "fonts" / "handwriting.ttf"
    if own.is_file():
        gaps = missing_glyphs(own)
        print(
            f"{'handwriting':16} {'ok' if not gaps else f'MISSING {len(gaps)}'}"
            f"{'':12} (the project's own face)"
        )
        failures += 1 if gaps else 0

    if failures:
        print(f"\n{failures} font(s) cannot render Vietnamese.")
    else:
        print(f"\nAll {len(CAPTION_FONTS)} bundled faces render Vietnamese.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())