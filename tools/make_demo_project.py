"""
Build a fake autovid workspace so the pipeline can be tested end to end
without real artwork, a real script or a real TTS run.

    python tools/make_demo_project.py                 # 48 scenes
    python tools/make_demo_project.py --scenes 8      # quick smoke test
    python tools/make_demo_project.py --out /tmp/demo --force

Everything it writes is deliberately worthless as content:

* `script.json`  — 48 scenes whose text is assembled from a pool of
  Vietnamese filler sentences so the *length* is realistic (~13 minutes
  total, which is what stage 1 measures).  It is not a real script.
* `images/`      — flat doodle-style PNGs at the target resolution, with
  off-centre shapes so Ken Burns movement is actually visible.
* `assets/`      — silent BGM, three short SFX and one font copied from
  the system font directories.
* `audio/`       — per-scene silence sized like real narration, so later
  stages have something to concatenate.

To exercise validation failures on purpose, pass `--break-it` to omit one
image, reference a missing SFX and add an out-of-range section break.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovid.infrastructure.ffmpeg import FFMPEG, run  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "tmp" / "autovid_demo"

WIDTH = 1920
HEIGHT = 1080
FPS = 30
VOICE = "Thái Sơn"
CHARS_PER_SECOND = 14.0

# Filler sentences, roughly the length of real explainer narration.
SENTENCE_POOL = [
    "Quả là gì? Nghe thì dễ, nhưng đến lúc bị hỏi thì chín phần mười người lớn đều đứng hình.",
    "Hồi cấp một, cô giáo từng nói với tôi rằng tiếng Việt là một trò chơi chữ, và tôi đã cười vì tưởng cô đùa.",
    "Hôm nay chúng ta sẽ thử đi tìm câu trả lời cho một câu hỏi tưởng như rất ngớ ngẩn nhưng lại tốn giấy mực của không ít học trò.",
    "Bạn cứ thử nghĩ mà xem, mỗi chữ trong tiếng Việt đều có một lớp nghĩa riêng, chỉ chờ đúng ngữ cảnh để bung ra.",
    "Tất nhiên là không phải chữ nào cũng thú vị như nhau, nhưng có những chữ mà khi tách ra thì cả câu chuyện đổi chiều hoàn toàn.",
    "Điều đáng nói là cách chúng ta dùng chữ trong đời sống hàng ngày lại chẳng hề giống trong sách vở chút nào.",
    "Có người học cả đời văn mà vẫn không ngờ rằng trò chơi chữ này lại có quy luật rõ ràng đến vậy.",
    "Thế nhưng, càng đi sâu thì càng thấy mọi thứ không đơn giản như vẻ bề ngoài của nó.",
    "Người xưa chơi chữ một cách rất có ý tứ, mỗi câu đối, mỗi vế ca dao đều ngầm chứa một tầng nghĩa khác.",
    "Vậy nên chuyện hôm nay kể cũng chẳng phải chuyện gì to tát, chỉ là một lần tò mò rồi bị cuốn theo đến tận cùng.",
    "Nhưng khoan đã, trước khi đi tiếp thì cần nói rõ ranh giới giữa hai thứ rất dễ bị lẫn vào nhau.",
    "Tôi đã thử hỏi mười người bất kỳ trên đường, và kết quả khiến tôi phải nghĩ lại về rất nhiều thứ đã học.",
    "Thế thì tại sao một chuyện đơn giản như vậy lại khiến người ta bối rối đến thế?",
    "Câu trả lời nằm ở chỗ chúng ta thường học chữ theo kiểu nhớ máy móc mà quên mất ý nghĩa gốc ban đầu.",
    "Nếu nhìn kỹ một chút, bạn sẽ thấy ở đó có một quy luật, và quy luật ấy lặp lại ở nhiều nơi khác nữa.",
    "Đến đây thì mọi chuyện bắt đầu rõ ràng hơn, và cũng bắt đầu thú vị hơn rất nhiều.",
    "Tuy nhiên, vẫn còn một chi tiết nằm ở giữa mà chúng ta đã đi ngang qua quá nhanh.",
    "Chi tiết đó nhỏ đến mức tôi đã bỏ qua nó hai lần trước khi nhận ra nó chính là chìa khoá của cả câu chuyện.",
    "Khi bạn đã thấy nó rồi thì rất khó để không thấy nó ở mọi chỗ, và đó chính là lúc trò chơi bắt đầu.",
    "Tóm lại, chữ Việt thú vị ở chỗ nó cho phép người nói vừa nói vừa chơi, và người nghe thì cứ tưởng mình đang được nghe chuyện.",
    "Hãy nhớ rằng mọi câu hỏi tưởng chừng ngớ ngẩn đều có thể dẫn tới một câu trả lời đáng để suy nghĩ.",
    "Và lần này cũng vậy, câu trả lời không nằm ở cuối con đường mà nằm ngay chỗ chúng ta bắt đầu.",
]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf",
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

SHAPES = [
    ("circle", "#E4572E"),
    ("square", "#3D5A80"),
    ("triangle", "#5B8C5A"),
    ("circle", "#B23A48"),
    ("square", "#6D597A"),
]


# --------------------------------------------------------------------------
# script.json
# --------------------------------------------------------------------------


def build_scene(index: int) -> dict:
    """One fake scene with the shape of a real one."""
    scene_id = index + 1

    first = SENTENCE_POOL[(index * 2) % len(SENTENCE_POOL)]
    second = SENTENCE_POOL[(index * 2 + 1) % len(SENTENCE_POOL)]
    text = f"{first} {second}"

    ken_burns_types = [
        ("zoom_in", 1.0, 1.08),
        ("pan_right", 1.10, 1.10),
        ("zoom_out", 1.12, 1.00),
        ("pan_left", 1.10, 1.10),
    ]
    motion_type, start_scale, end_scale = ken_burns_types[index % 4]

    overlays = []
    # A title overlay on the first scene, then one every section.
    if scene_id == 1 or (scene_id - 1) % 12 == 0:
        overlays.append(
            {
                "text": f"PHẦN {((scene_id - 1) // 12) + 1}",
                "font": "assets/fonts/handwriting.ttf",
                "font_size": 96,
                "color": "#FF5733",
                "stroke_color": "#000000",
                "stroke_width": 4,
                "position": "center",
                "start_offset_ms": 400,
                "end_offset_ms": 3600,
                "animation": "fade_in",
                "animation_duration_ms": 400,
            }
        )
    elif index % 3 == 1:
        overlays.append(
            {
                "text": "CHÚ Ý",
                "font": "assets/fonts/handwriting.ttf",
                "font_size": 72,
                "color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": 3,
                "position": "bottom",
                "start_offset_ms": 500,
                "end_offset_ms": 3000,
                "animation": "pop",
                "animation_duration_ms": 300,
            }
        )

    sfx = []
    if index % 4 == 0:
        sfx.append(
            {"file": "assets/sfx/whoosh.mp3", "time_offset_ms": 0, "volume": 0.8}
        )
    if index % 12 == 6:
        # Two SFX in one scene, spaced past the 5s density floor.
        sfx = [
            {"file": "assets/sfx/boom.mp3", "time_offset_ms": 0, "volume": 0.75},
            {"file": "assets/sfx/pop.mp3", "time_offset_ms": 8000, "volume": 0.6},
        ]

    transition = (
        {"type": "fade", "duration": 0.4}
        if index % 12 == 0 and index > 0
        else {"type": "cut", "duration": 0.0}
    )

    return {
        "id": scene_id,
        "text": text,
        "image_prompt": f"Doodle explainer illustration for scene {scene_id}, "
        "flat colours, white background, 16:9.",
        "image_file": f"images/scene_{scene_id:03d}.png",
        "pause_after_ms": None,
        "transition_in": transition,
        "ken_burns": {
            "enabled": True,
            "type": motion_type,
            "start_scale": start_scale,
            "end_scale": end_scale,
        },
        "text_overlays": overlays,
        "sfx": sfx,
    }


def build_script(total: int, *, break_it: bool) -> dict:
    scenes = [build_scene(index) for index in range(total)]

    if break_it and total > 4:
        # Deliberate failures so the validator's error paths are visible.
        scenes[2].pop("image_file", None)
        scenes[3]["sfx"] = [
            {"file": "assets/sfx/does_not_exist.mp3", "time_offset_ms": 0, "volume": 0.5}
        ]

    scene_count = len(scenes)
    section_breaks = [b for b in (13, 25, 37) if b < scene_count]
    if break_it:
        section_breaks.append(scene_count + 5)

    return {
        "video_metadata": {
            "title": "Quả là gì?",
            "author": "autovid demo",
            "resolution": f"{WIDTH}x{HEIGHT}",
            "fps": FPS,
            "language": "vi",
        },
        "tts_config": {
            "engine": "vieneu",
            "voice": VOICE,
            "speed": 1.0,
            "pitch": 0.0,
            "emotion": "neutral",
            "granularity": "sentence",
            "max_chars_per_chunk": 1000,
        },
        "audio_config": {
            "background_music": "assets/audio/bg.mp3",
            "background_volume": 0.12,
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
            "custom_pauses": {"6": 2500} if total > 6 else {},
        },
        "scenes": scenes,
    }


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------


def find_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    for directory in FONT_SEARCH_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in ("**/*.ttf", "**/*.otf"):
            for found in sorted(base.glob(pattern)):
                return found
    return None


def make_image(path: Path, index: int) -> None:
    from PIL import Image, ImageDraw

    shape, colour = SHAPES[index % len(SHAPES)]
    image = Image.new("RGB", (WIDTH, HEIGHT), "#FFFFFF")
    draw = ImageDraw.Draw(image)

    # Off-centre subject plus an asymmetric corner detail, so a Ken Burns
    # pan or zoom produces visible movement.
    cx = 420 + (index * 137) % (WIDTH - 840)
    cy = 320 + (index * 91) % (HEIGHT - 640)
    radius = 150 + (index * 17) % 120

    if shape == "circle":
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=colour,
            outline="#1B1B1B",
            width=6,
        )
    elif shape == "square":
        draw.rectangle(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=colour,
            outline="#1B1B1B",
            width=6,
        )
    else:
        draw.polygon(
            [(cx, cy - radius), (cx - radius, cy + radius), (cx + radius, cy + radius)],
            fill=colour,
            outline="#1B1B1B",
        )

    stem_x = cx + radius // 3
    draw.line(
        [(stem_x, cy - radius), (stem_x + 40, cy - radius - 70)],
        fill="#3F6B3A",
        width=12,
    )
    draw.rectangle((60, HEIGHT - 90, 60 + 40 + index * 3, HEIGHT - 50), fill="#1B1B1B")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def make_audio(out: Path, *, duration: float, args: list[str]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-loglevel", "error", "-t", f"{duration:.3f}", *args, str(out)])


def make_bgm(path: Path) -> None:
    make_audio(
        path,
        duration=60.0,
        args=[
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=196:sample_rate=48000",
            "-af",
            "volume=0.10,afade=t=in:d=2,afade=t=out:st=58:d=2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
        ],
    )


def make_sfx(path: Path, *, frequency: int, duration: float) -> None:
    make_audio(
        path,
        duration=duration,
        args=[
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
            "-af",
            f"volume=0.5,afade=t=out:st={max(duration - 0.15, 0):.2f}:d=0.15",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
        ],
    )


def make_scene_audio(path: Path, seconds: float) -> None:
    """Silence sized like real narration, so later stages have input."""
    make_audio(
        path,
        duration=seconds,
        args=[
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-c:a",
            "pcm_s16le",
        ],
    )


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"workspace to create (default: {DEFAULT_OUT})",
    )
    parser.add_argument("--scenes", type=int, default=48, help="scene count")
    parser.add_argument(
        "--force", action="store_true", help="delete an existing workspace first"
    )
    parser.add_argument(
        "--break-it",
        action="store_true",
        help="introduce deliberate errors to exercise validator failure paths",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="skip the per-scene silence files",
    )
    args = parser.parse_args()

    if FFMPEG is None:
        print("ERROR: ffmpeg not found (expected bin/ffmpeg or PATH)", file=sys.stderr)
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
    out.mkdir(parents=True)

    script = build_script(args.scenes, break_it=args.break_it)
    (out / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"script.json            {len(script['scenes'])} scenes")

    font = find_font()
    if font is None:
        print("WARNING: no system font found; text overlays cannot be rendered")
    else:
        target = out / "assets" / "fonts" / "handwriting.ttf"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(font, target)
        print(f"assets/fonts/          copied {font.name}")

    make_bgm(out / "assets" / "audio" / "bg.mp3")
    make_sfx(out / "assets" / "sfx" / "whoosh.mp3", frequency=900, duration=0.5)
    make_sfx(out / "assets" / "sfx" / "boom.mp3", frequency=64, duration=1.4)
    make_sfx(out / "assets" / "sfx" / "pop.mp3", frequency=1400, duration=0.2)
    print("assets/audio + sfx/    4 files")

    for scene in script["scenes"]:
        image_file = scene.get("image_file")
        if image_file:
            make_image(out / image_file, scene["id"])
    image_count = sum(1 for scene in script["scenes"] if scene.get("image_file"))
    print(f"images/                {image_count} PNGs at {WIDTH}x{HEIGHT}")

    if not args.no_audio:
        total_seconds = 0.0
        for scene in script["scenes"]:
            seconds = len(scene["text"]) / CHARS_PER_SECOND
            total_seconds += seconds
            make_scene_audio(
                out / "audio" / f"scene_{scene['id']:03d}.wav", seconds
            )
        print(
            f"audio/                 {len(script['scenes'])} silence files "
            f"({total_seconds:.0f}s total)"
        )

    print(f"\nworkspace ready: {out}")
    print(f"next: python autovid.py validate {out / 'script.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
