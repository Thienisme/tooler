"""
Render a long narration with VieNeu-TTS (local) and report timing.

Sources (choose ONE):
  --project tutien-001        -> extract narration from projects/tutien-001/story.json
                                 (reads narration.segments[].text automatically)
  --text path/to/file.txt     -> read plain text file

Examples:
  # Render tutien-001 with Mỹ Duyên at natural speed
  .venv/bin/python render_vieneu_long.py --project tutien-001 -v "Mỹ Duyên"

  # Render a text file with Thái Sơn, slower (0.85)
  .venv/bin/python render_vieneu_long.py --text story.txt -v "Thái Sơn" --speed 0.85

  # Preview only (no rendering): shows char count, chunks and output path
  .venv/bin/python render_vieneu_long.py --project mystery-004 --dry-run

Defaults: voice=Anh Khôi, speed=1.0.
Output default: projects/<id>/audio/narration_vieneu.mp3 for --project,
                test_vieneu_voices_output/render_out.mp3 for --text.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
    VieNeuTTSProvider,
)

MIN_CHARS = 100  # below this, a narration is almost certainly a stub

# Genre -> voice mapping (edit here to change your production voices).
GENRE_VOICES = {
    "tutien": "Ngọc Huyền",   # truyện tu tiên -> Nữ Bắc, đọc trôi, siết nhịp
    "ma": "Đức Trí",          # truyện ma / kinh dị -> Nam Bắc, đọc truyện chậm rãi
    "trinhtham": "Anh Khôi",  # trinh thám / mystery -> Nam Bắc, kể chuyện nhanh
}  # noqa: E501

# Genre -> default speed override (only applies when --genre is given
# and the user does NOT pass an explicit --speed).
GENRE_SPEEDS = {
    "tutien": 0.9,   # tu tiên đọc hơi nhanh -> chậm lại một chút
    "ma": 1.0,
    "trinhtham": 1.0,
}


def load_narration_from_project(project: str) -> str:
    """Extract narration text from projects/<project>/story.json."""
    story_path = Path("projects") / project / "story.json"
    if not story_path.exists():
        sys.exit(f"ERROR: not found: {story_path}")

    data = json.loads(story_path.read_text(encoding="utf-8"))
    segments = (data.get("narration") or {}).get("segments") or []
    parts = [
        seg.get("text", "").strip()
        for seg in segments
        if isinstance(seg, dict) and seg.get("text", "").strip()
    ]
    if not parts:
        sys.exit(
            f"ERROR: {story_path} has no narration.segments text "
            "(project looks empty)."
        )
    return "\n\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render long narration to MP3 with VieNeu-TTS."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--project", "-p",
        help="Project id under projects/ (reads story.json narration).",
    )
    source.add_argument(
        "--text", "-t", type=Path,
        help="Path to a plain-text file to render.",
    )
    parser.add_argument(
        "--genre", "-g", choices=sorted(GENRE_VOICES),
        help=(
            "Pick the production voice by story genre: "
            + ", ".join(f"{k}={v}" for k, v in GENRE_VOICES.items())
            + ". Overrides --voice."
        ),
    )
    parser.add_argument(
        "--voice", "-v", default=None,
        help="VieNeu preset voice name (ignored if --genre is given).",
    )
    parser.add_argument(
        "--speed", type=float, default=None,
        help="Speaking rate: 1.0 natural, 0.85 slower, 1.15 faster.",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output MP3 path (defaults depend on source).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be rendered (chars, chunks, output) and exit.",
    )
    args = parser.parse_args()

    voice = args.voice or "Anh Khôi"
    if args.genre:
        voice = GENRE_VOICES[args.genre]

    speed = (
        args.speed
        if args.speed is not None
        else GENRE_SPEEDS.get(args.genre, 1.0)
    )

    if args.project:
        project = args.project.rstrip("/")
        text = load_narration_from_project(project)
        output = args.output or Path("projects") / project / "audio" / (
            "narration_vieneu.mp3"
        )
    else:
        if not args.text.exists():
            sys.exit(f"ERROR: not found: {args.text}")
        text = args.text.read_text(encoding="utf-8").strip()
        output = args.output or Path("test_vieneu_voices_output/render_out.mp3")

    if len(text) < MIN_CHARS:
        sys.exit(
            f"ERROR: narration is only {len(text)} chars (stub?). "
            f"Refusing to render."
        )

    est_chunks = (len(text) + 999) // 1000
    est_audio_min = len(text) / 16 / 60  # ~16 chars/sec measured on this machine
    print(f"Source : {'project ' + args.project if args.project else args.text}")
    print(f"Text   : {len(text)} chars (~{est_chunks} chunks, "
          f"~{est_audio_min:.0f} min audio, ~{est_audio_min:.1f} min render)")
    print(f"Voice  : {voice}"
          + (f" (genre: {args.genre})" if args.genre else "")
          + f" | Speed: {speed}")
    print(f"Output : {output}")

    if args.dry_run:
        print("DRY RUN - nothing rendered.")
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Persistent cache dir for resume support.
    # Chunks are saved as:
    #   projects/<id>/audio/_vieneu_chunks/chunk_001.mp3 ...
    # On resume, already-completed chunks are skipped automatically.
    # The cache dir is cleaned up only after a successful full render.
    # ----------------------------------------------------------------
    if args.project:
        cache_dir = output.parent / "_vieneu_chunks"
    else:
        cache_dir = output.parent / "_vieneu_chunks_render"

    # Report resume status before starting
    if cache_dir.exists():
        done = sorted(cache_dir.glob("chunk_*.mp3"))
        done_ok = [f for f in done if f.stat().st_size > 0]
        if done_ok:
            print(f"Resume : found {len(done_ok)} cached chunk(s) in {cache_dir}")
            print(f"         will skip those and continue from the next one")
    else:
        print(f"Cache  : {cache_dir}")

    t0 = time.time()
    provider = VieNeuTTSProvider(voice=voice, speed=speed)
    duration = provider.generate_long(text, output, cache_dir=cache_dir)
    elapsed = time.time() - t0
    rtf = elapsed / duration if duration > 0 else float("inf")

    print(
        f"DONE: audio {duration / 60:.1f} min | "
        f"took {elapsed / 60:.1f} min | RTF {rtf:.2f}"
    )
    print(f"Output: {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)")

    # Clean up chunk cache after successful render
    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)
        print(f"Cache  : cleaned up {cache_dir}")


if __name__ == "__main__":
    main()
