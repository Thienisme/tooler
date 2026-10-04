"""The catalogue of fonts a caption may use.

A caption's face is a *choice*, not a path: `script.json` names one of these
keys and every machine then draws the same letters, which is the whole point
of shipping the files rather than pointing at whatever font happens to be
installed.  `resolve_font` (infrastructure/image/fonts.py) maps a key onto
the file, and a project may still name a raw path -- old scripts do exactly
that -- so both spellings keep working.

The catalogue is grouped by **mood** rather than by family name, because the
decision an author actually makes is "this line is a joke", not "this line is
Quicksand": a caption set in a face that fights the scene reads as a sticker
bolted onto the picture.

Every face here renders the full Vietnamese alphabet, and that is not a
property these fonts advertise.  Google Fonts splits each family into
subsets, and of the first fourteen candidates eight -- Caveat, Kalam,
Comic Neue, Titan One, Passion One, Fredoka and two more -- could not draw a
single one of `ớ ề ữ`, rendering them as blank boxes that look like a typo
rather than like a missing font.  `tools/check_font_coverage.py` is how the
list was made and `tests/test_studio_fonts.py` is what keeps it true.

All of them are SIL Open Font License, shipped next to the fonts under
`assets/fonts/licenses/`.
"""

from __future__ import annotations

from dataclasses import dataclass

# What a face is *for*.  The studio groups the picker by these, in this
# order, because the mood is the decision.
FONT_MOODS: tuple[tuple[str, str], ...] = (
    ("de", "De doc"),
    ("vui", "Hai huoc"),
    ("thu-phap", "Thu phap"),
)


@dataclass(frozen=True)
class CaptionFont:
    """One bundled face: what it is called, where it lives, what it is for."""

    key: str
    label: str
    path: str
    mood: str
    # The licence file under `assets/fonts/licenses/` that covers this face,
    # or None for the project's own face -- which predates this catalogue,
    # was not fetched from anywhere, and so has no licence this catalogue
    # can speak for.  Saying so is better than implying a file that is not
    # there.
    licence: str | None = None


CAPTION_FONTS: tuple[CaptionFont, ...] = (
    # -- plain and readable ---------------------------------------------
    CaptionFont("be-vietnam-pro", "Be Vietnam Pro",
                "assets/fonts/BeVietnamPro-Regular.ttf", "de",
                "bevietnampro.txt"),
    CaptionFont("bitter", "Bitter",
                "assets/fonts/Bitter[wght].ttf", "de", "bitter.txt"),
    CaptionFont("marmelad", "Marmelad",
                "assets/fonts/Marmelad-Regular.ttf", "de", "marmelad.txt"),
    # -- round and comic ------------------------------------------------
    CaptionFont("quicksand", "Quicksand",
                "assets/fonts/Quicksand[wght].ttf", "vui", "quicksand.txt"),
    CaptionFont("nunito", "Nunito",
                "assets/fonts/Nunito[wght].ttf", "vui", "nunito.txt"),
    CaptionFont("itim", "Itim",
                "assets/fonts/Itim-Regular.ttf", "vui", "itim.txt"),
    CaptionFont("coiny", "Coiny",
                "assets/fonts/Coiny-Regular.ttf", "vui", "coiny.txt"),
    CaptionFont("bungee", "Bungee",
                "assets/fonts/Bungee-Regular.ttf", "vui", "bungee.txt"),
    CaptionFont("boogaloo", "Boogaloo",
                "assets/fonts/Boogaloo-Regular.ttf", "vui", "boogaloo.txt"),
    CaptionFont("rowdies", "Rowdies",
                "assets/fonts/Rowdies-Regular.ttf", "vui", "rowdies.txt"),
    CaptionFont("bangers", "Bangers",
                "assets/fonts/Bangers-Regular.ttf", "vui", "bangers.txt"),
    # -- handwritten and brush -----------------------------------------
    CaptionFont("handwriting", "Handwriting (mac dinh cua project)",
                "assets/fonts/handwriting.ttf", "thu-phap", None),
    CaptionFont("pacifico", "Pacifico",
                "assets/fonts/Pacifico-Regular.ttf", "thu-phap",
                "pacifico.txt"),
    CaptionFont("lobster", "Lobster",
                "assets/fonts/Lobster-Regular.ttf", "thu-phap", "lobster.txt"),
    CaptionFont("patrick-hand", "Patrick Hand",
                "assets/fonts/PatrickHand-Regular.ttf", "thu-phap",
                "patrickhand.txt"),
    CaptionFont("patrick-hand-sc", "Patrick Hand SC",
                "assets/fonts/PatrickHandSC-Regular.ttf", "thu-phap",
                "patrickhandsc.txt"),
    CaptionFont("sriracha", "Sriracha",
                "assets/fonts/Sriracha-Regular.ttf", "thu-phap",
                "sriracha.txt"),
    CaptionFont("indie-flower", "Indie Flower",
                "assets/fonts/IndieFlower-Regular.ttf", "thu-phap",
                "indieflower.txt"),
)

FONT_BY_KEY: dict[str, CaptionFont] = {font.key: font for font in CAPTION_FONTS}
FONT_BY_PATH: dict[str, CaptionFont] = {font.path: font for font in CAPTION_FONTS}

# What a caption gets when a script says nothing: a face designed for this
# language, at a weight that survives being shrunk to fit.
DEFAULT_FONT_KEY = "be-vietnam-pro"


def font_for(reference: str) -> CaptionFont | None:
    """The catalogue entry a reference names, by key or by bundled path."""
    if reference in FONT_BY_KEY:
        return FONT_BY_KEY[reference]
    return FONT_BY_PATH.get(reference)


def font_reference(reference: str) -> str:
    """
    Turn a catalogue key into the path the renderer should open.

    Anything that is not a known key is returned unchanged, so a script that
    names its own font file -- which every project written before the
    catalogue existed does -- keeps working untouched.
    """
    font = FONT_BY_KEY.get(reference)
    return font.path if font else reference


def fonts_by_mood() -> list[tuple[str, str, list[CaptionFont]]]:
    """The catalogue as (mood key, mood label, faces), in `FONT_MOODS` order."""
    return [
        (
            key,
            label,
            [font for font in CAPTION_FONTS if font.mood == key],
        )
        for key, label in FONT_MOODS
    ]