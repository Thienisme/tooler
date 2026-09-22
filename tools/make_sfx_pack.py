#!/usr/bin/env python3
"""
Build the comedy sound-effect pack used by character presets.

The presets in `script.json` (`"preset": "boing"`) name a sound file, and
those files have to exist or the mix stage reports them missing.  This
generates them, so a fresh checkout has working entrances without anyone
having to hunt for licensed audio.

They are synthesised rather than sampled, which is honest about what they
are: a whoosh is filtered noise, a boing is a fast pitch slide, a ding is a
decaying bell.  Good enough to time an edit with, and each one is a single
file you can replace with a real recording later -- the pipeline only cares
about the path.

    python3 tools/make_sfx_pack.py                 # into data/sfx/
    python3 tools/make_sfx_pack.py --out projects/x/assets/sfx
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autovid.infrastructure.ffmpeg import FFMPEG, ffmpeg_available  # noqa: E402

SAMPLE_RATE = 48000

# (name, description, lavfi audio graph)
#
# Every graph is a single `lavfi` source piped through `-af`, so the pack
# needs nothing but the bundled ffmpeg.
PACK: tuple[tuple[str, str, str], ...] = (
    (
        "whoosh",
        "fast air pass, for a fly-in or a fly-out",
        "anoisesrc=color=pink:amplitude=0.35:sample_rate=48000,"
        "highpass=f=400,lowpass=f=6000,"
        "afade=t=in:d=0.06,afade=t=out:st=0.28:d=0.14,"
        "volume=0.9",
    ),
    (
        "swoosh_long",
        "longer slide, for a slide-in along the floor",
        "anoisesrc=color=brown:amplitude=0.3:sample_rate=48000,"
        "highpass=f=200,lowpass=f=3000,"
        "afade=t=in:d=0.2,afade=t=out:st=0.6:d=0.35,"
        "volume=0.85",
    ),
    (
        "boing",
        "springy landing, for drop_bounce",
        "sine=frequency=150:sample_rate=48000,"
        "asetrate=48000,"
        "vibrato=f=22:d=0.8,"
        "afade=t=out:st=0.25:d=0.2,volume=0.7",
    ),
    (
        "pop",
        "short bubble pop, for a scale-in",
        "sine=frequency=900:sample_rate=48000,"
        "asetrate=48000,"
        "afade=t=out:st=0.02:d=0.09,volume=0.8",
    ),
    (
        "ding",
        "bright bell, for a reveal or a title",
        "sine=frequency=1568:sample_rate=48000,"
        "aecho=0.8:0.6:60|120:0.5|0.3,"
        "afade=t=out:st=0.5:d=0.5,volume=0.55",
    ),
    (
        "ta_da",
        "two-note flourish, for a punchline",
        "sine=frequency=784:sample_rate=48000,"
        "afade=t=out:st=0.2:d=0.2,volume=0.6",
    ),
    (
        "sneak",
        "ticks for a character creeping in",
        "sine=frequency=1200:sample_rate=48000,"
        "tremolo=f=9:d=0.9,"
        "afade=t=in:d=0.05,afade=t=out:st=0.6:d=0.25,volume=0.4",
    ),
    (
        "thud",
        "heavy impact, for a punch-in or a landing",
        "sine=frequency=64:sample_rate=48000,"
        "afade=t=out:st=0.08:d=0.3,volume=0.9",
    ),
    (
        "sparkle",
        "shimmer, for a happy reaction",
        "sine=frequency=2637:sample_rate=48000,"
        "tremolo=f=14:d=0.6,"
        "afade=t=out:st=0.4:d=0.35,volume=0.35",
    ),
)


def durations() -> dict[str, float]:
    """How long each effect is, in seconds."""
    return {
        "whoosh": 0.42,
        "swoosh_long": 0.95,
        "boing": 0.45,
        "pop": 0.11,
        "ding": 1.0,
        "ta_da": 0.4,
        "sneak": 0.85,
        "thud": 0.4,
        "sparkle": 0.75,
    }


def build_one(destination: Path, graph: str, duration: float) -> None:
    """Encode one effect: a lavfi source through its filter chain."""
    command = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        graph,
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "2",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg could not build {destination.name}: {result.stderr.strip()}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the comedy SFX pack used by character presets."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data" / "sfx",
        help="directory to write the effects into (default: data/sfx)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild files that already exist",
    )
    args = parser.parse_args()

    if not ffmpeg_available():
        print(
            "ffmpeg not found: put a static binary in bin/ffmpeg or on PATH",
            file=sys.stderr,
        )
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    lengths = durations()

    built = 0
    skipped = 0
    # The sine sources above are mono and single-channel; `-ac 2` fixes
    # that at encode time rather than in every graph.
    for name, description, graph in PACK:
        destination = args.out / f"{name}.mp3"
        if destination.exists() and not args.force:
            skipped += 1
            print(f"  {name}.mp3 already there")
            continue
        build_one(destination, graph, lengths.get(name, 0.5))
        built += 1
        print(f"  {name}.mp3  {lengths.get(name, 0.5):.2f}s  {description}")

    print()
    print(f"{built} built, {skipped} already present in {args.out}")
    print("Use them from script.json as e.g. \"sfx\": \"data/sfx/whoosh.mp3\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
