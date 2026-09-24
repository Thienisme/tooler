#!/usr/bin/env python3
"""
Tạo ảnh tự động cho autovid từ script.json.

Đọc ``script.json``, sinh prompt 2D cartoon cho từng scene (theo style mẫu
bạn cung cấp), gọi Gemini image API rồi lưu đúng vào ``image_file`` của từng
scene để pipeline ``images → assembly → …`` chạy tiếp bình thường.

Ví dụ:

    # Xem trước prompt (không gọi API, không tạo ảnh)
    python tools/generate_images.py projects/topics-001/script.json --dry-run

    # Chỉ in prompt ra file để review / dán tay vào web
    python tools/generate_images.py projects/topics-001/script.json --prompts-only

    # Tạo ảnh, bỏ qua ảnh đã có sẵn (mặc định)
    python tools/generate_images.py projects/topics-001/script.json

    # Tạo lại TẤT CẢ ảnh, dùng ảnh mẫu để giữ style
    python tools/generate_images.py projects/topics-001/script.json \\
        --force --ref-image projects/topics-001/style_ref.png

    # Style riêng + mô tả nhân vật + tạo cho 1 workspace shorts
    python tools/generate_images.py projects/x/shorts/short-01/script.json \\
        --style-file notes/autovid-image-style.md \\
        --chars "Chí Phèo: đầu trọc, xăm trổ"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_mystery_story.infrastructure.image.gemini_image_generator import (  # noqa: E402
    DEFAULT_IMAGE_MODEL,
    DEFAULT_SCENE_STYLE,
    GeminiImageGenerator,
    build_scene_prompt,
)

MAX_PROMPT_CHARS = 2800  # prompt dài quá dễ bị API chối / pha loãng style
REF_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def load_env(path: Path) -> None:
    """Nạp .env tối giản (KEY=VALUE) — không cần python-dotenv."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------------- io

def load_script(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def script_aspect(script: dict) -> str:
    resolution = (script.get("video_metadata") or {}).get("resolution", "1920x1080")
    try:
        w, h = (int(x) for x in str(resolution).lower().split("x"))
    except ValueError:
        return "16:9"
    return "9:16" if h > w else "16:9"


def scene_output_path(script_path: Path, scene: dict) -> Path | None:
    image_file = scene.get("image_file")
    if not image_file:
        return None
    p = Path(image_file)
    if p.is_absolute():
        return p
    # Đường dẫn trong script tính từ workspace (thư mục chứa script.json),
    # nhưng vẫn chấp nhận đường dẫn tính từ repo root nếu nó tồn tại sẵn.
    candidate = script_path.parent / p
    if candidate.exists():
        return candidate
    repo_candidate = REPO_ROOT / p
    if repo_candidate.exists():
        return repo_candidate
    return candidate  # mặc định tính từ workspace


def load_style(args: argparse.Namespace) -> str:
    if args.style_file:
        return Path(args.style_file).read_text(encoding="utf-8").strip()
    if args.style:
        return args.style.strip()
    return DEFAULT_SCENE_STYLE


def collect_ref_images(args: argparse.Namespace) -> list[Path]:
    if not args.ref_image:
        return []
    rp = Path(args.ref_image)
    if rp.is_dir():
        files = sorted(f for f in rp.iterdir() if f.suffix.lower() in REF_SUFFIXES)
        refs = files[:3]
    else:
        refs = [rp]
    return [r if r.is_absolute() else r.resolve() for r in refs]


def parse_only(spec: str) -> set[int]:
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return wanted


# ------------------------------------------------------------------ logic

def build_entries(args: argparse.Namespace, script: dict) -> list[dict]:
    script_path = Path(args.script)
    style = load_style(args)
    aspect = script_aspect(script)

    extra_parts: list[str] = []
    if args.chars:
        extra_parts.append(
            "Recurring characters in this story (keep their look consistent in "
            f"every scene where they appear): {args.chars}"
        )
    title = (script.get("video_metadata") or {}).get("title")
    if title:
        extra_parts.append(f"Story title (context only, DO NOT render this text): {title}")

    entries: list[dict] = []
    for scene in script.get("scenes", []):
        text = (scene.get("text") or "").strip()
        out = scene_output_path(script_path, scene)
        if out is None:
            print(f"⚠️ Scene {scene.get('id')}: không có image_file → bỏ qua", file=sys.stderr)
            continue

        prompt = build_scene_prompt(
            scene_text=text,
            style=style,
            aspect_ratio=aspect,
            extra_instructions="; ".join(extra_parts) or None,
        )
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:MAX_PROMPT_CHARS]

        entries.append({"scene": scene, "out": out, "prompt": prompt})

    return entries


def write_prompt_pack(entries: list[dict], script_path: Path, model: str) -> Path:
    out_md = script_path.parent / "image_prompts.md"
    lines = [
        "# Image prompts (auto-generated)",
        "",
        f"- Script: `{script_path}`",
        f"- Model: `{model}`",
        "",
    ]
    for e in entries:
        lines += [
            f"## Scene {e['scene'].get('id')} → `{e['out'].name}`",
            "",
            "```text",
            e["prompt"],
            "```",
            "",
        ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


# ------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    load_env(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Tạo ảnh tự động cho autovid từ script.json (Gemini image API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("script", help="Đường dẫn tới script.json")
    parser.add_argument("--style", help="Style prompt thay cho style mặc định (có thể chứa {text})")
    parser.add_argument("--style-file", help="File chứa style prompt (vd notes/autovid-image-style.md)")
    parser.add_argument(
        "--ref-image",
        help="Ảnh mẫu (1 file hoặc thư mục, tối đa 3 ảnh) để giữ style/nhân vật",
    )
    parser.add_argument(
        "--chars",
        help='Mô tả nhân vật lặp lại, vd: "Chí Phèo: đầu trọc, xăm trổ; Bá Kiến: bụng phệ"',
    )
    parser.add_argument("--model", default=DEFAULT_IMAGE_MODEL, help=f"Mặc định: {DEFAULT_IMAGE_MODEL}")
    parser.add_argument("--force", action="store_true", help="Tạo lại cả ảnh đã có sẵn")
    parser.add_argument("--only", help="Chỉ tạo các scene này, vd: 1,5,7-9")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in prompt ra màn hình")
    parser.add_argument("--prompts-only", action="store_true", help="Ghi image_prompts.md rồi thoát")
    parser.add_argument("--sleep", type=float, default=3.0, help="Nghỉ giữa 2 ảnh (giây)")
    args = parser.parse_args(argv)

    script_path = Path(args.script)
    if not script_path.is_file():
        parser.error(f"Không tìm thấy {script_path}")

    script = load_script(script_path)
    entries = build_entries(args, script)
    if not entries:
        print("Không có scene nào để xử lý.")
        return 1

    if args.only:
        wanted = parse_only(args.only)
        entries = [e for e in entries if int(e["scene"].get("id", 0)) in wanted]
        if not entries:
            print("Không scene nào khớp --only.")
            return 1

    if args.dry_run:
        print(f"== DRY RUN — {len(entries)} prompt, model {args.model} ==\n")
        for e in entries:
            print(f"--- Scene {e['scene'].get('id')} → {e['out']} ---")
            print(e["prompt"])
            print()
        return 0

    if args.prompts_only:
        out_md = write_prompt_pack(entries, script_path, args.model)
        print(f"✅ Đã ghi {out_md} ({len(entries)} prompt)")
        return 0

    refs = collect_ref_images(args)
    for ref in refs:
        if not ref.is_file():
            print(f"❌ Ảnh mẫu không tồn tại: {ref}", file=sys.stderr)
            return 2
    if refs:
        print(f"Ảnh mẫu: {', '.join(r.name for r in refs)}")

    try:
        generator = GeminiImageGenerator(model=args.model)
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    total = len(entries)
    ok = skipped = failed = 0
    for i, e in enumerate(entries, 1):
        out: Path = e["out"]
        sid = e["scene"].get("id", i)
        if out.exists() and not args.force:
            print(f"[{i}/{total}] Scene {sid}: đã có {out.name} → bỏ qua (--force để tạo lại)")
            skipped += 1
            continue

        print(f"[{i}/{total}] Scene {sid}: tạo {out.name} ...")
        try:
            generator.generate(
                prompt=e["prompt"],
                output_path=out,
                aspect_ratio=script_aspect(script),
                reference_images=refs,
            )
            print(f"  ✅ Đã lưu {out}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ Thất bại: {exc}", file=sys.stderr)
            failed += 1

        if i < total and args.sleep > 0:
            time.sleep(args.sleep)

    print(f"\nKết quả: {ok} tạo mới, {skipped} bỏ qua, {failed} lỗi / {total} scene.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
