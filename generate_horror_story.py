"""generate_horror_story.py

Generate a full supernatural horror story from a seed in
data/horror_story_seeds.json and save the result under
projects/<seed-id>/.

Usage:
    python generate_horror_story.py <seed-id>
    python generate_horror_story.py random
    python generate_horror_story.py next

Output layout:
    projects/
    └── horror-001/
        ├── story.txt       ← full narration text (UTF-8)
        └── story.json      ← seed metadata + generation info
"""

import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Re-use the existing GeminiProvider from the src package
# ---------------------------------------------------------------------------
from ai_mystery_story.infrastructure.ai.gemini_provider import GeminiProvider

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

SEEDS_FILE = PROJECT_ROOT / "data" / "horror_story_seeds.json"
PROJECTS_DIR = PROJECT_ROOT / "projects"

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
HORROR_PROMPT_TEMPLATE = """\
Bạn là một nhà văn chuyên viết truyện kinh dị tâm linh
dành cho kênh YouTube "Sợ ma studio".

Nhiệm vụ:
Từ ý tưởng ngắn dưới đây, hãy phát triển thành một câu chuyện
kinh dị hoàn toàn nguyên bản, chưa từng được xuất bản hay kể ở bất kỳ đâu.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ý TƯỞNG HẠT GIỐNG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tiêu đề gợi ý : {title}
Ý tưởng       : {seed}
Bối cảnh      : {setting}
Các yếu tố    : {elements}
Không khí     : {tone}
Kiểu kết thúc : {ending_type}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YÊU CẦU VỀ NỘI DUNG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Thể loại:
  • Horror, Supernatural, Mystery, Psychological horror

Không khí:
  • U ám, ma mị, cô độc, căng thẳng tăng dần
  • Luôn có cảm giác "có thứ gì đó không đúng"

Cấu trúc bắt buộc (theo thứ tự):
  1. Hook mạnh trong 30 giây đầu khi đọc to
  2. Giới thiệu nhân vật chính với chiều sâu cảm xúc
  3. Thiết lập bí ẩn / bầu không khí đáng sợ
  4. Xuất hiện các dấu hiệu bất thường đầu tiên
  5. Nhân vật điều tra / khám phá sự thật từng bước
  6. Leo thang — mỗi bước tiến đến sự thật lại nguy hiểm hơn
  7. Cao trào — đối mặt trực tiếp với điều kinh khủng
  8. Twist hoặc tiết lộ sự thật bất ngờ
  9. Kết thúc ám ảnh — người nghe không thể ngừng suy nghĩ

Tuyệt đối KHÔNG:
  • Sao chép truyện, phim hay tác phẩm có sẵn
  • Dùng nhân vật nổi tiếng (Sadako, Kayako, v.v.)
  • Giải thích mọi thứ quá sớm
  • Kết thúc "giấc mơ" hoặc "tất cả chỉ là tưởng tượng"

Câu chuyện PHẢI:
  • Có logic nội tại nhất quán
  • Timeline rõ ràng, nhân vật có động cơ thực tế
  • Các chi tiết được foreshadow từ đầu
  • Twist (nếu có) phải có cơ sở từ trước

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YÊU CẦU VỀ VĂN PHONG (TTS-FRIENDLY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Phù hợp để đọc thành tiếng bằng TTS (Text-to-Speech)
  • Câu văn tự nhiên khi nghe, không dài dòng khi đọc to
  • Không dùng quá nhiều câu dài lồng nhau
  • Hạn chế hội thoại — tập trung vào mô tả và cảm xúc nội tâm
  • Tạo khoảng nghỉ tự nhiên bằng đoạn văn ngắn
  • Hình ảnh mô tả đủ mạnh để người nghe tưởng tượng
  • Viết bằng tiếng Việt, văn phong văn học, không dùng từ lóng

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ĐỘ DÀI MỤC TIÊU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • 10.000 – 15.000 từ (tương đương ~45–60 phút đọc)
  • Chia thành các đoạn rõ ràng, có thể dùng dấu "***" ngăn cách phần

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ĐỊNH DẠNG ĐẦU RA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Chỉ trả về phần nội dung câu chuyện, bắt đầu ngay bằng tiêu đề
(không cần ghi "Câu chuyện:" hay bất kỳ tiêu đề meta nào).
Định dạng:

[TIÊU ĐỀ TRUYỆN]

[Nội dung câu chuyện...]
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_seeds() -> dict:
    if not SEEDS_FILE.exists():
        raise RuntimeError(f"Seeds file not found: {SEEDS_FILE}")
    return json.loads(SEEDS_FILE.read_text(encoding="utf-8"))


def save_seeds(data: dict) -> None:
    SEEDS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def find_seed(seeds: list[dict], selector: str) -> dict:
    """Return a seed by id, 'random', or 'next'."""
    if selector == "random":
        available = [s for s in seeds if s.get("status", "pending") == "pending"]
        if not available:
            raise RuntimeError("No pending horror seeds available.")
        return random.choice(available)

    if selector == "next":
        for s in seeds:
            if s.get("status", "pending") == "pending":
                return s
        raise RuntimeError("No pending horror seeds available.")

    for s in seeds:
        if s.get("id") == selector:
            return s

    raise RuntimeError(f"Seed not found: {selector}")


def build_prompt(seed: dict) -> str:
    return HORROR_PROMPT_TEMPLATE.format(
        title=seed.get("title", ""),
        seed=seed.get("seed", ""),
        setting=seed.get("setting", ""),
        elements=", ".join(seed.get("elements", [])),
        tone=", ".join(seed.get("tone", [])),
        ending_type=", ".join(seed.get("ending_type", [])),
    )


def count_words(text: str) -> int:
    """Rough Vietnamese word count (space-separated tokens)."""
    return len(text.split())


def save_story(
    seed_id: str,
    story_text: str,
    seed: dict,
    story_id: str,
) -> tuple[Path, Path]:
    """Save story.txt and story.json; return (txt_path, json_path)."""
    project_dir = PROJECTS_DIR / seed_id
    project_dir.mkdir(parents=True, exist_ok=True)

    txt_path = project_dir / "story.txt"
    txt_path.write_text(story_text, encoding="utf-8")

    metadata = {
        "story_id": story_id,
        "seed_id": seed_id,
        "title": seed.get("title"),
        "genre": seed.get("genre"),
        "seed": seed.get("seed"),
        "setting": seed.get("setting"),
        "elements": seed.get("elements", []),
        "tone": seed.get("tone", []),
        "ending_type": seed.get("ending_type", []),
        "word_count": count_words(story_text),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            "story_text": "story.txt",
        },
    }

    json_path = project_dir / "story.json"
    json_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return txt_path, json_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python generate_horror_story.py <seed-id>")
        print("  python generate_horror_story.py random")
        print("  python generate_horror_story.py next")
        print()
        print("Examples:")
        print("  python generate_horror_story.py horror-001")
        print("  python generate_horror_story.py next")
        sys.exit(1)

    selector = sys.argv[1]

    load_dotenv()

    # ------------------------------------------------------------------
    # Load seeds
    # ------------------------------------------------------------------
    seeds_data = load_seeds()
    seeds = seeds_data.get("seeds", [])

    if not seeds:
        raise RuntimeError("No seeds found in horror_story_seeds.json")

    seed = find_seed(seeds, selector)
    seed_id = seed["id"]

    # ------------------------------------------------------------------
    # Print header
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("GENERATING HORROR STORY")
    print("=" * 60)
    print()
    print(f"Seed ID  : {seed_id}")
    print(f"Title    : {seed.get('title')}")
    print(f"Status   : {seed.get('status', 'pending')}")
    print()
    print(f"Seed     : {seed.get('seed')}")
    print()
    print(f"Setting  : {seed.get('setting')}")
    print()
    print(f"Elements : {', '.join(seed.get('elements', []))}")
    print(f"Tone     : {', '.join(seed.get('tone', []))}")
    print(f"Ending   : {', '.join(seed.get('ending_type', []))}")
    print()

    # ------------------------------------------------------------------
    # Build prompt & generate
    # ------------------------------------------------------------------
    prompt = build_prompt(seed)

    print("Building prompt... done")
    print()
    print("Calling Gemini API — this may take a few minutes...")
    print()

    provider = GeminiProvider()
    story_text = provider.generate(prompt)

    word_count = count_words(story_text)

    print()
    print(f"Generation complete. Word count: {word_count:,}")
    print()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    story_id = str(uuid.uuid4())
    txt_path, json_path = save_story(
        seed_id=seed_id,
        story_text=story_text,
        seed=seed,
        story_id=story_id,
    )

    # Mark seed as generated
    seed["status"] = "generated"
    seed["story_id"] = story_id
    save_seeds(seeds_data)

    # ------------------------------------------------------------------
    # Print summary
    # ------------------------------------------------------------------
    print("=" * 60)
    print("HORROR STORY SAVED")
    print("=" * 60)
    print()
    print(f"Seed ID    : {seed_id}")
    print(f"Story ID   : {story_id}")
    print(f"Word count : {word_count:,}")
    print()
    print(f"Story text : {txt_path.relative_to(PROJECT_ROOT)}")
    print(f"Metadata   : {json_path.relative_to(PROJECT_ROOT)}")
    print()
    print("Next steps:")
    print(f"  Audio  → python generate_audio.py {seed_id}")
    print(f"  Video  → python generate_video.py {seed_id}")
    print()


if __name__ == "__main__":
    main()
