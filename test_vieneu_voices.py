"""
Test VieNeu-TTS voices (local, 48kHz).

Generates the same horror-style sample used by test_fish_voices.py so the
two engines can be compared A/B by ear. Runs fully offline on CPU via ONNX
(first run downloads ~282MB from Hugging Face).

Usage:
    python test_vieneu_voices.py            # generate samples for the 15 new Bắc/Nam voices
    python test_vieneu_voices.py 2          # test only voice #2 (from the full 23-voice list)
    python test_vieneu_voices.py --list     # list voices without generating
"""

import sys
import time
from pathlib import Path

from vieneu import Vieneu

# Same sample text as test_fish_voices.py for a fair A/B comparison
SAMPLE_TEXT = (
    "Đêm đó, gió thổi rít qua khe cửa, căn nhà cũ kỹ chìm trong bóng tối. "
    "Từ tầng hầm, một tiếng động lạ vang lên, chậm rãi, như bước chân của "
    "ai đó đang tiến lại gần..."
)

OUTPUT_DIR = Path("test_vieneu_voices_output")

# Default shortlist: Bắc/Nam voices only (Trung excluded on purpose).
# Two voices already sampled (Mỹ Duyên, Quỳnh Anh, Anh Khôi) are skipped so
# every run produces only NEW samples.
DEFAULT_VOICE_NAMES = [
    # Bắc — kể chuyện / đọc truyện
    "Ngọc Linh",    # Nữ · Bắc · kể chuyện
    "Thanh Bình",    # Nam · Bắc · kể chuyện
    "Đức Trí",      # Nam · Bắc · đọc truyện
    "Ngọc Huyền",   # Nữ · Bắc · giọng đọc tự nhiên
    # Bắc — tự nhiên / tin tức
    "Minh Đức",     # Nam · Bắc · tin tức
    "Phạm Tuyên",   # Nam · Bắc · tự nhiên
    "Xuân Vĩnh",    # Nam · Bắc · tự nhiên
    "Trúc Ly",      # Nữ · Bắc · tự nhiên
    "Đoan Trang",   # Nữ · Bắc · tự nhiên
    "Mai Anh",      # Nữ · Bắc · tin tức
    "Mạnh Dũng",    # Nam · Bắc · tự nhiên
    "Minh Quân",    # Nam · Bắc · tự nhiên
    # Nam — kể chuyện / đọc truyện / tự nhiên
    "Thục Đoan",    # Nữ · Nam · kể chuyện
    "Kim Thanh",    # Nữ · Nam · đọc truyện
    "Thùy Dung",    # Nữ · Nam · tin tức
]


def list_voices(tts: Vieneu) -> None:
    voices = tts.list_preset_voices()
    print(f"\nDANH SACH {len(voices)} GIONG (VieNeu v3 Turbo, 48kHz):")
    print("=" * 60)
    for index, (label, voice_id) in enumerate(voices, 1):
        print(f"  {index}. {label}")


def test_voice(tts: Vieneu, voice_id: str, output_path: Path) -> bool:
    """Generate a sample with one voice. Returns True on success."""
    print(f"\n[{voice_id}] Generating...")
    start = time.time()

    try:
        audio = tts.infer(SAMPLE_TEXT, voice=voice_id)
        tts.save(audio, str(output_path))
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False

    elapsed = time.time() - start
    duration = len(audio) / 48_000
    rtf = elapsed / duration if duration > 0 else float("inf")
    size_kb = output_path.stat().st_size / 1024
    print(
        f"  OK -> {output_path.name} "
        f"({size_kb:.0f} KB, audio {duration:.1f}s, took {elapsed:.1f}s, "
        f"RTF {rtf:.2f})"
    )
    return True


def main() -> None:
    tts = Vieneu()

    if "--list" in sys.argv:
        list_voices(tts)
        return

    all_voices = {
        voice_id: label
        for label, voice_id in tts.list_preset_voices()
    }

    only = None
    for arg in sys.argv[1:]:
        if arg.isdigit():
            only = int(arg)
            break

    if only is not None:
        voice_ids = list(all_voices.keys())
        selected = [voice_ids[only - 1]] if 0 < only <= len(voice_ids) else []
    else:
        selected = [
            v for v in DEFAULT_VOICE_NAMES if v in all_voices
        ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("VIENEU-TTS VOICE TEST (local CPU, 48kHz)")
    print("=" * 60)
    print(f"Sample text: {SAMPLE_TEXT[:50]}...")

    results: dict[str, bool] = {}
    for voice_id in selected:
        safe_name = voice_id.lower().replace(" ", "_")
        output_path = OUTPUT_DIR / f"voice_{safe_name}.wav"
        results[voice_id] = test_voice(tts, voice_id, output_path)

    print("\n" + "=" * 60)
    print("KET QUA:")
    print("=" * 60)
    for voice_id, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {voice_id}")
    print(f"\nNghe thu cac file trong: {OUTPUT_DIR}/")
    print("(So sanh voi test_fish_voices_output/ de A/B hai engine)")


if __name__ == "__main__":
    main()
