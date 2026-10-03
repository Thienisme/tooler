#!/usr/bin/env python3
"""
Preview a script's character staging without a full render.

The assembler's real output needs ffmpeg.  This tool answers the smaller
question -- "does the staging look right?" -- by drawing the same scene
composition at preview size and writing either an animated GIF or an
MJPEG movie, both of which need no external binary.

    python3 tools/make_autodirector_preview.py script.json out.mp4
    python3 tools/make_autodirector_preview.py script.json out.gif --scenes 2 21

What is drawn: the scene artwork with its Ken Burns move, the characters
placed exactly as the script says (x/y/height/flip), a drop_bounce or
slide_in entrance, and a shrink_out exit.  The storytelling frame is drawn
too: the panel PNG from assets/frames, the artwork cropped into the panel's
art window, the matte backdrop around it, and the narrator standing
outside when `show_narrator` is on.  What is *not* drawn: the talk-cycle
mouth and pose swaps, which need the measured narration stage to time them.
The point is placement, so placement is what is real.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
# The project root holds an `autovid.py` that shadows the package of the
# same name, so src/ has to lead sys.path -- not merely be present in it,
# because an editable install already appends it near the end.  Any entry
# that would also resolve `autovid` (the bare '' or cwd the interpreter
# puts first when the script is imported rather than run) is demoted, and
# the root is appended last because it is only needed for `tools.`.
sys.path[:] = [
    entry
    for entry in sys.path
    if entry not in ("", ".", str(Path.cwd()))
]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from autovid.domain.characters import (  # noqa: E402
    CUE_HALF_SPAN,
    STORY_HOST_HALF_SPAN,
)
from autovid.domain.script import (  # noqa: E402
    STORY_FRAME_HOST_HEIGHT,
    STORY_FRAME_HOST_X,
    STORY_FRAME_HOST_Y,
)
from autovid.infrastructure.video.filters import (  # noqa: E402
    STORY_FRAME_BACKDROP_RGB,
    story_inset_for_style,
)
from tools.mjpeg_movie import write_mjpeg_movie  # noqa: E402

# A preview frame is small on purpose: MJPEG is uncompressed-ish, so a
# full-HD preview would be hundreds of megabytes for a few seconds.
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540

# Loaded on first use by `_load_registry`.
_REGISTRY: dict | None = None


def _resolve(path: str) -> Path:
    """Asset paths in a script are workspace- or repo-relative."""
    candidate = Path(path)
    if candidate.exists():
        return candidate
    return PROJECT_ROOT / path


def _ken_burns_scale(scene: dict, progress: float) -> float:
    """Interpolate the scene's Ken Burns scale across `progress` (0..1)."""
    motion = scene.get("ken_burns") or {}
    start = float(motion.get("start_scale", 1.0) or 1.0)
    end = float(motion.get("end_scale", start) or start)
    return start + (end - start) * progress


def _crop_to_ratio(image: Image.Image, width: int, height: int) -> Image.Image:
    """Centre-crop to the preview aspect ratio, then scale."""
    target_ratio = width / height
    source_ratio = image.width / image.height if image.height else target_ratio
    if abs(source_ratio - target_ratio) < 0.01:
        return image.resize((width, height), Image.LANCZOS)
    if source_ratio > target_ratio:
        # Too wide: trim the sides.
        crop_width = int(round(image.height * target_ratio))
        left = (image.width - crop_width) // 2
        box = (left, 0, left + crop_width, image.height)
    else:
        # Too tall: trim top and bottom, biased low so faces stay in frame.
        crop_height = int(round(image.width / target_ratio))
        top = int((image.height - crop_height) * 0.35)
        box = (0, top, image.width, top + crop_height)
    return image.crop(box).resize((width, height), Image.LANCZOS)


def _frame_for_scene(script: dict, scene: dict) -> dict | None:
    """
    The storytelling frame a scene should draw, or None for a bare picture.

    Mirrors the pipeline's own rule: the script-level block sets the frame
    for every scene, and a scene opts out with `"story_frame": false`.
    """
    config = script.get("story_frame") or {}
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    if scene.get("story_frame", True) is False:
        return None
    return config


def _panel_box(
    frame: dict, width: int, height: int
) -> tuple[int, int, int, int]:
    """The frame's own pixel box (x, y, w, h) on the preview canvas."""
    x = int(round(float(frame.get("x", 0.04)) * width))
    y = int(round(float(frame.get("y", 0.06)) * height))
    w = int(round(float(frame.get("width", 0.66)) * width))
    h = int(round(float(frame.get("height", 0.88)) * height))
    return x, y, max(2, w), max(2, h)


def _art_window(
    frame: dict, box: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """
    The artwork window inside the panel: the box shrunk by the style's mat.

    The same arithmetic `build_story_art_block` runs in the real filter
    graph, so a character placed at `y = 0.9` lands on the picture here
    exactly where the assembler would put it.
    """
    x, y, w, h = box
    inset = frame.get("art_inset")
    if inset is None:
        inset = story_inset_for_style(str(frame.get("style", "border")))
    if isinstance(inset, dict):
        top = max(int(round(h * float(inset.get("t", 0.0)))), 1)
        bottom = max(int(round(h * float(inset.get("b", 0.0)))), 1)
        left = max(int(round(w * float(inset.get("l", 0.0)))), 1)
        right = max(int(round(w * float(inset.get("r", 0.0)))), 1)
    else:
        mat = max(int(round(min(w, h) * float(inset))), 1)
        top = bottom = left = right = mat
    return (
        x + left,
        y + top,
        max(2, w - left - right),
        max(2, h - top - bottom),
    )


def _frame_panel(frame: dict, box: tuple[int, int, int, int]) -> Image.Image | None:
    """The frame's baked panel PNG, resized to the preview box, or None."""
    relative = frame.get("frame_png") or str(
        Path("assets") / "frames" / f"story_frame_{frame.get('style', 'border')}.png"
    )
    path = _resolve(str(relative))
    if not path.exists():
        print(
            f"WARN story_frame: panel {relative} not found -- "
            "run python3 tools/make_story_frame.py --style "
            f"{frame.get('style', 'border')}",
            file=sys.stderr,
        )
        return None
    _, _, w, h = box
    with Image.open(path) as raw:
        return raw.convert("RGBA").resize((w, h), Image.LANCZOS)


def _entrance_offset(character: dict, seconds: float) -> tuple[int, int]:
    """
    Pixel (dx, dy) the character still has to travel at `seconds` into the
    scene.  Returns (0, 0) once the entrance is over.
    """
    enter = character.get("enter") or {}
    kind = enter.get("type")
    duration = float(enter.get("duration_ms") or 0) / 1000.0
    if kind in (None, "none") or duration <= 0 or seconds >= duration:
        return (0, 0)
    progress = seconds / duration
    travel = int(round((1.0 - progress) * 100))
    if kind == "drop_bounce":
        # Rise to half the travel and settle: the arc, not a slide.
        return (0, -int(round(travel * 0.5 * (1 - progress) * 2)))
    if kind == "slide_in":
        edge = enter.get("from", "bottom")
        if edge == "left":
            return (travel * 4, 0)
        if edge == "right":
            return (-travel * 4, 0)
        return (0, travel)
    if kind == "fly_in":
        return (travel * 3, -travel)
    return (0, 0)


def _exit_scale(character: dict, seconds: float, scene_seconds: float) -> float:
    exit_ = character.get("exit") or {}
    kind = exit_.get("type")
    duration = float(exit_.get("duration_ms") or 0) / 1000.0
    if kind in (None, "none") or duration <= 0:
        return 1.0
    start = scene_seconds - duration
    if seconds < start:
        return 1.0
    progress = (seconds - start) / duration
    if kind in ("shrink_out", "zoom_out"):
        return max(0.05, 1.0 - progress)
    return 1.0


def _composite(
    canvas: Image.Image,
    sprite: Image.Image,
    character: dict,
    *,
    seconds: float,
    scene_seconds: float,
    frame_width: int,
    frame_height: int,
) -> None:
    height_fraction = float(character.get("height", 0.45) or 0.45)
    x_fraction = float(character.get("x", 0.5) or 0.5)
    y_fraction = float(character.get("y", 0.9) or 0.9)

    # The script's fractions are of a 1080-line frame; map them onto the
    # preview's own pixel grid so the composition matches by proportion.
    target_height = max(1, round(height_fraction * frame_height))
    scale = target_height / max(1, sprite.height)
    target_width = max(1, round(sprite.width * scale))

    working = sprite.resize((target_width, target_height), Image.LANCZOS)
    scale_out = _exit_scale(character, seconds, scene_seconds)
    if scale_out < 1.0:
        working = working.resize(
            (
                max(1, round(target_width * scale_out)),
                max(1, round(target_height * scale_out)),
            ),
            Image.LANCZOS,
        )
        target_width, target_height = working.size

    dx, dy = _entrance_offset(character, seconds)
    centre_x = x_fraction * frame_width + dx
    foot_y = y_fraction * frame_height + dy
    left = int(round(centre_x - target_width / 2))
    top = int(round(foot_y - target_height))

    canvas.alpha_composite(working, (max(0, left), max(0, top)))


def _load_registry() -> dict:
    """The character registry, cached: it names the narrator's sprite."""
    global _REGISTRY
    if _REGISTRY is None:
        path = PROJECT_ROOT / "assets" / "characters" / "characters.json"
        if path.exists():
            _REGISTRY = json.loads(path.read_text(encoding="utf-8"))
        else:
            _REGISTRY = {}
    return _REGISTRY


def _narrator_sprite(frame: dict) -> Image.Image | None:
    """
    The host that stands outside the panel, or None when there is none.

    `story_frame.use` names a registry key exactly like `host_layout.use`;
    an explicit `image_file` overrides it.
    """
    if not frame.get("show_narrator", True):
        return None
    relative = frame.get("image_file")
    if not relative:
        key = frame.get("use")
        entry = _load_registry().get(key) if key else None
        relative = entry.get("image_file") if isinstance(entry, dict) else None
    if not relative:
        return None
    path = _resolve(str(relative))
    if not path.exists():
        print(f"WARN story_frame: narrator {relative} not found", file=sys.stderr)
        return None
    with Image.open(path) as raw:
        return raw.convert("RGBA")


def _narrator_hidden(frame: dict, characters: list[dict]) -> bool:
    """
    Whether a guest stands in the narrator's strip, so it should stand down.

    The pipeline decides this per cue from the measured narration
    (`_host_suppression_windows`): a guest landing fully inside the panel
    is the story the frame exists to show and leaves the host talking, while
    one poking into the host's strip suppresses it.  The preview has no
    measured narration, so it applies the rule to the whole scene -- the
    placement question a preview can actually answer.
    """
    host_x = float(frame.get("host_x", STORY_FRAME_HOST_X))
    host_left = host_x - STORY_HOST_HALF_SPAN
    host_right = host_x + STORY_HOST_HALF_SPAN

    for character in characters:
        centre = float(character.get("x", 0.5) or 0.5)
        if centre + CUE_HALF_SPAN > host_left and centre - CUE_HALF_SPAN < host_right:
            return True
    return False


def _composite_narrator(
    canvas: Image.Image,
    sprite: Image.Image,
    frame: dict,
    *,
    width: int,
    height: int,
) -> None:
    """
    Place the narrator on the strip to the right of the panel.

    `host_x` is a frame fraction measured like every other cue, so a frame
    that reaches too far right leaves the host no clear strip and it is
    skipped rather than drawn on top of the picture.
    """
    host_x = float(frame.get("host_x", STORY_FRAME_HOST_X))
    host_y = float(frame.get("host_y", STORY_FRAME_HOST_Y))
    host_height = float(frame.get("host_height", STORY_FRAME_HOST_HEIGHT))

    target_height = max(1, round(host_height * height))
    scale = target_height / max(1, sprite.height)
    target_width = max(1, round(sprite.width * scale))
    working = sprite.resize((target_width, target_height), Image.LANCZOS)
    if bool(frame.get("host_flip", False)):
        working = working.transpose(Image.FLIP_LEFT_RIGHT)

    left = int(round(host_x * width - target_width / 2))
    top = int(round(host_y * height - target_height))
    if left < 0 or left + target_width > width:
        print(
            f"WARN story_frame: host_x={host_x} puts the narrator off the "
            "frame edge; nudge it inward or shrink host_height",
            file=sys.stderr,
        )
    canvas.alpha_composite(working, (max(0, left), max(0, top)))


def render_frames(
    script_path: Path,
    scene_ids: list[int],
    *,
    fps: int,
    width: int,
    height: int,
    seconds_per_scene: float = 2.5,
) -> list[Image.Image]:
    """Render the preview frames, in scene order."""
    script = json.loads(script_path.read_text(encoding="utf-8"))
    _load_registry()
    scenes = {
        scene["id"]: scene
        for scene in script.get("scenes", [])
        if isinstance(scene, dict) and "id" in scene
    }

    frames: list[Image.Image] = []
    per_scene = max(1, round(seconds_per_scene * fps))

    for scene_id in scene_ids:
        scene = scenes.get(scene_id)
        if scene is None:
            print(f"SKIP scene {scene_id}: not in script", file=sys.stderr)
            continue

        artwork_path = scene.get("image_file")
        if not artwork_path:
            print(f"SKIP scene {scene_id}: no image_file", file=sys.stderr)
            continue
        artwork = Image.open(_resolve(artwork_path)).convert("RGBA")

        sprites = []
        for character in scene.get("characters", []):
            sprite_path = character.get("image_file")
            if not sprite_path:
                continue
            try:
                sprite = Image.open(_resolve(sprite_path)).convert("RGBA")
            except OSError as error:
                print(
                    f"WARN scene {scene_id}: sprite {sprite_path}: {error}",
                    file=sys.stderr,
                )
                continue
            if bool(character.get("flip", False)):
                sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
            sprites.append((character, sprite))

        frame = _frame_for_scene(script, scene)
        box = _panel_box(frame, width, height) if frame else None
        window = _art_window(frame, box) if frame else None
        panel = _frame_panel(frame, box) if frame else None
        narrator = _narrator_sprite(frame) if frame else None
        narrator_hidden = (
            _narrator_hidden(frame, scene.get("characters", []))
            if (frame and narrator is not None)
            else False
        )

        scene_seconds = per_scene / fps
        for index in range(per_scene):
            seconds = index / fps
            progress = index / max(1, per_scene - 1)
            scale = _ken_burns_scale(scene, progress)

            # With a frame the picture lives only in the panel's window;
            # without one it fills the canvas.
            if window is not None:
                _, _, art_w, art_h = window
            else:
                art_w, art_h = width, height

            if abs(scale - 1.0) < 1e-3:
                zoomed = _crop_to_ratio(artwork, art_w, art_h)
            else:
                scaled_w = max(art_w, round(artwork.width * scale))
                scaled_h = max(art_h, round(artwork.height * scale))
                big = artwork.resize((scaled_w, scaled_h), Image.LANCZOS)
                left = (scaled_w - art_w) // 2
                top = (scaled_h - art_h) // 2
                zoomed = big.crop((left, top, left + art_w, top + art_h))

            if window is not None:
                # Layer order matches the filtergraph: the panel is the mat
                # and is opaque everywhere including its window, so the
                # picture has to go on top of it -- compositing the panel
                # last would bury the artwork and the cast behind it.
                canvas = Image.new(
                    "RGBA", (width, height), STORY_FRAME_BACKDROP_RGB + (255,)
                )
                if panel is not None:
                    canvas.alpha_composite(panel, box[:2])
                canvas.alpha_composite(zoomed.convert("RGBA"), window[:2])
                for character, sprite in sprites:
                    _composite(
                        canvas,
                        sprite,
                        character,
                        seconds=seconds,
                        scene_seconds=scene_seconds,
                        frame_width=width,
                        frame_height=height,
                    )
                if narrator is not None and not narrator_hidden:
                    _composite_narrator(
                        canvas, narrator, frame, width=width, height=height
                    )
            else:
                canvas = zoomed.convert("RGBA")
                for character, sprite in sprites:
                    _composite(
                        canvas,
                        sprite,
                        character,
                        seconds=seconds,
                        scene_seconds=scene_seconds,
                        frame_width=width,
                        frame_height=height,
                    )

            frames.append(canvas.convert("RGB"))

    return frames


def _write_gif(frames: list[Image.Image], out: Path, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=int(round(1000 / fps)),
        loop=0,
        optimize=True,
    )


def _write_mjpeg(frames: list[Image.Image], out: Path, quality: int) -> None:
    payloads = []
    for frame in frames:
        buffer = io.BytesIO()
        frame.save(buffer, format="JPEG", quality=quality)
        payloads.append(buffer.getvalue())
    out.parent.mkdir(parents=True, exist_ok=True)
    write_mjpeg_movie(
        out, payloads, width=frames[0].width, height=frames[0].height
    )


def _ffmpeg_exe() -> str | None:
    """
    Locate an ffmpeg this machine can actually run.

    The repository bundles a Linux ELF build, so `bin/ffmpeg` is useless on
    macOS; imageio-ffmpeg ships a native binary and is already a dependency.
    """
    try:
        import imageio_ffmpeg
    except ImportError:
        pass
    else:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    return shutil.which("ffmpeg")


def _write_h264(
    frames: list[Image.Image], out: Path, fps: int, crf: int
) -> bool:
    """
    Encode to H.264, which every player (including VS Code's Chromium
    preview and QuickTime) decodes.  Returns False if no ffmpeg was found.
    """
    exe = _ffmpeg_exe()
    if exe is None:
        return False

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="autovid_preview_") as staging:
        staging_dir = Path(staging)
        for index, frame in enumerate(frames):
            frame.save(staging_dir / f"{index:05d}.jpg", format="JPEG", quality=95)
        subprocess.run(
            [
                exe, "-y", "-framerate", str(fps),
                "-i", str(staging_dir / "%05d.jpg"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", str(crf), str(out),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview a script's character staging as an animated GIF or an "
            "MJPEG mp4 -- no ffmpeg needed."
        )
    )
    parser.add_argument("script", type=Path, help="script.json to preview")
    parser.add_argument("out", type=Path, help="output .gif, .mp4 or .mov path")
    parser.add_argument(
        "--scenes",
        nargs="+",
        type=int,
        default=[1, 2],
        help="scene ids to preview (default: the first two with a cast)",
    )
    parser.add_argument("--fps", type=int, default=12, help="frames per second")
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, help="preview width in pixels"
    )
    parser.add_argument(
        "--height", type=int, default=DEFAULT_HEIGHT, help="preview height"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=2.5,
        help="seconds to spend on each scene",
    )
    parser.add_argument(
        "--quality", type=int, default=82, help="JPEG quality for the MJPEG fallback"
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=23,
        help="H.264 quality: lower is better (18 near-lossless, 28 small)",
    )
    args = parser.parse_args()

    frames = render_frames(
        args.script,
        args.scenes,
        fps=args.fps,
        width=args.width,
        height=args.height,
        seconds_per_scene=args.seconds,
    )
    if not frames:
        raise SystemExit("nothing to render")

    container = args.out.suffix.lower()
    if container in (".mp4", ".mov"):
        try:
            encoded = _write_h264(frames, args.out, args.fps, args.crf)
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode("utf-8", "replace").strip()
            raise SystemExit(f"ffmpeg failed to encode {args.out}:\n{detail}")
        if not encoded:
            print(
                "note: no native ffmpeg found, writing MJPEG instead -- "
                "play it in QuickTime, not in VS Code's preview"
            )
            _write_mjpeg(frames, args.out, args.quality)
    else:
        _write_gif(frames, args.out, args.fps)

    size_mb = args.out.stat().st_size / 1024 / 1024
    print(
        f"WROTE {args.out}  ({len(frames)} frames, "
        f"{frames[0].width}x{frames[0].height}, {size_mb:.1f} MB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())