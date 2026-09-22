"""
🎬 Tạo Video - Tạo video từ audio + hình ảnh (Beat-based sync)
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components.theme import inject_theme, render_step_indicator

st.set_page_config(
    page_title="Tạo Video - AI Mystery",
    page_icon="🎬",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">🎬 Tạo Video</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Tự động render video từ audio MP3 và ảnh minh họa (Beat-based sync)</div>', unsafe_allow_html=True)

render_step_indicator(5, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Config ───────────────────────────────────────────────────
PROJECTS_DIR = PROJECT_ROOT / "projects"
FFMPEG = PROJECT_ROOT / "bin" / "ffmpeg"
FFPROBE = PROJECT_ROOT / "bin" / "ffprobe"

VIDEO_CONFIG = {
    "fast": {
        "output_name": "final_fast.mp4",
        "preset": "ultrafast",
        "crf": "28",
        "label": "⚡ Nhanh (ultrafast - CRF 28)",
    },
    "quality": {
        "output_name": "final_quality.mp4",
        "preset": "medium",
        "crf": "20",
        "label": "🎬 Chất lượng cao (medium - CRF 20)",
    },
}

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 30

# Transition options
TRANSITIONS = {
    "dissolve": "✨ Dissolve (Mờ dần)",
    "fade": "🌅 Fade (Đen mờ)",
    "fadeblack": "⬛ Fade Black (Qua đen)",
    "fadewhite": "⬜ Fade White (Qua trắng)",
    "wipeleft": "➡️ Wipe Left (Trượt trái)",
    "wiperight": "⬅️ Wipe Right (Trượt phải)",
    "slideleft": "→ Slide Left",
    "slideright": "← Slide Right",
    "circlecrop": "⭕ Circle Crop",
    "pixelize": "🎲 Pixelize",
    "radial": "💫 Radial",
    "smoothleft": "🌊 Smooth Left",
}


# ── Helper functions ─────────────────────────────────────────
def get_audio_duration(audio_file: Path) -> float:
    result = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def load_topics():
    topics_file = PROJECT_ROOT / "data" / "mystery_topics.json"
    if not topics_file.exists():
        return []
    data = json.loads(topics_file.read_text(encoding="utf-8"))
    return data.get("topics", [])


def load_story(topic_id):
    story_file = PROJECTS_DIR / topic_id / "story.json"
    if not story_file.exists():
        return None
    from ai_mystery_story.infrastructure.serializers.story_serializer import (
        StorySerializer,
    )
    data = json.loads(story_file.read_text(encoding="utf-8"))
    return StorySerializer.from_dict(data)


# ── Load topics ──────────────────────────────────────────────
topics = load_topics()
generated_topics = [t for t in topics if t.get("status") == "generated"]

if not generated_topics:
    st.warning("Chưa có story nào trong hệ thống.", icon="⚠️")
    st.page_link("pages/2_✍️_Tạo Story.py", label="✍️ Tạo Story")
    st.stop()

# ── Select topic ─────────────────────────────────────────────
query_params = st.query_params
default_topic = query_params.get("topic_id", None)

topic_options = {t["id"]: t["title"] for t in generated_topics}
topic_ids = list(topic_options.keys())
topic_labels = [f"[{tid}] {topic_options[tid]}" for tid in topic_ids]

if default_topic and default_topic in topic_ids:
    default_index = topic_ids.index(default_topic)
else:
    default_index = 0

with st.container(border=True):
    selected_idx = st.selectbox(
        "🎯 Chọn Story để Render Video",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = generated_topics[selected_idx]
topic_id = selected_topic["id"]
story = load_story(topic_id)

# ── Check requirements ───────────────────────────────────────
project_dir = PROJECTS_DIR / topic_id
audio_file = project_dir / "audio" / "narration_full.mp3"
# Also check for mixed audio with music
audio_with_music = project_dir / "audio" / "narration_with_music.mp3"
images_dir = project_dir / "images"
video_dir = project_dir / "video"

st.write("")
st.subheader(f"📖 {selected_topic['title']}", divider="red")

# Check audio - prefer mixed audio if available
has_audio = False
if audio_with_music.exists():
    audio_file = audio_with_music
    has_audio = True
elif audio_file.exists():
    has_audio = True
else:
    audio_dir = project_dir / "audio"
    if audio_dir.exists():
        mp3_files = list(audio_dir.glob("*.mp3"))
        if mp3_files:
            audio_file = mp3_files[0]
            has_audio = True

# Check images
has_images = (
    images_dir.exists() and any(images_dir.iterdir())
    if images_dir.exists()
    else False
)

# Check existing video
has_video = False
existing_video = None
if video_dir.exists():
    mp4_files = list(video_dir.glob("*.mp4"))
    if mp4_files:
        has_video = True
        existing_video = mp4_files[0]

# ── Status Dashboard ─────────────────────────────────────────
col1, col2, col3 = st.columns(3)

with col1:
    with st.container(border=True):
        if has_audio:
            st.metric("🔊 Audio MP3", "✅ Ready")
            st.caption(f"File: `{audio_file.name}`")
        else:
            st.metric("🔊 Audio MP3", "❌ Unset")
            st.caption("Chưa tìm thấy file audio")

with col2:
    with st.container(border=True):
        if has_images:
            image_count = len(list(images_dir.glob("*")))
            st.metric("🖼️ Ảnh minh họa", f"✅ {image_count} Ảnh")
            st.caption("Thư mục `images/` đã sẵn sàng")
        else:
            st.metric("🖼️ Ảnh minh họa", "❌ Missing")
            st.caption("Thiếu ảnh trong thư mục images/")

with col3:
    with st.container(border=True):
        if has_video:
            st.metric("🎬 Video Output", "✅ Exists")
            st.caption(f"File: `{existing_video.name}`")
        else:
            st.metric("🎬 Video Output", "⏳ Not Ready")
            st.caption("Chưa xuất file video")

# ── Hành động bổ sung ────────────────────────────────────────
if not has_audio:
    st.warning("Cần có file audio MP3 để tạo video.", icon="⚠️")
    col_action1, col_action2, _ = st.columns([1, 2, 1])
    with col_action1:
        st.page_link("pages/6_🔊_Audio.py", label="🔊 Đến trang Tạo Audio", use_container_width=True)

st.write("")

# ── Video settings & Generator ───────────────────────────────
if has_audio and has_images:
    st.subheader("⚙️ Cấu hình xuất Video", divider="gray")

    # Load story to get beat durations
    beat_durations = []
    if story and story.blueprint.beats:
        for beat in story.blueprint.beats:
            beat_durations.append({
                "order": beat.order,
                "title": beat.title,
                "duration_minutes": beat.target_duration_minutes,
                "duration_seconds": beat.target_duration_minutes * 60,
            })

    with st.container(border=True):
        col1, col2 = st.columns(2)

        with col1:
            mode = st.selectbox(
                "🚀 Chế độ Render (Preset)",
                ["fast", "quality"],
                format_func=lambda x: VIDEO_CONFIG[x]["label"],
            )

        with col2:
            st.markdown(f"• **Độ phân giải:** `{VIDEO_WIDTH}x{VIDEO_HEIGHT}` (FullHD)")
            st.markdown(f"• **Tốc độ khung hình:** `{FPS} FPS`")

        # Read audio stats
        try:
            duration = get_audio_duration(audio_file)

            s1, s2 = st.columns(2)
            s1.info(f"⏱️ **Thời lượng Audio:** {format_time(duration)}")

            if beat_durations:
                s2.info(f"🧩 **Số Beat:** {len(beat_durations)} beats")
            else:
                total_segments = int((duration + 7 - 0.001) // 7)
                s2.info(f"🧩 **Tổng số phân đoạn Video:** {total_segments} segments")

        except Exception as e:
            st.warning(f"Không thể đọc thời lượng audio: {e}", icon="⚠️")
            duration = 0

    st.write("")

    # ── ✨ Transitions Settings ──────────────────────────────
    st.subheader("✨ Hiệu ứng Chuyển cảnh (Transitions)", divider="blue")

    with st.container(border=True):
        enable_transitions = st.checkbox(
            "✨ Bật chuyển cảnh mượt mà",
            value=True,
            help="Thêm hiệu ứng cross-dissolve giữa các scene"
        )

        if enable_transitions:
            col_t1, col_t2 = st.columns(2)

            with col_t1:
                transition_type = st.selectbox(
                    "🎬 Loại chuyển cảnh",
                    list(TRANSITIONS.keys()),
                    format_func=lambda x: TRANSITIONS[x],
                    index=0,
                    help="Chọn hiệu ứng chuyển cảnh giữa các beat"
                )

            with col_t2:
                transition_duration = st.slider(
                    "⏱️ Thời gian chuyển cảnh (giây)",
                    min_value=0.5,
                    max_value=3.0,
                    value=1.0,
                    step=0.1,
                    help="Thời gian thực hiện hiệu ứng chuyển cảnh"
                )

            st.info(f"💡 Chuyển cảnh sẽ áp dụng giữa mỗi beat. Hiệu ứng: **{TRANSITIONS[transition_type]}**", icon="💡")
        else:
            transition_type = "dissolve"
            transition_duration = 1.0

    st.write("")

    # ── 📝 Subtitles Settings ────────────────────────────────
    st.subheader("📝 Cấu hình Phụ đề (Subtitles)", divider="blue")

    with st.container(border=True):
        enable_subtitles = st.checkbox(
            "📝 Bật phụ đề tự động",
            value=True,
            help="Hiển thị lời thuyết minh trên video"
        )

        subtitle_timestamps = None
        subtitle_text = None

        if enable_subtitles:
            # Priority 1: Check for real timestamps (from Edge TTS)
            timestamps_file = project_dir / "audio" / "timestamps.json"

            if timestamps_file.exists():
                ts_data = json.loads(timestamps_file.read_text(encoding="utf-8"))
                subtitle_timestamps = ts_data.get("words", [])
                if subtitle_timestamps:
                    st.success(f"✅ Tìm thấy {len(subtitle_timestamps)} word timestamps (đồng bộ chính xác!)", icon="📝")
                    with st.expander("👁️ Xem trước timestamps"):
                        for w in subtitle_timestamps[:10]:
                            st.text(f"{w['start']:.2f}s - {w['end']:.2f}s: {w['text']}")
                        if len(subtitle_timestamps) > 10:
                            st.text(f"... ({len(subtitle_timestamps) - 10} more words)")
            else:
                # Priority 2: Load narration text (proportional timing)
                st.info("💡 Không tìm thấy timestamps. Phụ đề sẽ dùng proportional timing.", icon="ℹ️")
                narration_file = project_dir / "narration_full.txt"
                if narration_file.exists():
                    subtitle_text = narration_file.read_text(encoding="utf-8")
                else:
                    story_file = project_dir / "story.json"
                    if story_file.exists():
                        story_data = json.loads(story_file.read_text(encoding="utf-8"))
                        narration = story_data.get("narration", {})
                        segments = narration.get("segments", [])
                        subtitle_text = "\n\n".join(s.get("text", "") for s in segments)

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                subtitle_position = st.selectbox(
                    "📍 Vị trí phụ đề",
                    ["bottom", "center", "top"],
                    format_func=lambda x: {
                        "bottom": "👇 Dưới cùng",
                        "center": "🎯 Giữa màn hình",
                        "top": "⬆️ Trên cùng",
                    }[x],
                    index=0,
                )
            with col_s2:
                subtitle_font_size = st.slider(
                    "🔤 Cỡ chữ phụ đề",
                    min_value=16,
                    max_value=36,
                    value=22,
                    step=2,
                )
        else:
            subtitle_text = None
            subtitle_timestamps = None
            subtitle_position = "bottom"
            subtitle_font_size = 22

    st.write("")

    # ── Show beat-image mapping ──────────────────────────────
    if beat_durations:
        st.subheader("📋 Beat → Ảnh Mapping", divider="blue")

        with st.container(border=True):
            st.info("Mỗi Beat sẽ hiển thị 1 ảnh với duration = duration Beat. Ảnh sync với nội dung audio.", icon="ℹ️")

            # Load images
            images = sorted([
                path
                for path in images_dir.iterdir()
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ])

            for beat in beat_durations:
                beat_order = beat["order"]
                # Find matching image
                beat_image = None
                for img in images:
                    if f"beat_{beat_order:02d}" in img.name:
                        beat_image = img
                        break

                if beat_image:
                    cols = st.columns([1, 3])
                    with cols[0]:
                        st.image(str(beat_image), caption=beat_image.name, width=200)
                    with cols[1]:
                        st.markdown(f"**Beat {beat_order}:** {beat['title']}")
                        st.markdown(f"**Duration:** {beat['duration_minutes']} phút ({beat['duration_seconds']:.0f}s)")
                else:
                    st.warning(f"Beat {beat_order}: Không tìm thấy ảnh `beat_{beat_order:02d}.jpg`")

    st.write("")
    st.subheader("🚀 Bắt đầu Tiến trình Render", divider="blue")

    with st.container(border=True):
        if st.button("✨ Bắt đầu Render Video FFmpeg", type="primary", use_container_width=True):
            progress_bar = st.progress(0, text="Đang khởi tạo các thông số...")
            log_container = st.empty()
            logs = []

            def add_log(msg):
                logs.append(msg)
                log_container.code("\n".join(logs[-15:]), language=None)

            try:
                images = sorted([
                    path
                    for path in images_dir.iterdir()
                    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ])

                if not images:
                    st.error("Không tìm thấy định dạng ảnh hợp lệ trong thư mục images!", icon="🚨")
                    st.stop()

                output_file = video_dir / VIDEO_CONFIG[mode]["output_name"]
                video_dir.mkdir(parents=True, exist_ok=True)

                add_log(f"🚀 Bắt đầu xuất video chế độ: {mode}")
                add_log(f"📁 Tổng số ảnh nạp vào: {len(images)}")
                add_log(f"🎵 Tổng thời lượng Audio: {format_time(duration)}")
                add_log(f"✨ Transitions: {'Bật' if enable_transitions else 'Tắt'}")
                add_log(f"📝 Subtitles: {'Bật' if enable_subtitles else 'Tắt'}")

                temp_dir = video_dir / "_segments"
                temp_dir.mkdir(exist_ok=True)

                concat_file = video_dir / "_concat.txt"
                silent_video = video_dir / "_silent.mp4"
                segment_files = []

                # Build beat-image mapping
                if beat_durations:
                    # Beat-based rendering
                    total_beats = len(beat_durations)

                    for beat_idx, beat in enumerate(beat_durations):
                        beat_order = beat["order"]
                        beat_duration = beat["duration_seconds"]

                        # Find matching image
                        beat_image = None
                        for img in images:
                            if f"beat_{beat_order:02d}" in img.name:
                                beat_image = img
                                break

                        if beat_image is None:
                            # Fallback to sequential image
                            beat_image = images[beat_idx % len(images)]

                        # Limit segment duration to avoid too long segments
                        # Split long beats into multiple segments of max 10s each
                        max_segment_duration = 10
                        num_sub_segments = max(1, int(beat_duration / max_segment_duration))
                        sub_segment_duration = beat_duration / num_sub_segments

                        for sub_idx in range(num_sub_segments):
                            remaining = beat_duration - sub_idx * sub_segment_duration
                            segment_duration = min(sub_segment_duration, remaining)

                            if segment_duration <= 0:
                                break

                            segment_file = temp_dir / f"segment_{len(segment_files):04d}.mp4"
                            segment_files.append(segment_file)

                            progress = 10 + int(60 * (len(segment_files) / (total_beats * 2)))
                            progress_bar.progress(
                                progress,
                                text=f"🎬 Đang xử lý Beat {beat_order}/{total_beats} (part {sub_idx + 1}/{num_sub_segments})...",
                            )
                            add_log(f"[Beat {beat_order}] Processing {beat_image.name} ({segment_duration:.2f}s)")

                            # Render segment
                            scale_filter = (
                                f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:"
                                "force_original_aspect_ratio=increase"
                            )

                            movement = (beat_idx + sub_idx) % 4
                            duration_expr = f"{segment_duration:.6f}"

                            if movement == 0:
                                crop_filter = (
                                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                    f"(in_w-out_w)/2:"
                                    f"(in_h-out_h)*t/{duration_expr}"
                                )
                            elif movement == 1:
                                crop_filter = (
                                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                    f"(in_w-out_w)/2:"
                                    f"(in_h-out_h)*(1-t/{duration_expr})"
                                )
                            elif movement == 2:
                                crop_filter = (
                                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                    f"(in_w-out_w)*t/{duration_expr}:"
                                    f"(in_h-out_h)/2"
                                )
                            else:
                                crop_filter = (
                                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                    f"(in_w-out_w)*(1-t/{duration_expr}):"
                                    f"(in_h-out_h)/2"
                                )

                            firelight_filter = (
                                "eq=brightness='"
                                "0.015"
                                "+0.020*sin(2*PI*t/3.7)"
                                "+0.012*sin(2*PI*t/1.9)"
                                "+0.008*sin(2*PI*t/5.3)"
                                "'"
                            )

                            filter_complex = (
                                f"{scale_filter},"
                                f"{crop_filter},"
                                f"{firelight_filter},"
                                "setsar=1,"
                                "format=yuv420p"
                            )

                            command = [
                                str(FFMPEG),
                                "-y",
                                "-loop",
                                "1",
                                "-i",
                                str(beat_image),
                                "-t",
                                str(segment_duration),
                                "-vf",
                                filter_complex,
                                "-r",
                                str(FPS),
                                "-an",
                                "-c:v",
                                "libx264",
                                "-preset",
                                VIDEO_CONFIG[mode]["preset"],
                                "-crf",
                                VIDEO_CONFIG[mode]["crf"],
                                "-pix_fmt",
                                "yuv420p",
                                str(segment_file),
                            ]

                            result = subprocess.run(
                                command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                            )

                            if result.returncode != 0:
                                add_log(f"❌ FFmpeg error: {result.stderr[-200:]}")
                                raise RuntimeError(f"FFmpeg failed for beat {beat_order}")

                else:
                    # Legacy fixed-duration rendering (fallback)
                    IMAGE_DURATION = 7
                    total_segments = int((duration + IMAGE_DURATION - 0.001) // IMAGE_DURATION)

                    for index in range(total_segments):
                        image = images[index % len(images)]
                        segment_start = index * IMAGE_DURATION
                        remaining = duration - segment_start
                        segment_duration = min(IMAGE_DURATION, remaining)

                        if segment_duration <= 0:
                            break

                        segment_file = temp_dir / f"segment_{index:04d}.mp4"
                        segment_files.append(segment_file)

                        progress = 10 + int(60 * (index / total_segments))
                        progress_bar.progress(
                            progress,
                            text=f"🎬 Đang xử lý Segment {index + 1}/{total_segments}...",
                        )
                        add_log(f"[{index + 1}/{total_segments}] Processing {image.name} ({segment_duration:.2f}s)")

                        # Render segment (same as above)
                        scale_filter = (
                            f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:"
                            "force_original_aspect_ratio=increase"
                        )

                        movement = index % 4
                        duration_expr = f"{segment_duration:.6f}"

                        if movement == 0:
                            crop_filter = (
                                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                f"(in_w-out_w)/2:"
                                f"(in_h-out_h)*t/{duration_expr}"
                            )
                        elif movement == 1:
                            crop_filter = (
                                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                f"(in_w-out_w)/2:"
                                f"(in_h-out_h)*(1-t/{duration_expr})"
                            )
                        elif movement == 2:
                            crop_filter = (
                                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                f"(in_w-out_w)*t/{duration_expr}:"
                                f"(in_h-out_h)/2"
                            )
                        else:
                            crop_filter = (
                                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                                f"(in_w-out_w)*(1-t/{duration_expr}):"
                                f"(in_h-out_h)/2"
                            )

                        firelight_filter = (
                            "eq=brightness='"
                            "0.015"
                            "+0.020*sin(2*PI*t/3.7)"
                            "+0.012*sin(2*PI*t/1.9)"
                            "+0.008*sin(2*PI*t/5.3)"
                            "'"
                        )

                        filter_complex = (
                            f"{scale_filter},"
                            f"{crop_filter},"
                            f"{firelight_filter},"
                            "setsar=1,"
                            "format=yuv420p"
                        )

                        command = [
                            str(FFMPEG),
                            "-y",
                            "-loop",
                            "1",
                            "-i",
                            str(image),
                            "-t",
                            str(segment_duration),
                            "-vf",
                            filter_complex,
                            "-r",
                            str(FPS),
                            "-an",
                            "-c:v",
                            "libx264",
                            "-preset",
                            VIDEO_CONFIG[mode]["preset"],
                            "-crf",
                            VIDEO_CONFIG[mode]["crf"],
                            "-pix_fmt",
                            "yuv420p",
                            str(segment_file),
                        ]

                        result = subprocess.run(
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        if result.returncode != 0:
                            add_log(f"❌ FFmpeg error: {result.stderr[-200:]}")
                            raise RuntimeError(f"FFmpeg failed for segment {index + 1}")

                progress_bar.progress(75, text="🔗 Đang nối các segment video thành chuỗi...")
                add_log("🔗 Joining video segments...")

                if enable_transitions and len(segment_files) > 1:
                    # Use xfade transitions
                    add_log(f"✨ Applying {transition_type} transitions...")
                    from src.ai_mystery_story.infrastructure.video.video_transitions import (
                        apply_xfade_transitions,
                    )

                    apply_xfade_transitions(
                        segment_files=segment_files,
                        output_path=silent_video,
                        transition=transition_type,
                        transition_duration=transition_duration,
                        fade_in_duration=1.5,
                        fade_out_duration=2.0,
                        fps=FPS,
                    )
                else:
                    # Simple concat
                    with concat_file.open("w", encoding="utf-8") as f:
                        for segment in segment_files:
                            f.write(f"file '{segment.resolve()}'\n")

                    subprocess.run(
                        [
                            str(FFMPEG),
                            "-y",
                            "-f",
                            "concat",
                            "-safe",
                            "0",
                            "-i",
                            str(concat_file),
                            "-c",
                            "copy",
                            "-movflags",
                            "+faststart",
                            str(silent_video),
                        ],
                        check=True,
                        capture_output=True,
                    )

                progress_bar.progress(88, text="🎵 Đang ghép Voice Audio MP3 vào Video...")
                add_log("🎵 Adding audio track...")

                subprocess.run(
                    [
                        str(FFMPEG),
                        "-y",
                        "-i",
                        str(silent_video),
                        "-i",
                        str(audio_file),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-t",
                        str(duration),
                        "-shortest",
                        "-movflags",
                        "+faststart",
                        str(output_file),
                    ],
                    check=True,
                    capture_output=True,
                )

                # ── Burn subtitles ───────────────────────────
                if enable_subtitles and (subtitle_timestamps or subtitle_text):
                    progress_bar.progress(92, text="📝 Đang thêm phụ đề...")
                    add_log("📝 Burning subtitles into video...")

                    from src.ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
                        SubtitleGenerator,
                        burn_subtitles_into_video,
                    )

                    # Generate SRT
                    srt_path = video_dir / "_subtitles.srt"
                    subtitle_gen = SubtitleGenerator(
                        max_chars_per_line=42,
                        max_lines=2,
                    )

                    if subtitle_timestamps:
                        # Use real timestamps from TTS
                        add_log("  Using word-level timestamps (accurate sync)")
                        subtitle_gen.generate_from_word_timestamps(
                            words=subtitle_timestamps,
                            output_path=srt_path,
                        )
                    else:
                        # Fallback to proportional timing
                        add_log("  Using proportional timing (no timestamps)")
                        import re
                        paragraphs = [
                            p.strip()
                            for p in subtitle_text.split("\n\n")
                            if p.strip()
                        ]
                        if len(paragraphs) <= 1:
                            paragraphs = re.split(r'(?<=[.!?。！？])\s+', subtitle_text.strip())
                            paragraphs = [p.strip() for p in paragraphs if p.strip()]

                        total_chars = sum(len(p) for p in paragraphs)
                        if total_chars > 0:
                            current_time = 0.0
                            segment_dicts = []
                            for para in paragraphs:
                                seg_duration = duration * len(para) / total_chars
                                segment_dicts.append({
                                    "text": para,
                                    "start_time": current_time,
                                    "end_time": current_time + seg_duration,
                                })
                                current_time += seg_duration

                            subtitle_gen.generate_from_segments(
                                segments=segment_dicts,
                                output_path=srt_path,
                            )

                    # Burn subtitles
                    if srt_path.exists():
                        temp_with_subs = video_dir / "_temp_with_subs.mp4"
                        burn_subtitles_into_video(
                            video_path=output_file,
                            srt_path=srt_path,
                            output_path=temp_with_subs,
                            font_size=subtitle_font_size,
                            position=subtitle_position,
                        )

                        # Replace original with subtitled version
                        temp_with_subs.replace(output_file)
                        srt_path.unlink(missing_ok=True)

                progress_bar.progress(95, text="🗑️ Đang xoá các file tạm (temporary files)...")
                add_log("🗑️ Cleaning up temp files...")

                for segment in segment_files:
                    segment.unlink(missing_ok=True)
                concat_file.unlink(missing_ok=True)
                silent_video.unlink(missing_ok=True)
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass

                progress_bar.progress(100, text="✅ Xuất video thành công!")
                add_log("=" * 40)
                add_log(f"🎉 Video saved: {output_file}")

                st.success(f"Video đã tạo thành công: `{output_file.name}`", icon="🎉")
                st.rerun()

            except Exception as e:
                progress_bar.progress(100, text="❌ Thất bại!")
                add_log(f"❌ Error: {e}")
                st.error(f"Xảy ra lỗi trong quá trình render video: {e}", icon="🚨")
                st.exception(e)

else:
    if not has_audio:
        st.warning("Cần Audio MP3 trước khi tạo Video.", icon="⚠️")
    if not has_images:
        st.warning("Cần ảnh minh họa trước khi tạo Video.", icon="⚠️")

# ── Existing video ───────────────────────────────────────────
if has_video:
    st.write("")
    st.subheader("🎬 Video hiện có", divider="gray")
    with st.container(border=True):
        st.video(str(existing_video))
        st.caption(f"File: `{existing_video.name}` | Size: {existing_video.stat().st_size // (1024*1024)}MB")

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)
with nav_col1:
    st.page_link("pages/7_🖼️_Tạo Ảnh.py", label="← Quay lại Tạo Ảnh", use_container_width=True)
with nav_col2:
    st.page_link("pages/1_📋_Topics.py", label="📋 Quay về Topics", use_container_width=True)
