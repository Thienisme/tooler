"""
Turn a script.json into the shot list the artwork has to answer.

    python tools/make_image_briefs.py projects/<id>/script.json
    python tools/make_image_briefs.py projects/<id>/script.json --rate 18.3

Writes `image_briefs.md` next to the script: one row per scene with the
narration it has to illustrate, how long that narration lasts, which file the
image must be, and an empty *Brief* column to fill in before drawing or
generating anything.

It also audits the artwork that is already there, because the two mistakes
this step makes are expensive: an image smaller than the frame (which stage 3
silently upscales, and which looks soft in the final video) and the same
image reused across too many scenes (which makes Ken Burns look broken even
when it is working).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autovid.domain.script import ScriptSchemaError, load_script  # noqa: E402
from autovid.paths import Paths, resolve_asset  # noqa: E402

# Measured on the production voice (Minh Quân Pro @ 0.92): ~18.3 characters
# per second of narration.  Only used to size scenes here; stage 2 measures
# the real thing.
DEFAULT_CHARS_PER_SECOND = 18.3

# Above this many scenes sharing one image, the repetition is visible.
REUSE_LIMIT = 3


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as handle:
            return handle.size
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("script", help="path to script.json")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the briefs (default: <workspace>/image_briefs.md)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_CHARS_PER_SECOND,
        help=f"narration speed in characters/second (default {DEFAULT_CHARS_PER_SECOND})",
    )
    args = parser.parse_args()

    script_path = Path(args.script).resolve()
    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    paths = Paths.from_workspace(script_path.parent)
    out = args.out or (paths.workspace / "image_briefs.md")
    metadata = script.video_metadata

    # -- per scene --------------------------------------------------------
    rows: list[dict] = []
    usage: Counter[str] = Counter()

    for scene in script.scenes:
        if scene.image_file:
            usage[scene.image_file] += 1
        seconds = len(scene.text) / max(args.rate, 1.0)
        rows.append(
            {
                "id": scene.id,
                "reference": scene.image_file,
                "prompt": scene.image_prompt,
                "chars": len(scene.text),
                "seconds": seconds,
                "excerpt": " ".join(scene.text.split())[:150],
            }
        )

    # -- artwork audit ----------------------------------------------------
    inventory: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        reference = row["reference"]
        if reference is None or reference in seen:
            continue
        seen.add(reference)

        resolved = resolve_asset(reference, paths.workspace)
        size = image_size(resolved) if resolved is not None else None
        upscaled = bool(
            size and (size[0] < metadata.width or size[1] < metadata.height)
        )
        inventory.append(
            {
                "reference": reference,
                "exists": resolved is not None,
                "size": size,
                "upscaled": upscaled,
                "scenes": usage[reference],
                "resolved": resolved,
            }
        )

    missing = [entry for entry in inventory if not entry["exists"]]
    upscaled = [entry for entry in inventory if entry["upscaled"]]
    reused = [entry for entry in inventory if entry["scenes"] > REUSE_LIMIT]
    prompt_only = [row for row in rows if row["reference"] is None]

    lines: list[str] = []
    lines.append(f"# Shot list — {metadata.title}")
    lines.append("")
    lines.append(
        f"Frame: {metadata.resolution} @ {metadata.fps}fps · "
        f"{len(script.scenes)} scene · ước tính {sum(r['seconds'] for r in rows) / 60:.1f} phút "
        f"(ở {args.rate:g} ký tự/s)"
    )
    lines.append("")
    lines.append(
        f"Ảnh cần: **{len(inventory)} file** "
        f"({len(missing)} chưa có) — mỗi ảnh phục vụ "
        f"{len(rows) / max(len(inventory), 1):.1f} scene."
    )
    lines.append("")
    lines.append("## Cần tạo / cần bổ sung")
    lines.append("")
    if missing:
        for entry in missing:
            lines.append(f"- [ ] `{entry['reference']}` — thiếu, dùng cho {entry['scenes']} scene")
    else:
        lines.append("- [ ] (không thiếu file nào)")
    for entry in upscaled:
        width, height = entry["size"]
        lines.append(
            f"- [ ] `{entry['reference']}` — {width}x{height} nhỏ hơn khung "
            f"{metadata.resolution}, sẽ bị upscale và mờ; nên làm lại ≥"
            f"{metadata.width}x{metadata.height}"
        )
    for entry in reused:
        lines.append(
            f"- [ ] `{entry['reference']}` — dùng cho {entry['scenes']} scene, "
            f"nhiều hơn {REUSE_LIMIT}, nên vẽ thêm ảnh khác"
        )
    for row in prompt_only:
        prompt = (row["prompt"] or "").strip()
        detail = f" — prompt: {prompt[:80]}" if prompt else ""
        lines.append(
            f"- [ ] scene {row['id']}: chỉ có `image_prompt`, chưa có ảnh thật{detail}"
        )
    lines.append("")

    lines.append("## Yêu cầu kỹ thuật cho mọi ảnh")
    lines.append("")
    lines.append(
        f"- Tỉ lệ 16:9, tối thiểu {metadata.width}x{metadata.height} "
        "(1920x1080 là an toàn; 2560x1440 nếu muốn zoom sâu)"
    )
    lines.append(
        "- Chừa lề an toàn ~10% quanh mép: Ken Burns zoom/pan 1.08–1.14 nên sẽ cắt bớt mép"
    )
    lines.append(
        "- Không để chữ quan trọng trong ảnh (chữ trên video là overlay riêng, tránh trùng)"
    )
    lines.append(
        "- Cùng một phong cách và tông màu cho cả video; đủ tương phản để chữ overlay đọc được"
    )
    lines.append(
        "- Đặt tên theo số thứ tự (01.jpg, 02.jpg…) để dùng lại cho lần sau dễ"
    )
    lines.append("")

    lines.append("## Danh sách ảnh")
    lines.append("")
    lines.append("| File | Trạng thái | Kích thước | Scene | Brief (điền vào đây) |")
    lines.append("|---|---|---|---|---|")
    for entry in inventory:
        size = entry["size"]
        size_text = f"{size[0]}x{size[1]}" if size else "?"
        if not entry["exists"]:
            status = "THIẾU"
        elif entry["upscaled"]:
            status = "nhỏ hơn khung"
        else:
            status = "ok"
        scenes = ", ".join(
            str(row["id"]) for row in rows if row["reference"] == entry["reference"]
        )
        lines.append(
            f"| `{Path(entry['reference']).name}` | {status} | {size_text} | "
            f"{scenes} |  |"
        )
    lines.append("")

    lines.append("## Nội dung từng scene")
    lines.append("")
    lines.append("| # | Dài | Ảnh | Lời kể (để vẽ cho khớp) |")
    lines.append("|---|---|---|---|")
    for row in rows:
        image = Path(row["reference"]).name if row["reference"] else "(prompt)"
        lines.append(
            f"| {row['id']} | {row['seconds']:.1f}s | `{image}` | {row['excerpt']}… |"
        )
    lines.append("")

    lines.append("## Quy trình")
    lines.append("")
    lines.append("1. Điền cột **Brief** trên cho từng ảnh (vẽ/generate gì, có nhân vật nào).")
    lines.append("2. Tạo ảnh theo brief, lưu đúng tên file trong bảng.")
    lines.append("3. Nếu ảnh có watermark AI: chèn logo bằng `apply_logo.py` (xem README).")
    lines.append(f"4. Kiểm lại: `python autovid.py images {script_path}`")
    lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"shot list  : {out}")
    print(f"scenes     : {len(rows)} ({sum(r['seconds'] for r in rows) / 60:.1f} min @ {args.rate:g} chars/s)")
    print(f"images     : {len(inventory)} needed, {len(missing)} missing, "
          f"{len(upscaled)} smaller than the frame, {len(reused)} reused too often")
    if missing:
        print("missing    :")
        for entry in missing[:10]:
            print(f"   - {entry['reference']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
