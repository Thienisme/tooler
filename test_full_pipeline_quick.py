"""
🎬 Full Pipeline Quick Test - Video 30 giây với tất cả tính năng mới
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
FFPROBE = PROJECT_ROOT / "bin" / "ffprobe"
PROJECTS_DIR = PROJECT_ROOT / "projects"
TEST_DIR = PROJECT_ROOT / "test_output_full"
TEST_DIR.mkdir(exist_ok=True)

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 30


def get_duration(path):
    r = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def main():
    print()
    print("=" * 60)
    print("🎬 FULL PIPELINE QUICK TEST (30 giây)")
    print("=" * 60)

    topic_id = "mystery-001"
    project_dir = PROJECTS_DIR / topic_id

    # Load story
    story = json.loads((project_dir / "story.json").read_text())
    title = story["blueprint"]["title"]
    narration_text = story["narration"]["segments"][0]["text"]

    # Use only first 500 chars for quick test (~30s)
    short_text = narration_text[:500]
    print(f"\n📖 Story: {title}")
    print(f"📝 Using short narration: {len(short_text)} chars (~30s)")

    # ── Step 1: Generate short narration audio ──────────────
    print("\n" + "=" * 60)
    print("STEP 1/5: Generate TTS Audio")
    print("=" * 60)

    audio_dir = TEST_DIR / "audio"
    audio_dir.mkdir(exist_ok=True)
    narration_audio = audio_dir / "narration.mp3"

    from ai_mystery_story.infrastructure.tts.edge_tts_provider import EdgeTTSProvider
    provider = EdgeTTSProvider(
        voice="vi-VN-NamMinhNeural",
        rate="-7%",
        pitch="-2Hz",
        max_chars=500,
    )

    start = time.time()
    duration = provider.generate(text=short_text, output_path=narration_audio)
    print(f"  ✅ Narration audio: {duration:.1f}s ({time.time()-start:.1f}s)")

    # ── Step 2: Generate Background Music ───────────────────
    print("\n" + "=" * 60)
    print("STEP 2/5: Generate Background Music")
    print("=" * 60)

    from ai_mystery_story.infrastructure.audio.background_music_provider import (
        BackgroundMusicProvider, AudioMixer,
    )

    bgm_provider = BackgroundMusicProvider(volume=0.12, fade_in_seconds=2.0, fade_out_seconds=3.0)
    bgm_path = audio_dir / "bgm.mp3"

    start = time.time()
    bgm_provider.get_music(duration_seconds=duration, output_path=bgm_path)
    print(f"  ✅ BGM generated ({time.time()-start:.1f}s)")

    # ── Step 3: Mix Audio ──────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 3/5: Mix Narration + BGM")
    print("=" * 60)

    mixer = AudioMixer(narration_volume=1.0, music_volume=1.0)
    mixed_audio = audio_dir / "mixed.mp3"

    start = time.time()
    mix_duration = mixer.mix(narration_audio, bgm_path, mixed_audio)
    print(f"  ✅ Mixed audio: {mix_duration:.1f}s ({time.time()-start:.1f}s)")

    # ── Step 4: Create Video with Transitions ──────────────
    print("\n" + "=" * 60)
    print("STEP 4/5: Create Video Segments + Transitions")
    print("=" * 60)

    images_dir = project_dir / "images"
    img1 = images_dir / "beat_01.png"
    img2 = images_dir / "beat_02.png"

    seg1 = TEST_DIR / "seg1.mp4"
    seg2 = TEST_DIR / "seg2.mp4"
    seg3 = TEST_DIR / "seg3.mp4"

    # Create 3 segments (10s each)
    seg_duration = duration / 3
    scale = f"scale={VIDEO_WIDTH*2}:{VIDEO_HEIGHT*2}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,format=yuv420p"

    for i, (img, seg) in enumerate([(img1, seg1), (img2, seg2), (img1, seg3)]):
        print(f"  [{i+1}/3] Creating segment {i+1} ({seg_duration:.1f}s)...")
        subprocess.run([
            str(FFMPEG), "-y",
            "-loop", "1", "-i", str(img),
            "-t", str(seg_duration),
            "-vf", scale,
            "-r", str(FPS), "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(seg),
        ], capture_output=True, check=True)

    # Apply transitions
    print("  Applying dissolve transitions...")
    from ai_mystery_story.infrastructure.video.video_transitions import apply_xfade_transitions

    silent_video = TEST_DIR / "silent.mp4"
    start = time.time()
    apply_xfade_transitions(
        segment_files=[seg1, seg2, seg3],
        output_path=silent_video,
        transition="dissolve",
        transition_duration=0.8,
        fade_in_duration=1.0,
        fade_out_duration=1.5,
        fps=FPS,
    )
    print(f"  ✅ Video with transitions ({time.time()-start:.1f}s)")

    # ── Step 5: Add Audio + Subtitles ──────────────────────
    print("\n" + "=" * 60)
    print("STEP 5/5: Add Audio + Subtitles")
    print("=" * 60)

    # Combine video + audio
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
    print("  ✅ Video + Audio combined")

    # Generate subtitles
    from ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
        SubtitleGenerator, burn_subtitles_into_video,
    )

    sub_gen = SubtitleGenerator(max_chars_per_line=40, max_lines=2)
    srt_path = TEST_DIR / "subtitles.srt"

    # Split short text into timed segments
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?。！？])\s+', short_text) if s.strip()]
    total_chars = sum(len(s) for s in sentences)
    current_time = 0.0
    seg_dicts = []
    for s in sentences:
        seg_dur = duration * len(s) / total_chars
        seg_dicts.append({"text": s, "start_time": current_time, "end_time": current_time + seg_dur})
        current_time += seg_dur

    sub_gen.generate_from_segments(seg_dicts, srt_path)
    print(f"  ✅ Subtitles generated ({len(seg_dicts)} entries)")

    # Burn subtitles
    final_output = TEST_DIR / "final_video.mp4"
    start = time.time()
    burn_subtitles_into_video(video_with_audio, srt_path, final_output, font_size=22)
    print(f"  ✅ Subtitles burned ({time.time()-start:.1f}s)")

    # ── Cleanup ─────────────────────────────────────────────
    for f in [seg1, seg2, seg3, silent_video, video_with_audio, srt_path]:
        f.unlink(missing_ok=True)

    # ── Results ─────────────────────────────────────────────
    print()
    print("=" * 60)
    print("🎉 PIPELINE HOÀN THÀNH!")
    print("=" * 60)

    if final_output.exists():
        size_mb = final_output.stat().st_size / (1024 * 1024)
        final_dur = get_duration(final_output)
        print(f"\n📁 Output: {final_output}")
        print(f"⏱️ Duration: {final_dur:.1f}s")
        print(f"💾 Size: {size_mb:.1f} MB")
        print()
        print("✨ Tính năng đã test:")
        print("  ✅ 🎵 Background Music (ambient mystery)")
        print("  ✅ 📝 Subtitles (burned into video)")
        print("  ✨ ✨ Transitions (dissolve between scenes)")
        print()
        print("👉 Mở file để xem kết quả:")
        print(f"   open {final_output}")
    else:
        print("❌ Output not found!")


if __name__ == "__main__":
    main()
