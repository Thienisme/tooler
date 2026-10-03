"""
Turn a script.json into the shot list the artwork has to answer.

    python tools/make_image_briefs.py projects/<id>/script.json
    python tools/make_image_briefs.py projects/<id>/script.json --rate 18.3

Writes `image_briefs.md` and `image_contact_sheet.html` next to the script:
the Markdown shot list is editable, while the HTML page shows every scene's
artwork beside its narration and flags missing, upscaled or heavily reused
images for a quick visual review.

It also audits the artwork that is already there, because the two mistakes
this step makes are expensive: an image smaller than the frame (which stage 3
silently upscales, and which looks soft in the final video) and the same
image reused across too many scenes (which makes Ken Burns look broken even
when it is working).
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

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


def write_contact_sheet(
    rows: list[dict],
    inventory: list[dict],
    *,
    title: str,
    resolution: str,
    output: Path,
) -> None:
    """Write a self-contained review page with per-scene image links."""
    assets = {entry["reference"]: entry for entry in inventory}
    cards: list[str] = []

    for row in rows:
        reference = row["reference"]
        entry = assets.get(reference) if reference else None
        excerpt = html.escape(row["excerpt"])
        prompt = html.escape((row["prompt"] or "").strip())
        filename = html.escape(Path(reference).name if reference else "No image")
        badges: list[str] = []

        if entry is None:
            badges.append('<span class="badge missing">Chưa có ảnh</span>')
        else:
            size = entry["size"]
            if size:
                badges.append(
                    f'<span class="badge">{size[0]} × {size[1]}</span>'
                )
            if entry["upscaled"]:
                badges.append('<span class="badge warning">Ảnh nhỏ hơn khung</span>')
            if entry["scenes"] > REUSE_LIMIT:
                badges.append(
                    f'<span class="badge warning">Dùng lại {entry["scenes"]} scene</span>'
                )
            if not entry["upscaled"] and entry["scenes"] <= REUSE_LIMIT:
                badges.append('<span class="badge good">Sẵn sàng</span>')

        image_markup = '<div class="placeholder">Chưa có ảnh cho scene này</div>'
        if entry is not None and entry["resolved"] is not None:
            relative = os.path.relpath(entry["resolved"], output.parent).replace(
                os.sep, "/"
            )
            image_url = quote(relative, safe="/:@")
            image_markup = (
                f'<a href="{html.escape(image_url, quote=True)}" target="_blank" '
                'rel="noreferrer">'
                f'<img src="{html.escape(image_url, quote=True)}" '
                f'alt="Scene {row["id"]}: {filename}" loading="lazy"></a>'
            )

        detail = f'<p class="prompt">Prompt: {prompt}</p>' if prompt else ""
        cards.append(
            '<article class="scene">'
            '<div class="scene-head">'
            f'<span class="scene-number">{row["id"]:02d}</span>'
            f'<span class="duration">~{row["seconds"]:.0f}s</span>'
            '</div>'
            f'<div class="preview">{image_markup}</div>'
            f'<div class="badges">{"".join(badges)}</div>'
            f'<h2>{filename}</h2>'
            f'<p class="narration">{excerpt}</p>{detail}'
            '</article>'
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    page = f'''<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Duyệt ảnh — {html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #242b30; background: #edf0ee; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    header {{ padding: 24px clamp(18px, 4vw, 52px); background: #173a3a; color: #fff; }}
    header h1 {{ margin: 0 0 6px; font-size: 24px; font-weight: 650; }}
    header p {{ margin: 0; color: #d4e5dc; font-size: 14px; }}
    main {{ max-width: 1600px; margin: auto; padding: 22px clamp(14px, 3vw, 40px) 48px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr)); gap: 16px; align-items: start; }}
    .scene {{ min-width: 0; overflow: hidden; background: #fff; border: 1px solid #d5ddda; border-radius: 6px; }}
    .scene-head {{ display: flex; align-items: baseline; justify-content: space-between; padding: 12px 14px 8px; }}
    .scene-number {{ color: #176c63; font-size: 17px; font-weight: 750; }}
    .duration {{ color: #67746f; font-size: 12px; }}
    .preview {{ display: grid; place-items: center; min-height: 188px; aspect-ratio: 16 / 9; overflow: hidden; background: repeating-conic-gradient(#e9eeeb 0% 25%, #f8faf9 0% 50%) 50% / 20px 20px; }}
    .preview img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
    .placeholder {{ padding: 18px; color: #9d3e2e; font-size: 14px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 14px 0; }}
    .badge {{ padding: 4px 7px; border-radius: 3px; background: #e8efec; color: #38534b; font-size: 11px; }}
    .badge.warning {{ background: #fff0d5; color: #80520b; }}
    .badge.missing {{ background: #fde4df; color: #963c2c; }}
    .badge.good {{ background: #e2f2e8; color: #286744; }}
    .scene h2 {{ overflow-wrap: anywhere; margin: 10px 14px 6px; font-size: 14px; font-weight: 650; }}
    .narration, .prompt {{ margin: 0; padding: 0 14px 12px; color: #4d5c56; font-size: 13px; line-height: 1.5; }}
    .prompt {{ color: #6a7771; font-size: 12px; }}
    @media (max-width: 480px) {{ header {{ padding-block: 18px; }} header h1 {{ font-size: 20px; }} }}
  </style>
</head>
<body>
  <header><h1>Duyệt ảnh: {html.escape(title)}</h1><p>{len(rows)} scene · khung {html.escape(resolution)} · bấm ảnh để mở bản gốc</p></header>
  <main><section class="grid">{"".join(cards)}</section></main>
</body>
</html>
'''
    output.write_text(page, encoding="utf-8")


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
        "--contact-sheet",
        type=Path,
        default=None,
        help="where to write the visual review page (default: image_contact_sheet.html beside the briefs)",
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
    contact_sheet = args.contact_sheet or out.with_name("image_contact_sheet.html")
    write_contact_sheet(
        rows,
        inventory,
        title=metadata.title,
        resolution=metadata.resolution,
        output=contact_sheet,
    )

    print(f"shot list  : {out}")
    print(f"review     : {contact_sheet}")
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
