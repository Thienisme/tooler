"""Download the bundled caption fonts from the Google Fonts repository.

The set is chosen by *mood* -- a plain readable sans, a round comic face and
a handwritten brush -- because a caption that is funnier than the joke is a
caption nobody reads.  Every family is downloaded with its licence (SIL Open
Font License), which sits next to the fonts so the repo carries the terms it
is required to carry.

Files come from `raw.githubusercontent.com` rather than the GitHub API on
purpose: the API allows sixty unauthenticated requests an hour, which a
fetch of twenty families plus their licences exhausts, and the failure mode
is silent -- a font appears to download and its licence quietly does not.
The raw endpoint is not rate limited at any rate we care about.

Vietnamese coverage is **not** assumed.  Google Fonts splits each family into
subsets and plenty of them have no Vietnamese glyphs at all -- of the first
fourteen families fetched, eight could not render a single one of `ớ ề ữ`.
Every family below was checked with `tools/check_font_coverage.py` and the
list is the survivors; `tests/test_studio_fonts.py` is what keeps it that
way.  A family that fails there is fixed by choosing a different face, not
by quietly widening the test.

Usage:  python tools/fetch_fonts.py [--force]
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/google/fonts/main"
LICENCE_NAMES = ("OFL.txt", "LICENSE.txt")
USER_AGENT = "autovid-font-fetch/1.0"

# family directory -> file within it.  Google Fonts has dropped the `static/`
# copies, so the variable files are what there is; PIL renders a variable
# font's default instance, which is the weight a caption wants anyway.
FAMILIES: dict[str, str] = {
    # -- plain and readable ----------------------------------------------
    "ofl/bevietnampro": "BeVietnamPro-Regular.ttf",
    "ofl/bitter": "Bitter[wght].ttf",
    "ofl/marmelad": "Marmelad-Regular.ttf",
    # -- round and comic -------------------------------------------------
    "ofl/quicksand": "Quicksand[wght].ttf",
    "ofl/nunito": "Nunito[wght].ttf",
    "ofl/itim": "Itim-Regular.ttf",
    "ofl/bungee": "Bungee-Regular.ttf",
    "ofl/boogaloo": "Boogaloo-Regular.ttf",
    "ofl/rowdies": "Rowdies-Regular.ttf",
    "ofl/coiny": "Coiny-Regular.ttf",
    "ofl/bangers": "Bangers-Regular.ttf",
    # -- handwritten and brush ------------------------------------------
    "ofl/pacifico": "Pacifico-Regular.ttf",
    "ofl/lobster": "Lobster-Regular.ttf",
    "ofl/patrickhand": "PatrickHand-Regular.ttf",
    "ofl/patrickhandsc": "PatrickHandSC-Regular.ttf",
    "ofl/sriracha": "Sriracha-Regular.ttf",
    "ofl/indieflower": "IndieFlower-Regular.ttf",
}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _raw(path: str) -> bytes:
    """Fetch one file in the repository, with the name URL-escaped.

    The variable font names carry literal brackets, which a URL is allowed to
    contain but no server is obliged to read unescaped.
    """
    directory, _, name = path.rpartition("/")
    quoted = urllib.parse.quote(name, safe="")
    return _get(f"{BASE}/{directory}/{quoted}")


def fetch(destination: Path, *, force: bool) -> tuple[int, list[str]]:
    fonts = destination / "fonts"
    licences = destination / "fonts" / "licenses"
    fonts.mkdir(parents=True, exist_ok=True)
    licences.mkdir(parents=True, exist_ok=True)

    fetched = 0
    skipped: list[str] = []
    for path, filename in FAMILIES.items():
        family = path.split("/")[-1]
        target = fonts / filename
        licence_target = licences / f"{family}.txt"
        if target.exists() and licence_target.exists() and not force:
            fetched += 1
            continue

        try:
            data = _raw(f"{path}/{filename}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            print(f"  ! {family}: {error}")
            skipped.append(family)
            continue
        target.write_bytes(data)
        fetched += 1

        for licence_name in LICENCE_NAMES:
            try:
                licence_target.write_bytes(_raw(f"{path}/{licence_name}"))
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                continue
        else:
            print(f"  ! {family}: no licence file found")
        print(f"  {target.name}")

    # Anything the list no longer mentions would otherwise linger in the repo
    # forever: the font file is selectable in nothing, and the licence file
    # is a promise about a font that is no longer shipped.
    keep = set(FAMILIES.values()) | {"handwriting.ttf"}
    for stale in fonts.glob("*.ttf"):
        if stale.name not in keep:
            stale.unlink()
            print(f"  - {stale.name} (no longer in the set)")
    keep_families = {path.split("/")[-1] for path in FAMILIES}
    for stale in licences.glob("*.txt"):
        if stale.stem not in keep_families:
            stale.unlink()
            print(f"  - licenses/{stale.name} (no longer in the set)")

    return fetched, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="re-download even if present"
    )
    arguments = parser.parse_args()

    workspace = Path(__file__).resolve().parent.parent
    print(f"Fetching caption fonts into {workspace / 'assets' / 'fonts'}")
    fetched, skipped = fetch(workspace / "assets", force=arguments.force)

    size = sum(
        path.stat().st_size
        for path in (workspace / "assets" / "fonts").glob("*.ttf")
    )
    print(f"{fetched} font file(s), {size / 1024 / 1024:.1f} MB")
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())