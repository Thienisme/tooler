"""
Build an autovid workspace from real content: a narration text plus artwork.

    python tools/make_project_from_text.py \
        --text narration.txt \
        --images projects/mystery-001/images \
        --out projects/autovid-mystery-001 \
        --title "Khu chung cư Hoa Mai" --genre trinhtham \
        --music data/music/mystery_biggest_discovery.mp3

This is the bridge between "a writer's text" and `script.json`, which is the
only thing stage 1 accepts.  Everything the writer should not have to think
about — scene boundaries, Ken Burns motion, transitions, pacing settings,
where the images live — is derived here, and the script it writes is then
checked with the same validator the pipeline uses, so a content problem is
reported now instead of 40 minutes into a render.

Input conventions
-----------------
* **Text** — plain UTF-8.  A blank line ends a paragraph, and a paragraph is
  the natural unit of one scene (one image), so the writer's own paragraphing
  survives into the video.
* **Headings** — a line wrapped in `[[ double brackets ]]`, or a line like
  `Chương 3` / `Hồi 12`, becomes a section break with its text overlaid on
  the scene it starts.  Anything else is narration.
* **Images** — any folder or glob, read in sorted order.  Fewer images than
  scenes is allowed: the images cycle, and that is reported.
* **Music** — one file, looped by the mix stage if it is shorter than the
  video.  Optional.

Outputs
-------
    <out>/script.json
    <out>/images/scene_001.png ...   (copies, named by scene id)
    <out>/assets/audio/bg.mp3        (when --music is given)
    <out>/assets/fonts/handwriting.ttf (when a system font was found)
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovid.application.validate import ScriptValidator, estimate_total_seconds  # noqa: E402
from autovid.domain.script import ScriptSchemaError, parse_script  # noqa: E402
from autovid.domain.sentences import split_sentences  # noqa: E402
from autovid.paths import Paths, resolve_asset  # noqa: E402

WIDTH = 1920
HEIGHT = 1080
FPS = 30

# Rough Vietnamese narration rate, used only to lay scenes out; the real
# durations are measured by the tts stage.
CHARS_PER_SECOND = 14.0

# Genre -> default voice.  "trinhtham" is Minh Quân Pro because this format
# explains rather than narrates, and it was chosen after auditioning all 25
# presets on the explainer sample (tools/audition_voices.py).
GENRE_VOICES = {
    "trinhtham": "Minh Quân Pro",
    "ma": "Đức Trí",
    "tutien": "Ngọc Huyền",
    "ngonlu": "Mỹ Duyên",
}

# Ken Burns cycles through these so consecutive scenes never move the same
# way.  Pans need headroom above 1.0 (the validator checks this).
MOTIONS = (
    ("zoom_in", 1.00, 1.08),
    ("pan_right", 1.10, 1.10),
    ("zoom_out", 1.12, 1.00),
    ("pan_left", 1.10, 1.10),
    ("pan_down", 1.10, 1.10),
    ("zoom_in", 1.04, 1.14),
)

FADE_EVERY = 12
FADE_SECONDS = 0.4

# Punchy joins, used when the caller asks for a livelier cut than a fade.
# All of these are real xfade transitions (the validator maps them).
LIVELY_TRANSITIONS = (
    "slide_left",
    "pixelize",
    "circle_open",
    "whip_pan",
    "flash_white",
    "diag_br",
)

# The comedy bundles a host can be given, cycled so consecutive scenes do
# not all enter the same way.
CHARACTER_PRESETS = ("boing", "whoosh", "pop", "sneak", "ta_da", "ninja")

# Which sentence inside a scene the host appears on, so a character walks on
# partway through rather than at the first frame of every scene.
CHARACTER_SENTENCE = 2

HEADING_BRACKETS = re.compile(r"^\[\[\s*(.+?)\s*\]\]$")
HEADING_NUMBERED = re.compile(
    r"^(?:chương|hồi|phần|tập)\s*[0-9ivxlc]+\b.*$", re.IGNORECASE
)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    str(Path.home() / ".fonts"),
    "/System/Library/Fonts",
    "/Library/Fonts",
    "C:/Windows/Fonts",
]

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# --------------------------------------------------------------------------
# reading the text
# --------------------------------------------------------------------------


def parse_text(raw: str) -> list[tuple[str, str | None]]:
    """
    Turn the narration into a flat list of blocks.

    Each block is `(kind, text)` where kind is "heading" or "body".  Headings
    are kept as anchors rather than dropped, because they become section
    breaks and overlays.

    Paragraphing is preserved: a blank line starts a new block, and a block's
    internal line breaks are collapsed to spaces so a hard-wrapped text file
    does not turn into hard-wrapped narration.
    """
    blocks: list[tuple[str, str | None]] = []
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("body", " ".join(" ".join(paragraph).split())))
            paragraph = []

    for line in raw.splitlines():
        stripped = line.strip()

        if not stripped:
            flush()
            continue

        bracketed = HEADING_BRACKETS.match(stripped)
        if bracketed:
            flush()
            blocks.append(("heading", bracketed.group(1)))
            continue

        if HEADING_NUMBERED.match(stripped) and len(stripped) < 120:
            flush()
            blocks.append(("heading", stripped))
            continue

        paragraph.append(stripped)

    flush()
    return blocks


def split_body(text: str, *, scene_seconds: float) -> list[str]:
    """
    One paragraph as one scene, unless it is too long or too short.

    A long paragraph is split at sentence boundaries so a scene never runs
    past `scene_seconds` by much — the point of the split is that the image
    keeps changing, which is the whole reason this format exists.  Resolution
    of the split is by *text length* here; the tts stage then measures what
    the voice actually did.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    max_chars = max(int(scene_seconds * CHARS_PER_SECOND), 120)

    scenes: list[str] = []
    current: list[str] = []
    length = 0

    for sentence in sentences:
        addition = len(sentence) + 1
        # A single very long sentence is kept whole: splitting it would break
        # a line of narration mid-thought, which reads worse than a slow cut.
        if current and length + addition > max_chars:
            scenes.append(" ".join(current))
            current = [sentence]
            length = addition
            continue
        current.append(sentence)
        length += addition

    if current:
        scenes.append(" ".join(current))
    return scenes


def merge_short(scenes: list[str], *, min_seconds: float) -> list[str]:
    """
    Fold scenes under `min_seconds` into the next one.

    A three-second scene is a flash of an image, not a beat, and it also
    makes every transition look like a mistake.
    """
    min_chars = max(int(min_seconds * CHARS_PER_SECOND), 40)

    merged: list[str] = []
    for scene in scenes:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = f"{merged[-1]} {scene}"
            continue
        merged.append(scene)

    if len(merged) > 1 and len(merged[-1]) < min_chars:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]} {tail}"
    return merged


def build_scenes(
    blocks: list[tuple[str, str | None]],
    *,
    scene_seconds: float,
    min_seconds: float,
) -> tuple[list[str], dict[int, str]]:
    """
    Blocks -> scene texts, plus the chapter title each scene should show.

    Headings are attached to the scene that follows them, so the title and
    its section break land exactly where the writer put them.
    """
    scenes: list[str] = []
    overlays: dict[int, str] = {}
    pending_heading: str | None = None

    for kind, text in blocks:
        if kind == "heading":
            pending_heading = text
            continue

        assert text is not None
        for piece in merge_short(
            split_body(text, scene_seconds=scene_seconds),
            min_seconds=min_seconds,
        ):
            if pending_heading is not None:
                overlays[len(scenes)] = pending_heading
                pending_heading = None
            scenes.append(piece)

    return scenes, overlays


# --------------------------------------------------------------------------
# images and assets
# --------------------------------------------------------------------------


def collect_images(pattern: str) -> list[Path]:
    path = Path(pattern)
    if path.is_dir():
        found = [
            child
            for child in sorted(path.iterdir())
            if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES
        ]
    else:
        found = [
            Path(match)
            for match in sorted(glob.glob(pattern))
            if Path(match).suffix.lower() in IMAGE_SUFFIXES
        ]
    return found


def find_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    for directory in FONT_SEARCH_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in ("**/*.ttf", "**/*.otf"):
            for found in sorted(base.glob(pattern)):
                return found
    return None


# --------------------------------------------------------------------------
# script.json
# --------------------------------------------------------------------------


def build_script(
    scenes: list[str],
    overlays: dict[int, str],
    images: list[Path],
    *,
    title: str,
    author: str,
    voice: str,
    speed: float,
    music: Path | None,
    music_volume: float,
    character: str | None = None,
    character_every: int = 2,
    lively: bool = False,
    punches: bool = False,
) -> dict:
    section_breaks = sorted(index + 1 for index in overlays if index > 0)

    scene_payload = []
    for index, text in enumerate(scenes):
        motion_type, start_scale, end_scale = MOTIONS[index % len(MOTIONS)]
        image = images[index % len(images)]

        payload: dict = {
            "id": index + 1,
            "text": text,
            "image_file": f"images/scene_{index + 1:03d}{image.suffix.lower()}",
            "ken_burns": {
                "enabled": True,
                "type": motion_type,
                "start_scale": start_scale,
                "end_scale": end_scale,
            },
        }

        if lively and index > 0:
            transition = {
                "type": LIVELY_TRANSITIONS[index % len(LIVELY_TRANSITIONS)],
                "duration": FADE_SECONDS,
            }
        elif index > 0 and index % FADE_EVERY == 0:
            transition = {"type": "fade", "duration": FADE_SECONDS}
        else:
            transition = {"type": "cut", "duration": 0.0}
        payload["transition_in"] = transition

        if character and index % max(character_every, 1) == 0:
            payload["characters"] = [
                {
                    "image_file": character,
                    "preset": CHARACTER_PRESETS[
                        (index // max(character_every, 1)) % len(CHARACTER_PRESETS)
                    ],
                    "x": 0.2,
                    "y": 0.97,
                    "height": 0.52,
                    # A host that walks on at the very first frame of every
                    # scene is wallpaper; from the second sentence it reads
                    # as a reaction.
                    "at_sentence": CHARACTER_SENTENCE,
                    "for_sentences": 3,
                }
            ]

        if punches and index % 4 == 1 and index > 0:
            payload["impact"] = {
                "at_sentence": CHARACTER_SENTENCE,
                "intensity": 0.09,
                "shake_px": 12,
                "duration_ms": 500,
                "flash": "white",
            }

        heading = overlays.get(index)
        if heading:
            payload["text_overlays"] = [
                {
                    "text": heading,
                    "font": "assets/fonts/handwriting.ttf",
                    "font_size": 96,
                    "color": "#FFD166",
                    "stroke_color": "#000000",
                    "stroke_width": 4,
                    "position": "center",
                    "start_offset_ms": 400,
                    "end_offset_ms": 4200,
                    "animation": "fade_in",
                    "animation_duration_ms": 500,
                }
            ]

        scene_payload.append(payload)

    return {
        "video_metadata": {
            "title": title,
            "author": author,
            "resolution": f"{WIDTH}x{HEIGHT}",
            "fps": FPS,
            "language": "vi",
        },
        "tts_config": {
            "engine": "vieneu",
            "voice": voice,
            "speed": speed,
            "pitch": 0.0,
            "emotion": "neutral",
            # One sentence per inference keeps the pause engine in control of
            # the rhythm, which is the whole point of `sentence` granularity.
            "granularity": "sentence",
            "max_chars_per_chunk": 1000,
        },
        "audio_config": {
            "background_music": "assets/audio/bg" + (music.suffix if music else ".mp3")
            if music
            else None,
            "background_volume": music_volume if music else 0.0,
            "master_volume": -14.0,
            "fade_in_seconds": 2.0,
            "fade_out_seconds": 3.0,
        },
        "pacing": {
            "auto_pause": {
                "enabled": True,
                "per_sentence": True,
                "min_pause_ms": 300,
                "max_pause_ms": 5000,
                "respect_tts_natural_pause": True,
            },
            "section_breaks": section_breaks,
            "custom_pauses": {},
        },
        "scenes": scene_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--text", required=True, help="narration .txt (UTF-8)")
    parser.add_argument(
        "--images",
        required=True,
        help="folder of artwork (or a glob), read in sorted order",
    )
    parser.add_argument("--out", required=True, type=Path, help="workspace to create")
    parser.add_argument("--title", default=None, help="episode title")
    parser.add_argument("--author", default="autovid", help="shown in video_metadata")
    parser.add_argument(
        "--genre",
        choices=sorted(GENRE_VOICES),
        default=None,
        help="picks the default voice (trinhtham, ma, tutien, ngonlu)",
    )
    parser.add_argument("--voice", default=None, help="override the voice name")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="narration speed, 0.5-2.0 (applied with ffmpeg atempo)",
    )
    parser.add_argument(
        "--music",
        default=None,
        help="background track file; omitted means a video with no music",
    )
    parser.add_argument("--music-volume", type=float, default=0.12)
    parser.add_argument(
        "--scene-seconds",
        type=float,
        default=20.0,
        help="target narration length per scene (default 20)",
    )
    parser.add_argument(
        "--min-scene-seconds",
        type=float,
        default=6.0,
        help="scenes shorter than this are merged into the next one",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=None,
        help="keep only the first N scenes (useful for a short acceptance run)",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="transparent PNG of a character to walk into some scenes, e.g. "
        "assets/characters/host_idle.png (see tools/make_demo_character.py)",
    )
    parser.add_argument(
        "--character-every",
        type=int,
        default=2,
        help="put the character in every Nth scene (default 2)",
    )
    parser.add_argument(
        "--lively",
        action="store_true",
        help="use punchy transitions (pixelize, circle, whip pan, flash) "
        "instead of the occasional fade",
    )
    parser.add_argument(
        "--punches",
        action="store_true",
        help="add a punch-in (zoom spike, camera shake, white flash) to "
        "every fourth scene",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace an existing workspace"
    )
    args = parser.parse_args()

    text_path = Path(args.text)
    if not text_path.exists():
        print(f"ERROR: text not found: {text_path}", file=sys.stderr)
        return 1

    images = collect_images(args.images)
    if not images:
        print(f"ERROR: no images found for {args.images!r}", file=sys.stderr)
        return 1

    out = args.out.resolve()
    if out.exists():
        if not args.force:
            print(
                f"ERROR: {out} already exists. Pass --force to replace it.",
                file=sys.stderr,
            )
            return 1
        shutil.rmtree(out)

    blocks = parse_text(text_path.read_text(encoding="utf-8"))
    scenes, overlays = build_scenes(
        blocks,
        scene_seconds=args.scene_seconds,
        min_seconds=args.min_scene_seconds,
    )
    if args.max_scenes:
        scenes = scenes[: args.max_scenes]
        overlays = {index: title for index, title in overlays.items() if index < len(scenes)}

    if not scenes:
        print("ERROR: the text produced no scenes", file=sys.stderr)
        return 1

    music = Path(args.music) if args.music else None
    if music is not None and not music.exists():
        print(f"ERROR: music not found: {music}", file=sys.stderr)
        return 1

    voice = args.voice or GENRE_VOICES.get(args.genre or "", "Anh Khôi")
    title = args.title or text_path.stem

    character = args.character
    if character is not None:
        # Checked here rather than at render time: a project that is built
        # around a character file that does not exist fails much later.
        resolved = resolve_asset(character, out if out.exists() else PROJECT_ROOT)
        if resolved is None:
            print(
                f"ERROR: character not found: {character}\n"
                "      Run tools/make_demo_character.py for a placeholder, or "
                "point --character at your own transparent PNG.",
                file=sys.stderr,
            )
            return 1
        character = str(resolved)

    payload = build_script(
        scenes,
        overlays,
        images,
        title=title,
        author=args.author,
        voice=voice,
        speed=args.speed,
        music=music,
        music_volume=args.music_volume,
        character=character,
        character_every=args.character_every,
        lively=args.lively,
        punches=args.punches,
    )

    # Parse before writing: a schema mistake here would otherwise surface
    # only when the user runs stage 1.
    try:
        script = parse_script(payload)
    except ScriptSchemaError as error:
        print(f"ERROR: built an invalid script: {error}", file=sys.stderr)
        return 1

    out.mkdir(parents=True)
    (out / "images").mkdir()
    (out / "assets" / "audio").mkdir(parents=True)

    # One copy per scene, named by scene id: the workspace then stands alone,
    # and a scene can have its artwork swapped without touching the others.
    for scene in script.scenes:
        image = images[(scene.id - 1) % len(images)]
        target = out / "images" / f"scene_{scene.id:03d}{image.suffix.lower()}"
        if not target.exists():
            shutil.copyfile(image, target)

    font = find_font()
    if font is not None:
        font_target = out / "assets" / "fonts" / "handwriting.ttf"
        font_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(font, font_target)

    if music is not None:
        shutil.copyfile(music, out / "assets" / "audio" / f"bg{music.suffix}")

    (out / "script.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    paths = Paths.from_workspace(out)
    report = ScriptValidator(script, paths).run(skip_engine_check=True)

    estimated = estimate_total_seconds(script)
    print(f"workspace   : {out}")
    print(f"text        : {text_path} ({sum(len(text) for text in scenes):,} chars)")
    print(
        f"scenes      : {len(script.scenes)} "
        f"(~{estimated / 60:.1f} min of narration at {CHARS_PER_SECOND:g} chars/s)"
    )
    print(
        f"images      : {len(images)} source file(s)"
        + (
            f", cycled across {len(script.scenes)} scenes"
            if len(images) < len(script.scenes)
            else ""
        )
    )
    print(
        f"voice       : {voice} @ {args.speed:.2f}x"
        + (f" ({args.genre})" if args.genre else "")
    )
    print(f"music       : {music.name if music else 'none'}")
    print(
        f"headings    : {len(overlays)} overlay(s), "
        f"{len(script.pacing.section_breaks)} section break(s)"
    )
    character_cues = sum(len(scene.characters) for scene in script.scenes)
    if character_cues:
        presets = sorted(
            {
                character_["preset"]
                for scene in payload["scenes"]
                for character_ in scene.get("characters", [])
            }
        )
        print(
            f"characters  : {character_cues} cue(s) every "
            f"{max(args.character_every, 1)} scene(s), presets "
            f"{', '.join(presets)}"
        )
    if args.lively:
        print(f"transitions : punchy ({', '.join(LIVELY_TRANSITIONS)})")
    if args.punches:
        print("punch-ins   : every 4th scene (zoom spike, shake, flash)")
    print(f"font        : {font if font else 'NONE (text overlays will fail)'}")

    if report.errors or report.warnings:
        print()
        for issue in report.errors:
            print(f"[x] {issue}")
        for issue in report.warnings:
            print(f"[!] {issue}")

    if report.errors:
        print("\nERROR: the script does not pass validation", file=sys.stderr)
        return 1

    print(f"\nnext: python autovid.py validate {out / 'script.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
