"""add_chapter.py

Nhân bản thumbnail gốc từ projects/<source-project>/thumbnail/
và chèn text chương (ví dụ "Chapter 1-20") vào góc trên bên trái.

Source thumbnail luôn lấy từ project có --source (mặc định là
<topic-id> với phần số được bỏ đi, ví dụ tt-lathien-1-20 → tt-lathien--full_text).
Có thể override bằng --source.

Output lưu vào:
    projects/<topic-id>/thumbnail/<original-name>

Usage:
    python add_chapter.py <topic-id> [options]

Examples:
    python add_chapter.py tt-lathien-1-20
    python add_chapter.py tt-lathien-21-40
    python add_chapter.py tt-lathien-1-20 --source tt-lathien--full_text
    python add_chapter.py tt-lathien-1-20 --text "Tập 1-20"
    python add_chapter.py tt-lathien-1-20 --font-size 120
"""

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = PROJECT_ROOT / "projects"

# Font dùng để vẽ chữ
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Màu chữ và shadow
TEXT_COLOR = (255, 255, 255, 255)  # trắng
SHADOW_COLOR      = (0, 0, 0, 200)         # đen bán trong suốt
BACKGROUND_COLOR  = (0, 0, 0, 160)         # nền bán trong suốt phía sau chữ

# Vị trí: cách mép trái / mép trên
MARGIN_X = 60
MARGIN_Y = 50

# Padding bên trong background box
PAD_X = 28
PAD_Y = 18

# Font size mặc định (có thể override bằng --font-size)
DEFAULT_FONT_SIZE = 130


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def find_source_project(topic_id: str) -> str:
    """
    Tự động suy ra source project từ topic_id.
    Ví dụ: tt-lathien-1-20  → tt-lathien--full_text
            tt-lathien-21-40 → tt-lathien--full_text
    Quy tắc: bỏ phần -số-số ở cuối rồi thêm --full_text.
    Nếu không match pattern thì dùng chính topic_id làm source.
    """
    stripped = re.sub(r"-\d+(?:-\d+)?$", "", topic_id)
    candidate = stripped + "--full_text"
    if (PROJECTS_DIR / candidate).exists():
        return candidate
    return topic_id


def find_thumbnail(project_id: str) -> Path:
    """Trả về file ảnh đầu tiên trong projects/<project_id>/thumbnail/."""
    thumb_dir = PROJECTS_DIR / project_id / "thumbnail"
    if not thumb_dir.exists():
        raise FileNotFoundError(f"Thumbnail directory not found: {thumb_dir}")

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = sorted([f for f in thumb_dir.iterdir() if f.suffix.lower() in exts])
    if not files:
        raise FileNotFoundError(f"No image found in: {thumb_dir}")
    return files[0]


def infer_chapter_text(topic_id: str) -> str:
    """
    Suy ra text chương từ topic_id.
    Ví dụ: tt-lathien-1-20  → 'Chapter 1-20'
            tt-lathien-21-40 → 'Chapter 21-40'
    Nếu không match trả về topic_id.
    """
    m = re.search(r"(\d+)-(\d+)$", topic_id)
    if m:
        return f"Chapter {m.group(1)}-{m.group(2)}"
    return topic_id


def draw_chapter_text(
    image: Image.Image,
    text: str,
    font_size: int,
) -> Image.Image:
    """Vẽ text lên ảnh ở góc trên bên trái, có nền mờ và shadow."""
    img = image.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Load font
    if FONT_PATH.exists():
        font = ImageFont.truetype(str(FONT_PATH), font_size)
    else:
        # Fallback: PIL default (nhỏ, không bold — chỉ dùng khi không có font)
        print(f"  WARNING: font not found at {FONT_PATH}, using PIL default")
        font = ImageFont.load_default()

    # Đo kích thước text
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_offset_x = -bbox[0]
    text_offset_y = -bbox[1]

    # Vị trí box nền
    box_x1 = MARGIN_X
    box_y1 = MARGIN_Y
    box_x2 = MARGIN_X + text_w + PAD_X * 2
    box_y2 = MARGIN_Y + text_h + PAD_Y * 2

    # Vẽ background box (layer riêng để có alpha)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.rounded_rectangle(
        [box_x1, box_y1, box_x2, box_y2],
        radius=16,
        fill=BACKGROUND_COLOR,
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Vị trí text trong box
    text_x = box_x1 + PAD_X + text_offset_x
    text_y = box_y1 + PAD_Y + text_offset_y

    # Shadow (offset 4px)
    shadow_offset = max(4, font_size // 28)
    draw.text(
        (text_x + shadow_offset, text_y + shadow_offset),
        text,
        font=font,
        fill=SHADOW_COLOR,
    )

    # Chữ chính
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=TEXT_COLOR,
    )

    return img


# ----------------------------------------------------------------
# Main
# ----------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    # Parse options
    source_override = None
    chapter_text_override = None
    font_size = DEFAULT_FONT_SIZE

    if "--source" in args:
        idx = args.index("--source")
        source_override = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--text" in args:
        idx = args.index("--text")
        chapter_text_override = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--font-size" in args:
        idx = args.index("--font-size")
        font_size = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("Usage:")
        print("  python add_chapter.py <topic-id> [options]")
        print()
        print("Options:")
        print("  --source <project-id>   Source thumbnail project")
        print("                          (default: auto-detected from topic-id)")
        print("  --text <chapter-text>   Chapter text to overlay")
        print("                          (default: auto from topic-id, e.g. 'Chapter 1-20')")
        print("  --font-size <int>       Font size (default: 110)")
        print()
        print("Examples:")
        print("  python add_chapter.py tt-lathien-1-20")
        print("  python add_chapter.py tt-lathien-1-20 --text 'Tập 1-20'")
        print("  python add_chapter.py tt-lathien-1-20 --source tt-lathien--full_text")
        sys.exit(1)

    topic_id = args[0]

    # Resolve source project
    source_project = source_override or find_source_project(topic_id)

    # Resolve chapter text
    chapter_text = chapter_text_override or infer_chapter_text(topic_id)

    print()
    print("=" * 60)
    print("ADD CHAPTER TEXT TO THUMBNAIL")
    print("=" * 60)
    print(f"Topic ID       : {topic_id}")
    print(f"Source project : {source_project}")
    print(f"Chapter text   : {chapter_text}")
    print(f"Font size      : {font_size}")
    print()

    # Find source thumbnail
    try:
        source_thumb = find_thumbnail(source_project)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Source file    : {source_thumb}")

    # Prepare output dir
    output_dir = PROJECTS_DIR / topic_id / "thumbnail"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / source_thumb.name

    # Open source image
    img = Image.open(source_thumb)
    print(f"Image size     : {img.size[0]}x{img.size[1]}px")
    print()

    # Draw chapter text
    result = draw_chapter_text(img, chapter_text, font_size)

    # Save (preserve format)
    if output_path.suffix.lower() in {".jpg", ".jpeg"}:
        result.convert("RGB").save(output_path, quality=95)
    else:
        result.save(output_path)

    size_kb = output_path.stat().st_size // 1024
    print(f"Output         : {output_path}")
    print(f"File size      : {size_kb} KB")
    print()
    print("Done!")
    print()


if __name__ == "__main__":
    main()
