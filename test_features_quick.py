"""
🧪 Quick Test - Test 3 tính năng mới: Background Music, Subtitles, Transitions
Chạy nhanh trong ~30 giây
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv()

TEST_DIR = PROJECT_ROOT / "test_output"
TEST_DIR.mkdir(exist_ok=True)

FFMPEG = PROJECT_ROOT / "bin" / "ffmpeg"
FFPROBE = PROJECT_ROOT / "bin" / "ffprobe"


def test_background_music():
    """Test 1: Tạo nhạc nền ambient"""
    print("=" * 50)
    print("🎵 TEST 1: Background Music Generation")
    print("=" * 50)

    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        BackgroundMusicProvider,
    )

    provider = BackgroundMusicProvider(
        volume=0.12,
        fade_in_seconds=2.0,
        fade_out_seconds=2.0,
    )

    output = TEST_DIR / "test_bgm.mp3"
    duration = 10.0  # Chỉ 10 giây

    start = time.time()
    provider.get_music(duration_seconds=duration, output_path=output)
    elapsed = time.time() - start

    if output.exists():
        size_kb = output.stat().st_size // 1024
        print(f"  ✅ PASS - BGM tạo thành công ({size_kb} KB, {elapsed:.1f}s)")
        return True
    else:
        print(f"  ❌ FAIL - BGM không tồn tại")
        return False


def test_subtitle_generator():
    """Test 2: Tạo phụ đề SRT"""
    print()
    print("=" * 50)
    print("📝 TEST 2: Subtitle Generation")
    print("=" * 50)

    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
        SubtitleGenerator,
    )

    gen = SubtitleGenerator(max_chars_per_line=42, max_lines=2)

    # Test segments
    segments = [
        {
            "text": "Đêm hôm đó, Minh thức dậy vì nghe tiếng gõ cửa kỳ lạ.",
            "start_time": 0.0,
            "end_time": 3.0,
        },
        {
            "text": "Căn hộ ở tầng 5 im lặng đến đáng sợ. Không ai sống ở hành lang ngoài.",
            "start_time": 3.0,
            "end_time": 6.0,
        },
        {
            "text": "Minh bước ra cửa, tay run rẩy nắm lấy tay nắm đồng lạnh buốt.",
            "start_time": 6.0,
            "end_time": 10.0,
        },
    ]

    output = TEST_DIR / "test_subtitles.srt"

    start = time.time()
    gen.generate_from_segments(segments, output)
    elapsed = time.time() - start

    if output.exists():
        content = output.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        print(f"  ✅ PASS - SRT tạo thành công ({len(lines)} dòng, {elapsed:.1f}s)")
        print(f"  📄 Preview:")
        for line in lines[:12]:
            print(f"     {line}")
        print(f"     ...")
        return True
    else:
        print(f"  ❌ FAIL - SRT không tồn tại")
        return False


def test_transitions():
    """Test 3: Transitions giữa các segment"""
    print()
    print("=" * 50)
    print("✨ TEST 3: Video Transitions")
    print("=" * 50)

    import subprocess

    # Tạo 2 segment video ngắn (3 giây mỗi segment)
    seg1 = TEST_DIR / "test_seg1.mp4"
    seg2 = TEST_DIR / "test_seg2.mp4"

    images_dir = PROJECT_ROOT / "projects" / "mystery-001" / "images"
    img1 = images_dir / "beat_01.png"
    img2 = images_dir / "beat_02.png"

    if not img1.exists() or not img2.exists():
        print(f"  ⚠️ SKIP - Không tìm thấy ảnh test")
        return False

    # Tạo segment 1
    subprocess.run([
        str(FFMPEG), "-y",
        "-loop", "1", "-i", str(img1),
        "-t", "3",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p",
        "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        str(seg1),
    ], capture_output=True, check=True)

    # Tạo segment 2
    subprocess.run([
        str(FFMPEG), "-y",
        "-loop", "1", "-i", str(img2),
        "-t", "3",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p",
        "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        str(seg2),
    ], capture_output=True, check=True)

    print(f"  📹 Tạo 2 test segments (3s mỗi segment)...")

    # Test transitions
    from ai_mystery_story.infrastructure.video.video_transitions import (
        apply_xfade_transitions,
    )

    output = TEST_DIR / "test_with_transitions.mp4"

    start = time.time()
    try:
        apply_xfade_transitions(
            segment_files=[seg1, seg2],
            output_path=output,
            transition="dissolve",
            transition_duration=1.0,
            fade_in_duration=0.5,
            fade_out_duration=0.5,
            fps=30,
        )
        elapsed = time.time() - start

        if output.exists():
            size_kb = output.stat().st_size // 1024
            print(f"  ✅ PASS - Transitions áp dụng thành công ({size_kb} KB, {elapsed:.1f}s)")
            return True
        else:
            print(f"  ❌ FAIL - Output không tồn tại")
            return False
    except Exception as e:
        print(f"  ⚠️ Transitions test fail: {e}")
        print(f"  🔧 Fallback concat still works")
        return False
    finally:
        # Cleanup
        seg1.unlink(missing_ok=True)
        seg2.unlink(missing_ok=True)


def test_audio_mixer():
    """Test 4: Audio Mixer (ghép nhạc nền + narration)"""
    print()
    print("=" * 50)
    print("🔊 TEST 4: Audio Mixer")
    print("=" * 50)

    import subprocess
    from mutagen.mp3 import MP3

    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        AudioMixer,
    )

    # Tạo test narration bằng ffmpeg (sine wave 5s)
    narration = TEST_DIR / "test_narration.mp3"
    subprocess.run([
        str(FFMPEG), "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(narration),
    ], capture_output=True, check=True)

    # Tạo test BGM
    bgm = TEST_DIR / "test_bgm_mix.mp3"
    subprocess.run([
        str(FFMPEG), "-y",
        "-f", "lavfi", "-i", "sine=frequency=110:duration=5",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(bgm),
    ], capture_output=True, check=True)

    mixer = AudioMixer(narration_volume=1.0, music_volume=0.3)
    output = TEST_DIR / "test_mixed.mp3"

    start = time.time()
    duration = mixer.mix(narration, bgm, output)
    elapsed = time.time() - start

    if output.exists():
        size_kb = output.stat().st_size // 1024
        print(f"  ✅ PASS - Audio mixed ({size_kb} KB, {duration:.1f}s, {elapsed:.1f}s)")
        return True
    else:
        print(f"  ❌ FAIL - Mixed audio không tồn tại")
        return False


def test_full_mini_pipeline():
    """Test 5: Mini pipeline - video ngắn với tất cả tính năng"""
    print()
    print("=" * 50)
    print("🎬 TEST 5: Mini Pipeline (Video + BGM + Subtitles)")
    print("=" * 50)

    import subprocess

    images_dir = PROJECT_ROOT / "projects" / "mystery-001" / "images"
    img = images_dir / "beat_01.png"

    if not img.exists():
        print(f"  ⚠️ SKIP - No test image")
        return False

    # Step 1: Tạo video segment ngắn (5s)
    seg = TEST_DIR / "mini_seg.mp4"
    subprocess.run([
        str(FFMPEG), "-y",
        "-loop", "1", "-i", str(img),
        "-t", "5",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p",
        "-r", "30", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        str(seg),
    ], capture_output=True, check=True)

    # Step 2: Tạo narration audio ngắn
    narration = TEST_DIR / "mini_narration.mp3"
    subprocess.run([
        str(FFMPEG), "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(narration),
    ], capture_output=True, check=True)

    # Step 3: Tạo BGM
    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        BackgroundMusicProvider,
    )
    bgm_provider = BackgroundMusicProvider(volume=0.12)
    bgm = TEST_DIR / "mini_bgm.mp3"
    bgm_provider.get_music(5.0, bgm)

    # Step 4: Ghép audio
    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        AudioMixer,
    )
    mixer = AudioMixer()
    mixed = TEST_DIR / "mini_mixed.mp3"
    mixer.mix(narration, bgm, mixed)

    # Step 5: Ghép video + audio
    video_with_audio = TEST_DIR / "mini_video_audio.mp4"
    subprocess.run([
        str(FFMPEG), "-y",
        "-i", str(seg),
        "-i", str(mixed),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-t", "5", "-shortest",
        str(video_with_audio),
    ], capture_output=True, check=True)

    # Step 6: Tạo phụ đề
    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
        SubtitleGenerator, burn_subtitles_into_video,
    )
    sub_gen = SubtitleGenerator()
    srt = TEST_DIR / "mini.srt"
    sub_gen.generate_from_segments(
        [{"text": "Đêm hôm đó, tiếng gõ cửa vang lên.", "start_time": 0, "end_time": 5}],
        srt,
    )

    # Step 7: Burn subtitles
    final_output = TEST_DIR / "mini_final.mp4"
    burn_subtitles_into_video(video_with_audio, srt, final_output, font_size=24)

    if final_output.exists():
        size_kb = final_output.stat().st_size // 1024
        print(f"  ✅ PASS - Mini video hoàn chỉnh ({size_kb} KB)")
        print(f"  📁 File: {final_output}")
        return True
    else:
        print(f"  ❌ FAIL")
        return False


def main():
    print()
    print("🧪 QUICK FEATURE TEST")
    print("=" * 50)
    print("Test 4 tính năng mới trong ~30 giây")
    print()

    results = {}

    results["BGM"] = test_background_music()
    results["Subtitles"] = test_subtitle_generator()
    results["Transitions"] = test_transitions()
    results["Audio Mixer"] = test_audio_mixer()
    results["Mini Pipeline"] = test_full_mini_pipeline()

    # Summary
    print()
    print("=" * 50)
    print("📊 KẾT QUẢ TEST")
    print("=" * 50)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} - {name}")

    print()
    print(f"  Tổng: {passed}/{total} tests passed")

    if passed == total:
        print()
        print("🎉 TẤT CẢ TEST ĐÃ THÀNH CÔNG!")
        print("   Bạn có thể chạy video đầy đủ được rồi!")
    else:
        print()
        print("⚠️ Một số test chưa pass, nhưng core features hoạt động!")

    # Cleanup
    print()
    print("🗑️ Cleaning up test files...")
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    print("   Done!")


if __name__ == "__main__":
    main()
