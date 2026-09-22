#!/usr/bin/env python3
"""
Render every character effect into one labelled video, so a script can be
written against effects that have actually been seen.

This uses the same code the pipeline uses -- `resolve_character_cues`,
`SpritePlanner` and `build_scene_filtergraph` -- rather than a separate
demo renderer.  That is deliberate: a preview built with its own filtergraph
would keep working after the real one broke, which is the opposite of what a
preview is for.

    python3 tools/preview_character_effects.py
    python3 tools/preview_character_effects.py --effect boing,drop_bounce
    python3 tools/preview_character_effects.py --character assets/characters/host_point.png

Output: `tmp/character_effects/effects.mp4` (plus the per-effect clips).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autovid.domain.characters import resolve_character_cues  # noqa: E402
from autovid.domain.script import parse_script  # noqa: E402
from autovid.infrastructure.ffmpeg import FFMPEG, ffmpeg_available  # noqa: E402
from autovid.infrastructure.video.filters import (  # noqa: E402
    ImpactPunch,
    OverlayLayer,
    build_scene_filtergraph,
)
from autovid.infrastructure.video.sprites import SpritePlanner  # noqa: E402
from autovid.infrastructure.video.text import render_text_layer  # noqa: E402

# Every variant the schema accepts, grouped the way the schema groups them.
ENTERS = (
    "none",
    "fade_in",
    "pop",
    "fly_in",
    "slide_in",
    "drop_bounce",
    "spin_in",
    "zoom_in",
)
EXITS = (
    "none",
    "fade_out",
    "shrink_out",
    "fly_out",
    "slide_out",
    "drop_out",
    "spin_out",
    "zoom_out",
)
IDLES = ("none", "bob", "sway", "bob_sway", "shake", "talk")
PRESETS = ("pop", "boing", "whoosh", "ta_da", "sneak", "ninja")

DEFAULT_CHARACTER = PROJECT_ROOT / "assets" / "characters" / "host_idle.png"


def make_background(path: Path, size: tuple[int, int]) -> Path:
    """A plate with a horizon and a wall/floor split, like a real scene."""
    width, height = size
    horizon = int(height * 0.72)
    image = Image.new("RGB", size, (30, 42, 56))
    draw = ImageDraw.Draw(image)
    for y in range(horizon):
        blend = y / max(horizon, 1)
        draw.line(
            (0, y, width, y),
            fill=(
                int(26 + 40 * blend),
                int(38 + 48 * blend),
                int(56 + 40 * blend),
            ),
        )
    draw.rectangle((0, horizon, width, height), fill=(46, 60, 74))
    draw.line((0, horizon, width, horizon), fill=(150, 170, 185), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def build_scene_script(
    *,
    background: Path,
    character: Path,
    resolution: str,
    fps: int,
    cue: dict,
    impact: dict | None = None,
) -> "object":
    scene: dict = {
        "id": 1,
        "text": "Câu mẫu để dựng hiệu ứng.",
        "image_file": str(background),
        "ken_burns": {"enabled": True, "type": "zoom_in",
                      "start_scale": 1.0, "end_scale": 1.04},
        "characters": [{"image_file": str(character), **cue}],
    }
    if impact is not None:
        scene["impact"] = impact
    return parse_script(
        {
            "video_metadata": {
                "title": "character effects preview",
                "resolution": resolution,
                "fps": fps,
            },
            "tts_config": {"voice": "preview", "speed": 1.0},
            "scenes": [scene],
        }
    )


def render_variant(
    *,
    script,
    label: str,
    seconds: float,
    resolution: tuple[int, int],
    fps: int,
    cache_dir: Path,
    destination: Path,
    explicit_font: Path | None = None,
) -> None:
    """Render one effect the way the assembly stage would."""
    frames = int(round(seconds * fps))
    duration_s = frames / fps

    timeline = {
        "scenes": [
            {
                "id": 1,
                "start_s": 0.0,
                "narration_s": duration_s,
                "pause_after_s": 0.0,
                "end_s": duration_s,
            }
        ]
    }
    plan = resolve_character_cues(script, timeline=timeline)
    cue = plan.for_scene(1)[0]

    planner = SpritePlanner(cache_dir)
    sprite_layers = planner.plan(
        cue, source=Path(cue.image_file), frame_size=resolution, fps=fps
    )

    # The punch-in gets its own variant, so only that one carries one.
    impact_spec = script.scenes[0].impact
    impact = None
    if impact_spec is not None and impact_spec.enabled:
        impact = ImpactPunch(
            offset_s=seconds * 0.55,
            intensity=impact_spec.intensity,
            shake_px=impact_spec.shake_px,
            duration_s=impact_spec.duration_ms / 1000.0,
            flash=impact_spec.flash,
        )

    label_layer = render_text_layer(
        text=label,
        font_path=_label_font(explicit_font),
        font_size=max(int(resolution[1] * 0.048), 18),
        colour="#FFFFFF",
        stroke_colour="#101418",
        stroke_width=5,
        position="top",
        frame_size=resolution,
        destination=cache_dir / f"label_{abs(hash(label)) % 10**8}.png",
    )

    graph = build_scene_filtergraph(
        frames=frames,
        frame_size=resolution,
        fps=fps,
        motion={
            "enabled": True,
            "type": "zoom_in",
            "start_scale": 1.0,
            "end_scale": 1.04,
        },
        overlays=[
            OverlayLayer(
                path=label_layer.path,
                start_s=0.0,
                end_s=duration_s,
                animation="none",
            )
        ],
        sprites=sprite_layers,
        impact=impact,
    )

    inputs: list[str] = ["-i", str(script.scenes[0].image_file)]
    for layer in sprite_layers:
        inputs += [
            "-loop", "1", "-framerate", str(fps), "-t", f"{duration_s:.4f}",
            "-i", str(layer.frame.path),
        ]
    inputs += [
        "-loop", "1", "-framerate", str(fps), "-t", f"{duration_s:.4f}",
        "-i", str(label_layer.path),
    ]
    if impact is not None and impact.flash != "none":
        colour = "white" if impact.flash == "white" else "black"
        flash_s = max(impact.duration_s * 0.6, 0.05)
        inputs += [
            "-f", "lavfi", "-i",
            f"color=c={colour}:s={resolution[0]}x{resolution[1]}"
            f":r={fps}:d={flash_s:.4f}",
        ]

    result = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", graph,
            "-map", "[out]",
            "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-an",
            "-movflags", "+faststart",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label}: {result.stderr.strip()[-400:]}")


def _label_font(explicit: Path | None = None) -> Path:
    """
    A font for the burned-in labels.

    Bundled fonts live inside a workspace (they are produced by the demo
    project generator), so the search covers those too before giving up with
    instructions rather than silently rendering boxes.
    """
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"font not found: {explicit}")
        return explicit

    search: list[Path] = []
    search.extend(sorted((PROJECT_ROOT / "assets" / "fonts").glob("*.ttf")))
    search.extend(sorted((PROJECT_ROOT / "data" / "fonts").glob("*.ttf")))
    for workspace in sorted((PROJECT_ROOT / "projects").glob("*/assets/fonts")):
        search.extend(sorted(workspace.glob("*.ttf")))

    if search:
        return search[0]

    raise FileNotFoundError(
        "no .ttf font found for the labels; pass --font /path/to/font.ttf "
        "(assets/fonts, data/fonts and projects/*/assets/fonts were searched)"
    )


def variants(selected: set[str]) -> list[tuple[str, dict, dict | None]]:
    """(label, cue, impact) for every variant that was asked for."""
    items: list[tuple[str, dict, dict | None]] = []

    for enter in ENTERS:
        cue = {
            "x": 0.5 if enter != "drop_bounce" else 0.3,
            "y": 0.94,
            "height": 0.62,
            "enter": {"type": enter, "from": "left", "duration_ms": 700},
            "idle": {"type": "none"},
            "exit": {"type": "none"},
        }
        items.append((f"enter: {enter}", cue, None))

    for exit_type in EXITS:
        cue = {
            "x": 0.5,
            "y": 0.94,
            "height": 0.62,
            "enter": {"type": "none"},
            "idle": {"type": "none"},
            "exit": {"type": exit_type, "to": "right", "duration_ms": 700},
        }
        items.append((f"exit: {exit_type}", cue, None))

    for idle in IDLES:
        cue = {
            "x": 0.5,
            "y": 0.94,
            "height": 0.62,
            "enter": {"type": "none"},
            "idle": {"type": idle, "amplitude_px": 18, "period_s": 2.0},
            "exit": {"type": "none"},
        }
        items.append((f"idle: {idle}", cue, None))

    for preset in PRESETS:
        cue = {"preset": preset, "x": 0.5, "y": 0.94, "height": 0.62}
        items.append((f"preset: {preset}", cue, None))

    items.append(
        (
            "impact: punch-in + flash",
            {
                "x": 0.5,
                "y": 0.94,
                "height": 0.62,
                "enter": {"type": "none"},
                "idle": {"type": "talk", "amplitude_px": 14, "period_s": 0.4},
                "exit": {"type": "none"},
            },
            {
                "at_offset_ms": 0,
                "intensity": 0.1,
                "shake_px": 14,
                "duration_ms": 600,
                "flash": "white",
            },
        )
    )

    if selected:
        items = [
            item
            for item in items
            if any(
                keyword in item[0].lower()
                for keyword in (value.lower() for value in selected)
            )
        ]
    return items


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render every character effect into one labelled video."
    )
    parser.add_argument(
        "--character",
        type=Path,
        default=DEFAULT_CHARACTER,
        help="sprite to animate (default: assets/characters/host_idle.png)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "tmp" / "character_effects",
        help="output directory (default: tmp/character_effects)",
    )
    parser.add_argument("--resolution", default="1280x720")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--seconds",
        type=float,
        default=2.4,
        help="length of each effect clip (default: 2.4)",
    )
    parser.add_argument(
        "--effect",
        default="",
        help="comma-separated filter on the labels, e.g. 'enter,spin'",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=None,
        help="font used for the burned-in labels (default: first .ttf found)",
    )
    args = parser.parse_args()

    if not ffmpeg_available():
        print("ffmpeg not found in bin/ or on PATH", file=sys.stderr)
        return 1

    if not args.character.exists():
        print(
            f"character not found: {args.character}\n"
            "Run: python3 tools/make_demo_character.py",
            file=sys.stderr,
        )
        return 1

    width, height = (int(part) for part in args.resolution.lower().split("x"))
    args.out.mkdir(parents=True, exist_ok=True)
    background = make_background(args.out / "background.png", (width, height))
    cache = args.out / "cache"

    selected = {part.strip() for part in args.effect.split(",") if part.strip()}
    items = variants(selected)
    if not items:
        print("no effect matched that filter", file=sys.stderr)
        return 1

    clips: list[Path] = []
    print(f"rendering {len(items)} effect(s) at {args.resolution}@{args.fps}fps")
    for index, (label, cue, impact) in enumerate(items, start=1):
        script = build_scene_script(
            background=background,
            character=args.character,
            resolution=args.resolution,
            fps=args.fps,
            cue=cue,
            impact=impact,
        )
        clip = args.out / f"{index:02d}_{label.replace(': ', '_').replace(' ', '-')}.mp4"
        render_variant(
            script=script,
            label=label,
            seconds=args.seconds,
            resolution=(width, height),
            fps=args.fps,
            cache_dir=cache,
            destination=clip,
            explicit_font=args.font,
        )
        clips.append(clip)
        print(f"  [{index}/{len(items)}] {label}")

    listing = args.out / "concat.txt"
    listing.write_text(
        "".join(f"file '{clip.resolve()}'\n" for clip in clips), encoding="utf-8"
    )
    combined = args.out / "effects.mp4"
    result = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", str(combined),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"could not combine the clips: {result.stderr.strip()}", file=sys.stderr)
        return 1

    print()
    print(f"one video with every effect: {combined}")
    print(f"per-effect clips:            {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
