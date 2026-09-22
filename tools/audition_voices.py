"""
Audition every VieNeu preset voice on one explainer-style script.

    .venv/bin/python tools/audition_voices.py
    .venv/bin/python tools/audition_voices.py --speed 0.95 --text sample.txt
    .venv/bin/python tools/audition_voices.py --voices "Mai Anh,Minh Đức"

Picking the voice is the one decision in this pipeline that cannot be made
from a number, so this tool exists to make it cheap: every preset reads the
same explainer sample, each clip announces its own voice first, and one
combined file plays them all back to back so the shortlist can be compared
without opening 25 files.

Outputs (default `projects/voice_auditions/`)
-------------------------------------------
    NN_<voice>.mp3        one clip per voice: "<voice>." + the sample
    all_voices.mp3        every clip back to back, 0.6s apart
    index.json            machine-readable: label, duration, chars/s, timing
    index.txt             the same list, readable, in playback order
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovid.infrastructure.audio.tools import measure_loudness  # noqa: E402
from autovid.infrastructure.ffmpeg import (  # noqa: E402
    ffmpeg_available,
    probe_duration,
    run,
)

DEFAULT_OUT = PROJECT_ROOT / "projects" / "voice_auditions"

# A hook plus a body sentence, because the two read differently: the hook
# needs energy, the body needs to stay clear for several minutes.  Taken from
# the demo script so auditions are judged in the format they will ship in.
DEFAULT_SAMPLE = (
    "Quả là gì? Nghe thì dễ, nhưng đến lúc bị hỏi thì chín phần mười người lớn "
    "đều đứng hình. Hôm nay chúng ta sẽ thử đi tìm câu trả lời cho một câu hỏi "
    "tưởng như rất ngớ ngẩn nhưng lại tốn giấy mực của không ít học trò."
)

GAP_SECONDS = 0.6

# Voice clips are cached next to the output, so a second run for one more
# voice does not re-synthesize the twenty-five that already exist.
CACHE_DIR = ".cache"

# Styles that read as explainer/voice-of-authority rather than as fiction.
EXPLAINER_HINTS = ("tin tức", "tự nhiên")


def slug(name: str) -> str:
    """Filesystem-safe ASCII slug, so clips are easy to pass around."""
    # Đ/đ carry no decomposition, so they are transliterated by hand; a
    # dropped letter would turn "Đoan Trang" into "oan-Trang".
    transliterated = name.replace("đ", "d").replace("Đ", "D")
    normalised = unicodedata.normalize("NFKD", transliterated)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in ascii_only)
    return "-".join(part for part in cleaned.split("-") if part) or "voice"


def synthesise(tts, text: str, voice: str, destination: Path) -> None:
    audio = tts.infer(text=text, voice=voice)
    tts.save(audio, str(destination))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--text", default=None, help="read the sample from a file")
    parser.add_argument("--speed", type=float, default=1.0, help="0.5-2.0")
    parser.add_argument(
        "--voices",
        default=None,
        help="comma-separated names to audition, or a substring filter",
    )
    parser.add_argument(
        "--explainer-only",
        action="store_true",
        help="skip the fiction/storytelling voices (tin tức + tự nhiên only)",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output folder"
    )
    args = parser.parse_args()

    if not ffmpeg_available():
        print("ERROR: ffmpeg not found (expected bin/ffmpeg or PATH)", file=sys.stderr)
        return 1

    sample = DEFAULT_SAMPLE
    if args.text:
        sample = Path(args.text).read_text(encoding="utf-8").strip()
    if not sample:
        print("ERROR: the sample text is empty", file=sys.stderr)
        return 1

    out = args.out.resolve()
    if out.exists() and args.force:
        shutil.rmtree(out)
    # An existing folder is reused rather than refused: the cache inside lets
    # a later run audition one extra voice without redoing the rest.
    out.mkdir(parents=True, exist_ok=True)

    try:
        from vieneu import Vieneu
    except ImportError:
        print(
            "ERROR: the VieNeu engine is not installed in this interpreter.\n"
            "    python3 -m venv .venv && .venv/bin/pip install vieneu pillow\n"
            "    then run: .venv/bin/python tools/audition_voices.py",
            file=sys.stderr,
        )
        return 1

    print("Loading VieNeu (v3 Turbo, ONNX/CPU)...")
    started = time.monotonic()
    tts = Vieneu()
    print(f"Loaded in {time.monotonic() - started:.1f}s\n")

    presets = sorted(tts.list_preset_voices(), key=lambda item: item[0])
    if args.voices:
        wanted = [name.strip() for name in args.voices.split(",") if name.strip()]
        presets = [
            (label, name)
            for label, name in presets
            if name in wanted or any(want.lower() in label.lower() for want in wanted)
        ]
    if args.explainer_only:
        presets = [
            (label, name)
            for label, name in presets
            if any(hint in label.lower() for hint in EXPLAINER_HINTS)
        ]

    if not presets:
        print("ERROR: no voices matched", file=sys.stderr)
        return 1

    index: list[dict] = []
    wav_parts: list[Path] = []
    work = out / CACHE_DIR
    work.mkdir(parents=True, exist_ok=True)
    gap = work / "gap.wav"

    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-t",
            f"{GAP_SECONDS:.2f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(gap),
        ]
    )

    try:
        for position, (label, name) in enumerate(presets, start=1):
            tag = f"{position:02d}_{slug(name)}"
            intro = work / f"{tag}_intro.wav"
            body = work / f"{tag}_body.wav"
            joined = work / f"{tag}.wav"

            # The voice announces itself, so the combined file needs no note
            # to follow and a single clip is identifiable on its own.
            cached = intro.exists() and body.exists()
            if cached and not args.force:
                compute_s = 0.0
            else:
                intro_text = f"Giọng {name}."
                started = time.monotonic()
                try:
                    synthesise(tts, intro_text, name, intro)
                    synthesise(tts, sample, name, body)
                except Exception as error:  # noqa: BLE001 - keep auditioning
                    print(
                        f"[x] {name}: {type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
                    continue
                compute_s = time.monotonic() - started

            listing = work / f"{tag}_concat.txt"
            listing.write_text(
                "".join(f"file '{part}'\n" for part in (intro, body)),
                encoding="utf-8",
            )
            run(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(joined),
                ]
            )

            mp3 = out / f"{tag}.mp3"
            run(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(joined),
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(mp3),
                ]
            )

            body_s = probe_duration(body)
            loudness = measure_loudness(joined)
            index.append(
                {
                    "order": position,
                    "name": name,
                    "label": label,
                    "explainer_style": any(
                        hint in label.lower() for hint in EXPLAINER_HINTS
                    ),
                    "file": mp3.name,
                    "duration_s": round(probe_duration(joined), 3),
                    "sample_seconds": round(body_s, 3),
                    "chars_per_second": round(len(sample) / body_s, 1),
                    "integrated_lufs": loudness.integrated_lufs,
                    "true_peak_db": loudness.true_peak_db,
                    "synthesis_seconds": round(compute_s, 1),
                }
            )
            wav_parts.extend([gap, joined])
            print(
                f"[{position:02d}/{len(presets)}] {tag}.mp3  "
                f"{body_s:.1f}s sample, {len(sample) / body_s:.1f} chars/s, "
                f"{loudness.integrated_lufs:.1f} LUFS, "
                + ("reused from cache" if cached else f"synced in {compute_s:.1f}s"),
                flush=True,
            )

        if not index:
            print("ERROR: every voice failed", file=sys.stderr)
            return 1

        listing = work / "all_concat.txt"
        listing.write_text(
            "".join(f"file '{part}'\n" for part in wav_parts), encoding="utf-8"
        )
        run(
            [
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(out / "all_voices.mp3"),
            ]
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    (out / "index.json").write_text(
        json.dumps(
            {
                "sample_chars": len(sample),
                "speed": args.speed,
                "gap_seconds": GAP_SECONDS,
                "voices": index,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "DANH SÁCH NGHE THỬ GIỌNG (thứ tự trong all_voices.mp3)",
        f"Nội dung mẫu: {len(sample)} ký tự, speed {args.speed}",
        "",
    ]
    for entry in index:
        mark = "*" if entry["explainer_style"] else " "
        lines.append(
            f"{entry['order']:02d}{mark} {entry['name']:<16} "
            f"{entry['chars_per_second']:>5.1f} chars/s  "
            f"{entry['integrated_lufs']:>6.1f} LUFS  {entry['file']}"
        )
        lines.append(f"      {entry['label']}")
    lines.append("")
    lines.append("* = phong cách tin tức/tự nhiên (hợp thuyết minh hơn kể chuyện)")
    (out / "index.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n{len(index)} voice(s) auditioned -> {out}")
    print(f"nghe 1 file: {out / 'all_voices.mp3'}")
    print(f"danh sách  : {out / 'index.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
