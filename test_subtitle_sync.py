"""
📝 Test Subtitle Sync - Test phụ đề đồng bộ với word-level timestamps
"""

import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv()

FFMPEG = PROJECT_ROOT / "bin" / "ffmpeg"
TEST_DIR = PROJECT_ROOT / "test_subtitle_sync"
TEST_DIR.mkdir(exist_ok=True)

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080


def main():
    print()
    print("=" * 60)
    print("📝 SUBTITLE SYNC TEST (Word-level timestamps)")
    print("=" * 60)

    # Short test text
    test_text = (
        "Khu chung cư cũ Hoa Mai nằm im lìm dưới màn sương đêm. "
        "Đêm hôm đó, Minh thức dậy vì nghe tiếng gõ cửa kỳ lạ. "
        "Căn hộ ở tầng 5 im lặng đến đáng sợ."
    )

    print(f"\n📝 Text: {test_text[:80]}...")

    # ── Step 1: Generate TTS with timestamps ────────────────
    print("\n" + "=" * 60)
    print("STEP 1/3: Generate TTS with word timestamps")
    print("=" * 60)

    from ai_mystery_story.infrastructure.tts.edge_tts_timestamped import EdgeTTSTimestamped

    tts = EdgeTTSTimestamped(
        voice="vi-VN-NamMinhNeural",
        rate="-7%",
        pitch="-2Hz",
    )

    audio_dir = TEST_DIR / "audio"
    audio_dir.mkdir(exist_ok=True)

    audio_path = audio_dir / "narration.mp3"
    ts_path = audio_dir / "timestamps.json"

    start = time.time()
    result = tts.generate_with_timestamps(
        text=test_text,
        audio_output=audio_path,
        timestamps_output=ts_path,
    )
    elapsed = time.time() - start

    print(f"\n  ✅ Generated in {elapsed:.1f}s")
    print(f"  🎵 Duration: {result['duration']:.2f}s")
    print(f"  📝 Words: {len(result['words'])}")
    print(f"  📄 Segments: {len(result['segments'])}")

    # Show word timestamps
    print("\n  Word timestamps:")
    for w in result["words"][:15]:
        print(f"    {w['start']:.2f}s - {w['end']:.2f}s: {w['text']}")
    if len(result["words"]) > 15:
        print(f"    ... ({len(result['words']) - 15} more words)")

    # Show segments
    print("\n  Sentence segments:")
    for s in result["segments"]:
        print(f"    {s['start']:.2f}s - {s['end']:.2f}s: {s['text'][:50]}...")

    # ── Step 2: Generate SRT from real timestamps ───────────
    print("\n" + "=" * 60)
    print("STEP 2/3: Generate SRT from real timestamps")
    print("=" * 60)

    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import SubtitleGenerator

    sub_gen = SubtitleGenerator(max_chars_per_line=40, max_lines=2)
    srt_path = TEST_DIR / "subtitles.srt"

    # Use word-level timestamps for accurate SRT
    sub_gen.generate_from_word_timestamps(
        words=result["words"],
        output_path=srt_path,
    )

    # Show SRT content
    srt_content = srt_path.read_text(encoding="utf-8")
    print(f"\n  📄 SRT content:")
    for line in srt_content.strip().split("\n")[:20]:
        print(f"    {line}")

    # ── Step 3: Create video with synced subtitles ──────────
    print("\n" + "=" * 60)
    print("STEP 3/3: Create video with synced subtitles")
    print("=" * 60)

    # Create simple video from image
    img = PROJECT_ROOT / "projects" / "mystery-001" / "images" / "beat_01.png"
    raw_video = TEST_DIR / "raw_video.mp4"

    subprocess.run([
        str(FFMPEG), "-y",
        "-loop", "1", "-i", str(img),
        "-t", str(result["duration"]),
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,format=yuv420p",
        "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        str(raw_video),
    ], capture_output=True, check=True)
    print("  ✅ Raw video created")

    # Add audio
    video_audio = TEST_DIR / "video_audio.mp4"
    subprocess.run([
        str(FFMPEG), "-y",
        "-i", str(raw_video),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(video_audio),
    ], capture_output=True, check=True)
    print("  ✅ Audio added")

    # Burn subtitles
    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import burn_subtitles_into_video

    final_output = TEST_DIR / "final_synced.mp4"
    burn_subtitles_into_video(video_audio, srt_path, final_output, font_size=22)

    # Cleanup
    for f in [raw_video, video_audio]:
        f.unlink(missing_ok=True)

    # ── Results ─────────────────────────────────────────────
    print()
    print("=" * 60)
    print("🎉 SUBTITLE SYNC TEST COMPLETED!")
    print("=" * 60)

    if final_output.exists():
        size_kb = final_output.stat().st_size // 1024
        print(f"\n📁 Output: {final_output}")
        print(f"💾 Size: {size_kb} KB")
        print(f"⏱️ Duration: {result['duration']:.1f}s")
        print()
        print("✨ So sánh với version cũ:")
        print("  ❌ Cũ: Proportional timing (chia đều theo ký tự)")
        print("  ✅ Mới: Word-level timestamps (đồng bộ chính xác)")
        print()
        print("👉 Mở file để xem:")
        print(f"   open {final_output}")
    else:
        print("❌ Output not found!")

    # Save timestamp data for reference
    print(f"\n📄 Timestamps data: {ts_path}")


if __name__ == "__main__":
    main()
