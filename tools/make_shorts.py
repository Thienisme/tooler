"""
Build vertical 9:16 Shorts workspaces from a story's `narration.shorts`.

    python tools/make_shorts.py projects/mystery-001/story.json \
        --out projects/mystery-001-shorts --run

The story pipeline (`notes/note.txt`) asks the editor to cut two to five
self-contained 50-60s passages out of the finished narration and keep them
in `narration.shorts`.  Those passages are the Shorts; this tool is what
turns them into videos.

Each short becomes its own autovid workspace under `<out>/short-01/`,
`<out>/short-02/`… at 1080x1920, because the pipeline renders one script per
workspace and one Short is one video.  Everything about scene splitting,
Ken Burns, transitions and pacing is reused verbatim from
`make_project_from_text.py`, so a Short cuts the same way the long video
does -- only the frame is rotated.

Input
-----
* a `story.json` whose `narration.shorts` the review workflow filled in, or
  any JSON file with a top-level `shorts` array.  Each entry is either
  `{order, title, text}` or a bare string.
* artwork: `--images`, or the `images/` folder next to the JSON.

With `--run` the seven pipeline stages are executed for every workspace, so
the command ends with one `output/final_video_subtitled.mp4` per Short.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
for _path in (TOOLS_DIR, PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from autovid.domain.script import (  # noqa: E402
    Script,
    ScriptSchemaError,
    parse_script,
)
from make_project_from_text import (  # noqa: E402
    GENRE_VOICES,
    build_script,
    build_scenes,
    collect_images,
    find_font,
    parse_text,
)

# Vertical Full HD.  The pipeline is resolution-agnostic; the validator
# accepts this because it now measures the shorter side rather than the
# width.
VERTICAL = (1080, 1920)

# Default scene length.  A Short is under a minute, so scenes are shorter
# than the long format's 20s: the image should change more often to hold a
# thumb on a phone.
SHORT_SCENE_SECONDS = 12.0
SHORT_MIN_SCENE_SECONDS = 4.0

# A Short is at most three minutes on YouTube, and the passages it is cut
# from are 50-60s.  Declaring the envelope keeps the validator from warning
# every Short as "runtime_short" against the long format's 8-20 minutes.
SHORT_TARGET_MINUTES = (0.1, 3.0)

# Ken Burns moves for a narrow frame.  The long format's deltas are tuned for
# a 1920px-wide canvas; on a 1080px-wide vertical frame the same zoom moves
# barely 80px, which the motion check flags as a frozen image.  These zoom
# further and pan with more headroom so the move is visible on a phone.
VERTICAL_MOTIONS = (
    ("zoom_in", 1.00, 1.16),
    ("pan_right", 1.14, 1.14),
    ("zoom_out", 1.18, 1.00),
    ("pan_left", 1.14, 1.14),
    ("pan_down", 1.14, 1.14),
    ("zoom_in", 1.04, 1.22),
)

STAGES = ("validate", "tts", "images", "assembly", "mix", "render", "captions")


class ShortsInputError(RuntimeError):
    """Raised when no usable shorts array can be found."""


def find_shorts(data: dict) -> list[dict]:
    """
    Pull the shorts out of a loaded JSON document.

    Accepts `narration.shorts` (the shape the review workflow writes) and a
    top-level `shorts`.  Entries may be dicts or plain strings; empty ones
    are dropped rather than rendered as a blank scene.
    """
    raw: object = None
    narration = data.get("narration")
    if isinstance(narration, dict) and narration.get("shorts"):
        raw = narration["shorts"]
    elif data.get("shorts"):
        raw = data["shorts"]

    if not isinstance(raw, list) or not raw:
        raise ShortsInputError(
            "no 'shorts' array found: expected 'narration.shorts' or a "
            "top-level 'shorts' in the JSON"
        )

    shorts: list[dict] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            entry = {"order": index, "title": "", "text": item}
        elif isinstance(item, dict):
            entry = {
                "order": item.get("order", index),
                "title": str(item.get("title") or ""),
                "text": str(item.get("text") or ""),
            }
        else:
            continue
        if entry["text"].strip():
            shorts.append(entry)

    if not shorts:
        raise ShortsInputError("every entry in 'shorts' was empty")
    return shorts


def build_short_workspace(
    short: dict,
    images: list[Path],
    out_dir: Path,
    *,
    title: str,
    author: str,
    voice: str,
    speed: float,
    music: Path | None,
    music_volume: float,
    character: str | None = None,
    character_every: int = 2,
    scene_seconds: float = SHORT_SCENE_SECONDS,
    min_scene_seconds: float = SHORT_MIN_SCENE_SECONDS,
    resolution: tuple[int, int] = VERTICAL,
    target_minutes: tuple[float, float] = SHORT_TARGET_MINUTES,
    motions: tuple[tuple[str, float, float], ...] = VERTICAL_MOTIONS,
    lively: bool = True,
    punches: bool = False,
) -> tuple[Path, Script]:
    """
    Write one vertical workspace for one short.

    Returns the script path and the parsed script.  Mirrors
    `make_project_from_text.main`: split the text into scenes, build the
    script, copy the artwork and font the script references, then write
    `script.json`.  The caller is expected to have checked `out_dir` is free.
    """
    blocks = parse_text(short["text"])
    scenes, overlays = build_scenes(
        blocks, scene_seconds=scene_seconds, min_seconds=min_scene_seconds
    )
    if not scenes:
        raise ShortsInputError(
            f"short {short['order']!r} produced no scenes from its text"
        )

    # The host is copied into the workspace and referenced relatively, so the
    # workspace stays self-contained and portable rather than pointing back
    # at wherever the PNG happened to be on this machine.
    character_ref = character
    character_source: Path | None = None
    if character:
        character_source = Path(character)
        character_ref = f"assets/characters/{character_source.name}"

    payload = build_script(
        scenes,
        overlays,
        images,
        title=title,
        author=author,
        voice=voice,
        speed=speed,
        music=music,
        music_volume=music_volume,
        character=character_ref,
        character_every=character_every,
        # A host anchored at x=0.2 sits off the left edge on a narrow frame,
        # so it is nudged inward for the vertical layout.
        character_x=0.32,
        lively=lively,
        punches=punches,
        resolution=resolution,
        motions=motions,
    )
    payload["video_metadata"]["target_minutes"] = [
        target_minutes[0],
        target_minutes[1],
    ]

    try:
        script = parse_script(payload)
    except ScriptSchemaError as error:
        raise ShortsInputError(
            f"short {short['order']!r} built an invalid script: {error}"
        ) from error

    out_dir.mkdir(parents=True)
    (out_dir / "images").mkdir()
    (out_dir / "assets" / "audio").mkdir(parents=True)

    for scene in script.scenes:
        image = images[(scene.id - 1) % len(images)]
        target = out_dir / "images" / f"scene_{scene.id:03d}{image.suffix.lower()}"
        if not target.exists():
            shutil.copyfile(image, target)

    font = find_font()
    if font is not None:
        font_target = out_dir / "assets" / "fonts" / "handwriting.ttf"
        font_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(font, font_target)

    if music is not None:
        shutil.copyfile(music, out_dir / "assets" / "audio" / f"bg{music.suffix}")

    if character_source is not None:
        character_target = (
            out_dir / "assets" / "characters" / character_source.name
        )
        character_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(character_source, character_target)

    script_path = out_dir / "script.json"
    script_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return script_path, script


def run_pipeline(
    script_path: Path,
    *,
    fake_tts: bool,
    captions: bool,
    fit: str = "cover",
    progress=print,
) -> int:
    """
    Run the stage commands for one workspace, stopping at the first failure.

    The stages are invoked through `autovid.py` rather than imported, so this
    tool tracks the same entry point a human uses and needs no knowledge of
    the stage classes.

    `fit` is handed to the images stage: on a vertical frame, landscape
    artwork has to either crop to the centre (`cover`) or letterbox (`pad`).
    """
    stages = STAGES if captions else STAGES[:-1]
    for stage in stages:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "autovid.py"),
            stage,
            str(script_path),
        ]
        if stage == "validate":
            command.append("--skip-engine-check")
        if stage == "images":
            command.extend(["--fit", fit])
        if stage == "tts" and fake_tts:
            command.extend(["--backend", "fake"])

        progress(f"  $ {' '.join(command[1:])}")
        result = subprocess.run(command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            progress(f"  [x] stage '{stage}' failed; stopping")
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "story",
        type=Path,
        help="story.json (or any JSON with a 'shorts' array)",
    )
    parser.add_argument("--out", required=True, type=Path, help="output folder")
    parser.add_argument(
        "--images",
        default=None,
        help="artwork folder or glob; defaults to the 'images/' folder next "
        "to the JSON",
    )
    parser.add_argument("--title", default=None, help="base title for the Shorts")
    parser.add_argument("--author", default="autovid", help="shown in video_metadata")
    parser.add_argument(
        "--genre",
        choices=sorted(GENRE_VOICES),
        default=None,
        help="picks the default voice (trinhtham, ma, tutien, ngonlu)",
    )
    parser.add_argument("--voice", default=None, help="override the voice name")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--music", default=None, help="background track file")
    parser.add_argument("--music-volume", type=float, default=0.12)
    parser.add_argument(
        "--character",
        default=None,
        help="transparent PNG host to walk into some scenes",
    )
    parser.add_argument("--character-every", type=int, default=2)
    parser.add_argument(
        "--scene-seconds",
        type=float,
        default=SHORT_SCENE_SECONDS,
        help=f"target narration length per scene (default {SHORT_SCENE_SECONDS:g})",
    )
    parser.add_argument(
        "--min-scene-seconds",
        type=float,
        default=SHORT_MIN_SCENE_SECONDS,
        help="scenes shorter than this are merged into the next one",
    )
    parser.add_argument(
        "--size",
        default=f"{VERTICAL[0]}x{VERTICAL[1]}",
        help="vertical frame, WxH (default 1080x1920)",
    )
    parser.add_argument(
        "--min-minutes",
        type=float,
        default=SHORT_TARGET_MINUTES[0],
        help="validator runtime floor for a Short, in minutes",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=SHORT_TARGET_MINUTES[1],
        help="validator runtime ceiling for a Short, in minutes",
    )
    parser.add_argument(
        "--no-lively",
        action="store_true",
        help="use the long format's occasional fade instead of punchy cuts",
    )
    parser.add_argument(
        "--punches",
        action="store_true",
        help="add a punch-in to every fourth scene",
    )
    parser.add_argument(
        "--fit",
        choices=("cover", "pad"),
        default="cover",
        help="'cover' crops artwork to the vertical frame (default), 'pad' "
        "letterboxes it; passed to the images stage when it runs",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="run the full pipeline for every workspace after building it",
    )
    parser.add_argument(
        "--fake-tts",
        action="store_true",
        help="with --run, use placeholder tones instead of the voice model",
    )
    parser.add_argument(
        "--no-captions",
        action="store_true",
        help="with --run, stop after render (skip the subtitle pass)",
    )
    parser.add_argument("--force", action="store_true", help="replace existing output")
    args = parser.parse_args()

    story_path = args.story
    if not story_path.exists():
        print(f"ERROR: input not found: {story_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(story_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"ERROR: {story_path} is not valid JSON: {error}", file=sys.stderr)
        return 1

    try:
        shorts = find_shorts(data)
    except ShortsInputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    images = collect_images(args.images) if args.images else collect_images(
        str(story_path.parent / "images")
    )
    if not images:
        print(
            f"ERROR: no images found (looked at "
            f"{args.images or story_path.parent / 'images'})",
            file=sys.stderr,
        )
        return 1

    try:
        width, height = (int(part) for part in args.size.lower().split("x", 1))
    except (ValueError, TypeError):
        print(f"ERROR: --size must be WxH, got {args.size!r}", file=sys.stderr)
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

    music = Path(args.music) if args.music else None
    if music is not None and not music.exists():
        print(f"ERROR: music not found: {music}", file=sys.stderr)
        return 1

    voice = args.voice or GENRE_VOICES.get(args.genre or "", "Anh Khôi")
    base_title = args.title or story_path.stem

    character = args.character
    if character is not None:
        candidate = Path(character)
        if not candidate.is_absolute() and not candidate.exists():
            candidate = PROJECT_ROOT / candidate
        if not candidate.exists():
            print(f"ERROR: character not found: {character}", file=sys.stderr)
            return 1
        character = str(candidate.resolve())

    print(f"input       : {story_path}")
    print(f"shorts      : {len(shorts)}")
    print(f"images      : {len(images)} source file(s)")
    print(f"frame       : {width}x{height} (vertical)")
    print(f"voice       : {voice} @ {args.speed:.2f}x")
    print(f"music       : {music.name if music else 'none'}")
    print()

    failures = 0
    for short in shorts:
        order = short["order"]
        label = f"short-{order:02d}"
        title = (
            f"{base_title} — {short['title']}" if short["title"] else base_title
        )
        workspace = out / label

        try:
            script_path, script = build_short_workspace(
                short,
                images,
                workspace,
                title=title,
                author=args.author,
                voice=voice,
                speed=args.speed,
                music=music,
                music_volume=args.music_volume,
                character=character,
                character_every=args.character_every,
                scene_seconds=args.scene_seconds,
                min_scene_seconds=args.min_scene_seconds,
                resolution=(width, height),
                target_minutes=(args.min_minutes, args.max_minutes),
                lively=not args.no_lively,
                punches=args.punches,
            )
        except ShortsInputError as error:
            print(f"[x] {label}: {error}")
            failures += 1
            continue

        estimated = sum(len(scene.text) for scene in script.scenes)
        print(
            f"[+] {label}: {len(script.scenes)} scene(s), ~{estimated:,} chars "
            f"-> {script_path}"
        )

        if args.run:
            code = run_pipeline(
                script_path,
                fake_tts=args.fake_tts,
                captions=not args.no_captions,
                fit=args.fit,
            )
            if code != 0:
                failures += 1

    print()
    if failures:
        print(f"done with {failures} problem(s) across {len(shorts)} short(s)")
        return 1
    print(f"done: {len(shorts)} short workspace(s) under {out}")
    if not args.run:
        print(
            "next: run the pipeline for each, e.g.\n"
            f"  python autovid.py validate {out / 'short-01' / 'script.json'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
