"""
🧪 Test Video Quality - Video 2.2 phút với đầy đủ tính năng
BGM volume increased to 0.25 (25%) for better audibility
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

# ============================================================
# Config
# ============================================================
FFMPEG = PROJECT_ROOT / "bin" / "ffmpeg"
FFPROBE = PROJECT_ROOT / "bin" / "ffprobe"
TOPIC_ID = "mystery-001"
PROJECT_DIR = PROJECT_ROOT / "projects" / TOPIC_ID

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 30

TEST_DIR = PROJECT_ROOT / "test_quality_output"
TEST_DIR.mkdir(exist_ok=True)


def get_duration(path):
    r = subprocess.run(
        [str(FFPROBE), "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def main():
    print()
    print("=" * 60)
    print("🧪 TEST VIDEO QUALITY - BGM VOLUME 25%")
    print("=" * 60)
    print()

    # ── Step 0: Chuẩn bị ────────────────────────────────────
    print("Step 0/7: Chuẩn bị素材...")

    # Thumbnail
    thumbnail_dir = PROJECT_DIR / "thumbnail"
    thumbnail_dir.mkdir(exist_ok=True)
    thumb = thumbnail_dir / "beat_01.png"
    if not thumb.exists():
        import shutil
        shutil.copy2(PROJECT_DIR / "images" / "beat_01.png", thumb)
        print("  ✅ Thumbnail created from beat_01.png")

    # Get narration text
    story_data = json.loads((PROJECT_DIR / "story.json").read_text())
    full_text = story_data["narration"]["segments"][0]["text"]

    # Use ~2300 chars for ~2.5 min audio
    short_text = full_text[:2300]
    print(f"  ✅ Text: {len(short_text)} chars")

    # ── Step 1: Generate audio + timestamps TOGETHER ───────
    print()
    print("=" * 60)
    print("Step 1/7: Tạo audio + timestamps (cùng lúc)")
    print("=" * 60)

    from ai_mystery_story.infrastructure.tts.edge_tts_timestamped import EdgeTTSTimestamped

    tts = EdgeTTSTimestamped(
        voice="vi-VN-NamMinhNeural",
        rate="-7%",
        pitch="-2Hz",
    )

    narration_audio = TEST_DIR / "narration.mp3"
    ts_file = TEST_DIR / "timestamps.json"

    print(f"  ⏳ Generating audio + timestamps...")
    start = time.time()
    result = tts.generate_with_timestamps(
        text=short_text,
        audio_output=narration_audio,
        timestamps_output=ts_file,
    )
    elapsed = time.time() - start

    duration = result["duration"]
    word_timestamps = result["words"]
    print(f"  ✅ Audio: {duration:.1f}s ({duration/60:.1f} min) [{elapsed:.1f}s]")
    print(f"  ✅ Timestamps: {len(word_timestamps)} words")

    # ── Step 2: Tạo video segments (Ken Burns) ─────────────
    print()
    print("=" * 60)
    print("Step 2/7: Tạo video segments (Ken Burns + firelight)")
    print("=" * 60)

    images_dir = PROJECT_DIR / "images"
    images = sorted([
        p for p in images_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ])
    print(f"  Images available: {len(images)}")

    segment_duration = 10
    num_segments = max(1, int(duration / segment_duration) + 1)
    segment_files = []

    print(f"  Creating {num_segments} segments...")

    for i in range(num_segments):
        img = images[i % len(images)]
        seg_dur = min(segment_duration, duration - i * segment_duration)
        if seg_dur <= 0:
            break

        seg_file = TEST_DIR / f"seg_{i:04d}.mp4"
        segment_files.append(seg_file)

        movement = i % 4
        dur_expr = f"{seg_dur:.6f}"

        scale_filter = (
            f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:"
            "force_original_aspect_ratio=increase"
        )

        if movement == 0:
            crop = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*t/{dur_expr}"
        elif movement == 1:
            crop = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*(1-t/{dur_expr})"
        elif movement == 2:
            crop = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*t/{dur_expr}:(in_h-out_h)/2"
        else:
            crop = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*(1-t/{dur_expr}):(in_h-out_h)/2"

        firelight = (
            "eq=brightness='"
            "0.015+0.020*sin(2*PI*t/3.7)"
            "+0.012*sin(2*PI*t/1.9)"
            "+0.008*sin(2*PI*t/5.3)'"
        )

        vf = f"{scale_filter},{crop},{firelight},setsar=1,format=yuv420p"

        subprocess.run([
            str(FFMPEG), "-y",
            "-loop", "1", "-i", str(img),
            "-t", str(seg_dur),
            "-vf", vf,
            "-r", str(FPS), "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(seg_file),
        ], capture_output=True, check=True)

        print(f"  [{i+1}/{num_segments}] {img.name} ({seg_dur:.1f}s)", end="\r")

    print(f"\n  ✅ {len(segment_files)} segments created")

    # ── Step 3: Apply transitions ──────────────────────────
    print()
    print("=" * 60)
    print("Step 3/7: Áp dụng transitions (dissolve)")
    print("=" * 60)

    from ai_mystery_story.infrastructure.video.video_transitions import apply_xfade_transitions

    silent_video = TEST_DIR / "silent.mp4"
    start = time.time()
    apply_xfade_transitions(
        segment_files=segment_files,
        output_path=silent_video,
        transition="dissolve",
        transition_duration=1.0,
        fade_in_duration=1.5,
        fade_out_duration=2.0,
        fps=FPS,
    )
    elapsed = time.time() - start
    print(f"  ✅ Transitions applied ({elapsed:.1f}s)")

    # ── Step 4: Generate Background Music (INCREASED VOLUME) ─
    print()
    print("=" * 60)
    print("Step 4/7: Tạo Background Music (Volume: 25%)")
    print("=" * 60)

    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        BackgroundMusicProvider, AudioMixer,
    )

    bgm_provider = BackgroundMusicProvider(
        volume=0.25,  # Increased from 0.12 to 0.25
        fade_in_seconds=2.0,
        fade_out_seconds=3.0,
    )
    bgm_path = TEST_DIR / "bgm.mp3"
    start = time.time()
    bgm_provider.get_music(duration_seconds=duration, output_path=bgm_path)
    elapsed = time.time() - start
    print(f"  ✅ BGM generated (volume: 25%) ({elapsed:.1f}s)")

    # ── Step 5: Mix audio ─────────────────────────────────
    print()
    print("=" * 60)
    print("Step 5/7: Ghép narration + BGM")
    print("=" * 60)

    mixer = AudioMixer(narration_volume=1.0, music_volume=1.0)
    mixed_audio = TEST_DIR / "mixed.mp3"
    start = time.time()
    mix_dur = mixer.mix(narration_audio, bgm_path, mixed_audio)
    elapsed = time.time() - start
    print(f"  ✅ Mixed audio: {mix_dur:.1f}s ({elapsed:.1f}s)")

    # ── Step 6: Combine video + audio ──────────────────────
    print()
    print("=" * 60)
    print("Step 6/7: Ghép video + audio")
    print("=" * 60)

    video_with_audio = TEST_DIR / "video_audio.mp4"
    subprocess.run([
        str(FFMPEG), "-y",
        "-i", str(silent_video),
        "-i", str(mixed_audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), "-shortest",
        "-movflags", "+faststart",
        str(video_with_audio),
    ], capture_output=True, check=True)
    print(f"  ✅ Video + Audio combined")

    # ── Step 7: Burn subtitles ─────────────────────────────
    print()
    print("=" * 60)
    print("Step 7/7: Burn subtitles vào video")
    print("=" * 60)

    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
        SubtitleGenerator, burn_subtitles_into_video,
    )

    sub_gen = SubtitleGenerator(max_chars_per_line=42, max_lines=2)
    srt_path = TEST_DIR / "subtitles.srt"

    print(f"  Using word-level timestamps ({len(word_timestamps)} words)")
    sub_gen.generate_from_word_timestamps(
        words=word_timestamps,
        output_path=srt_path,
    )

    final_output = TEST_DIR / "final_video_5min.mp4"
    start = time.time()
    burn_subtitles_into_video(
        video_with_audio, srt_path, final_output,
        font_size=22, position="bottom",
    )
    elapsed = time.time() - start
    print(f"  ✅ Subtitles burned ({elapsed:.1f}s)")

    # ── Cleanup ─────────────────────────────────────────────
    print()
    print("Cleaning up temp files...")
    for f in segment_files:
        f.unlink(missing_ok=True)
    for f in [silent_video, video_with_audio, srt_path, mixed_audio, bgm_path]:
        f.unlink(missing_ok=True)

    # ── Results ─────────────────────────────────────────────
    print()
    print("=" * 60)
    print("🎉 TEST HOÀN THÀNH!")
    print("=" * 60)

    if final_output.exists():
        size_mb = final_output.stat().st_size / (1024 * 1024)
        final_dur = get_duration(final_output)
        print()
        print(f"📁 Output: {final_output}")
        print(f"⏱️ Duration: {final_dur:.1f}s ({final_dur/60:.1f} min)")
        print(f"💾 Size: {size_mb:.1f} MB")
        print()
        print("✨ Tính năng đã test:")
        print("  ✅ 🎵 Background Music (volume: 25%)")
        print("  ✅ 📝 Subtitles (word-level timestamps)")
        print("  ✅ ✨ Transitions (dissolve)")
        print("  ✅ 🎬 Ken Burns effect")
        print("  ✅ 🔥 Firelight flicker effect")
        print()
        print("👉 Mở file để xem:")
        print(f"   {final_output}")
    else:
        print("❌ Output not found!")


if __name__ == "__main__":
    main()
