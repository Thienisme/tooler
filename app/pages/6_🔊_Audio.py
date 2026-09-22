"""
🔊 Audio - Tạo và phát audio từ narration
"""

import json
import os
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
    page_title="Audio - AI Mystery",
    page_icon="🔊",
    layout="wide",
)

inject_theme()


@st.cache_resource(show_spinner="Đang tải model VieNeu-TTS (lần đầu ~282MB)...")
def get_vieneu_engine():
    """Cache the heavy local model across Streamlit reruns."""
    from vieneu import Vieneu

    return Vieneu()


def make_vieneu_provider(voice: str, speed: float):
    """Build a lightweight provider around the shared cached engine."""
    from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
        VieNeuTTSProvider,
    )

    return VieNeuTTSProvider(
        voice=voice,
        speed=speed,
        engine=get_vieneu_engine(),
    )


# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">🔊 Tạo Audio</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Chuyển đổi lời thuyết minh thành file MP3 bằng Edge TTS</div>', unsafe_allow_html=True)

render_step_indicator(3, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Load story ───────────────────────────────────────────────
PROJECTS_DIR = PROJECT_ROOT / "projects"


def load_story(topic_id):
    story_file = PROJECTS_DIR / topic_id / "story.json"
    if not story_file.exists():
        return None
    from ai_mystery_story.infrastructure.serializers.story_serializer import (
        StorySerializer,
    )

    data = json.loads(story_file.read_text(encoding="utf-8"))
    return StorySerializer.from_dict(data)


def load_topics():
    topics_file = PROJECT_ROOT / "data" / "mystery_topics.json"
    if not topics_file.exists():
        return []
    data = json.loads(topics_file.read_text(encoding="utf-8"))
    return data.get("topics", [])


topics = load_topics()
generated_topics = [t for t in topics if t.get("status") == "generated"]

if not generated_topics:
    st.warning("Chưa có story nào trong hệ thống. Hãy tạo story trước!", icon="⚠️")
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
        "🎯 Chọn Story để chuyển đổi Voice Audio",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = generated_topics[selected_idx]
story = load_story(selected_topic["id"])

if story is None:
    st.error("Không tìm thấy dữ liệu story của dự án này.", icon="🚨")
    st.stop()

# ── Check narration guardrail ─────────────────────────────────
if story.narration is None:
    st.warning("Story này chưa được khởi tạo lời thuyết minh (Narration).", icon="⚠️")
    st.page_link(
        "pages/4_🎙️_Narration.py",
        label="🎙️ Tạo Narration",
    )
    st.stop()

# ── Story Overview Dashboard ──────────────────────────────────
st.write("")
st.subheader(f"📖 {story.blueprint.title}", divider="red")

m1, m2, m3 = st.columns(3)
with m1:
    with st.container(border=True):
        st.metric("🧩 Thuyết minh Segments", len(story.narration.segments))
with m2:
    with st.container(border=True):
        st.metric("📝 Độ dài bài viết", f"{len(story.narration.full_text()):,} ký tự")
with m3:
    with st.container(border=True):
        audio_dir = PROJECTS_DIR / selected_topic["id"] / "audio"
        full_audio = audio_dir / "narration_full.mp3"
        # Also check old naming
        if not full_audio.exists():
            full_audio = audio_dir / "full.mp3"
        st.metric("🔊 Trạng thái Audio", "✅ Đã tạo" if full_audio.exists() else "⏳ Chưa tạo")

st.write("")

# ── Voice Settings Section ───────────────────────────────────
st.subheader("⚙️ Cấu hình Giọng đọc (TTS Settings)", divider="gray")

with st.container(border=True):
    tts_engine = st.radio(
        "Chọn Engine TTS",
        [
            "Edge TTS (Mặc định - nhanh, dấu chuẩn)",
            "VieNeu-TTS (Chất lượng cao - chạy local)",
            "Fish Speech (Truyền cảm - cần API key)",
        ],
        horizontal=True,
    )

    if "Edge TTS" in tts_engine:
        col1, col2, col3 = st.columns(3)
        with col1:
            voice = st.selectbox(
                "🗣️ Giọng đọc (Voice)",
                ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"],
                index=0 if os.getenv("EDGE_TTS_VOICE", "vi-VN-NamMinhNeural") == "vi-VN-NamMinhNeural" else 1,
                help="Chọn giọng Nam Minh hoặc Hoài Mỹ từ Edge TTS",
            )
        with col2:
            rate = st.text_input(
                "⚡ Tốc độ đọc (Rate)",
                value=os.getenv("EDGE_TTS_RATE", "-7%"),
                help="Ví dụ: -5%, +0%, +10%",
            )
        with col3:
            pitch = st.text_input(
                "🎵 Tông giọng (Pitch)",
                value=os.getenv("EDGE_TTS_PITCH", "-2Hz"),
                help="Ví dụ: -2Hz, +0Hz, +2Hz",
            )
    elif "VieNeu" in tts_engine:
        from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import VIENEU_VOICES
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            vieneu_voice = st.selectbox(
                "🎙️ Giọng Việt (23 giọng built-in)",
                list(VIENEU_VOICES.keys()),
                index=1,
                help="Giọng đọc truyện/kể chuyện 48kHz, dấu tiếng Việt chuẩn",
            )
        with col_v2:
            vieneu_speed = st.slider("⚡ Tốc độ", 0.5, 2.0, 1.0, 0.05)
        st.info(
            "✨ VieNeu chạy local (không tốn API, không giới hạn) nhưng chậm hơn Edge ~1-2x. "
            "Phụ đề cần timestamps sẽ được tạo bằng Edge TTS.",
            icon="⏳",
        )
    else:
        from ai_mystery_story.infrastructure.tts.fish_speech_provider import VIETNAMESE_VOICES
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            fish_api_key = st.text_input("🔑 Fish API Key", value=os.getenv("FISH_API_KEY", ""), type="password")
            fish_voice = st.selectbox("🎙️ Giọng Việt", list(VIETNAMESE_VOICES.keys()), index=0)
        with col_f2:
            fish_speed = st.slider("⚡ Tốc độ", 0.5, 2.0, 0.9, 0.1)
            fish_model = st.selectbox("🤖 Model", ["s2.1-pro-free", "s2.1-pro"], index=0)
        st.info("⚠️ Fish Speech có thể mất dấu tiếng Việt. Dùng Edge TTS nếu cần dấu chuẩn.", icon="⚠️")

st.write("")

# ── 🎵 Background Music Settings ─────────────────────────────
st.subheader("🎵 Cấu hình Nhạc Nền (Background Music)", divider="gray")

with st.container(border=True):
    enable_bgm = st.checkbox(
        "🔊 Bật nhạc nền",
        value=True,
        help="Thêm nhạc nềnambient/trinh thám để video thêm sinh động"
    )

    if enable_bgm:
        col_bgm1, col_bgm2 = st.columns(2)

        with col_bgm1:
            bgm_option = st.radio(
                "Nguồn nhạc nền",
                ["🎵 Tự động tạo (Ambient Mystery)", "📁 Upload file nhạc tùy chỉnh"],
                horizontal=True,
            )

            if "Upload" in bgm_option:
                uploaded_music = st.file_uploader(
                    "📁 Upload file nhạc nền (MP3, WAV)",
                    type=["mp3", "wav", "m4a", "ogg"],
                    help="Chọn file nhạc nền từ máy tính"
                )
            else:
                uploaded_music = None

        with col_bgm2:
            music_volume = st.slider(
                "🔊 Âm lượng nhạc nền",
                min_value=0.01,
                max_value=0.5,
                value=0.12,
                step=0.01,
                help="Âm lượng nhạc nền so với giọng đọc (khuyến nghị: 0.08 - 0.15)"
            )

            st.info(f"💡 Âm lượng hiện tại: {music_volume:.0%} - Nhạc nền sẽ tự động fade in/out", icon="💡")
    else:
        uploaded_music = None
        music_volume = 0.12

st.write("")

# ── Audio Status & Player ─────────────────────────────────────
is_regenerating = st.session_state.get("regenerate_audio", False)

if full_audio.exists() and not is_regenerating:
    with st.container(border=True):
        st.success("File Audio hợp nhất (Full Audio) đã sẵn sàng!", icon="✅")

        # Audio Info Stats
        from mutagen.mp3 import MP3

        audio_info = MP3(str(full_audio))
        duration_min = audio_info.info.length / 60
        chunks = sorted(audio_dir.glob("segment_*.mp3"))
        if not chunks:
            chunks = sorted(audio_dir.glob("chunk_*.mp3"))

        a1, a2, a3 = st.columns(3)
        with a1:
            with st.container(border=True):
                st.metric("⏱️ Thời lượng Audio", f"{duration_min:.2f} phút")
        with a2:
            with st.container(border=True):
                st.metric("📁 Số lượng Segments", f"{len(chunks)} files")
        with a3:
            with st.container(border=True):
                file_size_mb = os.path.getsize(str(full_audio)) / (1024 * 1024)
                st.metric("💾 Kích thước File", f"{file_size_mb:.2f} MB")

        st.write("")
        st.markdown("#### 🎧 Nghe trực tuyến (Full Audio MP3)")
        st.audio(str(full_audio), format="audio/mp3")

        # ── Mix Background Music ─────────────────────────────
        if enable_bgm:
            st.write("")
            st.markdown("#### 🎵 Ghép Nhạc Nền")

            mixed_audio_path = audio_dir / "narration_with_music.mp3"

            if mixed_audio_path.exists():
                st.success("✅ Audio đã ghép nhạc nền sẵn sàng!", icon="🎵")
                st.audio(str(mixed_audio_path), format="audio/mp3")

                if st.button("🔄 Tạo lại Audio + Nhạc nền", use_container_width=True):
                    st.session_state["mix_bgm"] = True
                    st.rerun()
            else:
                if st.button("🎵 Ghép Nhạc Nền", type="primary", use_container_width=True):
                    st.session_state["mix_bgm"] = True
                    st.rerun()

        st.write("")
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            if st.button("🔄 Tạo lại Audio (Ghi đè)", use_container_width=True):
                st.session_state["regenerate_audio"] = True
                st.rerun()
        with col_act2:
            st.page_link(
                "pages/11_🔄_Pipeline.py",
                label="🔄 Chạy Pipeline",
                use_container_width=True,
            )

else:
    if is_regenerating:
        st.warning("BẠN ĐANG TRONG CHẾ ĐỘ TẠO LẠI AUDIO. File audio cũ sẽ bị ghi đè.", icon="⚠️")

    with st.container(border=True):
        st.markdown("### 🚀 Tiến hành Chuyển đổi TTS & Ghép Audio")
        st.caption("Dịch vụ sẽ gửi request tới Edge TTS, tạo từng chunk âm thanh và dùng FFmpeg nối lại thành file mp3 hoàn chỉnh.")

        if st.button("✨ Bắt đầu tạo Audio", type="primary", use_container_width=True):
            progress_bar = st.progress(0, text="Đang khởi tạo...")

            try:
                from ai_mystery_story.application.audio.audio_generator import (
                    AudioGenerator,
                )
                from ai_mystery_story.application.audio_assembly_service import (
                    AudioAssemblyService,
                )
                from ai_mystery_story.application.tts_service import TTSService
                from ai_mystery_story.infrastructure.audio.ffmpeg_audio_assembler import (
                    FFmpegAudioAssembler,
                )
                from ai_mystery_story.infrastructure.repositories.json_story_repository import (
                    JsonStoryRepository,
                )
                from ai_mystery_story.infrastructure.tts.edge_tts_provider import (
                    EdgeTTSProvider,
                )

                progress_bar.progress(15, text="Đang cấu hình TTS Provider...")
                if "VieNeu" in tts_engine:
                    voice_name = VIENEU_VOICES[vieneu_voice]
                    provider = make_vieneu_provider(
                        voice=voice_name,
                        speed=vieneu_speed,
                    )
                elif "Edge TTS" in tts_engine:
                    # Use timestamped provider for accurate subtitle sync
                    from ai_mystery_story.infrastructure.tts.edge_tts_timestamped import (
                        EdgeTTSTimestamped,
                    )
                    timestamped_tts = EdgeTTSTimestamped(
                        voice=voice,
                        rate=rate,
                        pitch=pitch,
                    )
                    provider = EdgeTTSProvider(
                        voice=voice,
                        rate=rate,
                        pitch=pitch,
                    )
                else:
                    from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
                        FishSpeechProvider,
                    )
                    provider = FishSpeechProvider(
                        api_key=fish_api_key,
                        model=fish_model,
                        speed=fish_speed,
                        voice_name=fish_voice,
                    )

                tts_service = TTSService(provider=provider)
                audio_assembly_service = AudioAssemblyService(
                    assembler=FFmpegAudioAssembler(),
                )
                audio_generator = AudioGenerator(
                    tts_service=tts_service,
                    audio_assembly_service=audio_assembly_service,
                )

                progress_bar.progress(40, text="🗣️ Đang tạo các file audio từng segment...")
                audio = audio_generator.generate(
                    story=story,
                    output_dir=audio_dir,
                )

                # Generate timestamps for subtitle sync (Edge TTS only)
                if "Edge TTS" in tts_engine:
                    progress_bar.progress(70, text="📝 Đang tạo timestamps cho phụ đề...")
                    narration_text = story.narration.full_text()
                    timestamps_file = audio_dir / "timestamps.json"
                    try:
                        timestamped_tts.generate_with_timestamps(
                            text=narration_text,
                            audio_output=audio_dir / "_timestamps_audio.mp3",
                            timestamps_output=timestamps_file,
                        )
                        # Clean up temp audio
                        (audio_dir / "_timestamps_audio.mp3").unlink(missing_ok=True)
                        print(f"  ✅ Timestamps saved: {timestamps_file.name}")
                    except Exception as e:
                        print(f"  ⚠️ Timestamps generation failed: {e}")

                progress_bar.progress(85, text="💾 Đang tổng hợp file MP3 và lưu story...")
                repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
                repository.save(story, topic_id=selected_topic["id"])

                progress_bar.progress(100, text="✅ Hoàn tất!")

                st.session_state["regenerate_audio"] = False
                st.balloons()
                st.success("Audio đã được tạo và ghép nối thành công!", icon="🎉")

                st.rerun()

            except Exception as e:
                progress_bar.progress(100, text="❌ Thất bại!")
                st.error(f"Xảy ra lỗi trong quá trình xử lý Audio: {e}", icon="🚨")
                st.exception(e)


# ── Handle Background Music Mixing ───────────────────────────
if st.session_state.get("mix_bgm", False):
    st.session_state["mix_bgm"] = False

    with st.container(border=True):
        st.markdown("### 🎵 Đang ghép nhạc nền...")
        progress_bar = st.progress(0, text="Đang khởi tạo...")

        try:
            from src.ai_mystery_story.infrastructure.audio.background_music_provider import (
                BackgroundMusicProvider,
                AudioMixer,
            )

            progress_bar.progress(20, text="🎵 Đang xử lý nhạc nền...")

            # Determine music source
            music_path = None
            if enable_bgm and uploaded_music:
                # Save uploaded file temporarily
                temp_music_dir = audio_dir / "temp"
                temp_music_dir.mkdir(exist_ok=True)
                music_path = temp_music_dir / uploaded_music.name
                music_path.write_bytes(uploaded_music.read())

            # Create music provider
            music_provider = BackgroundMusicProvider(
                music_path=music_path,
                volume=music_volume,
            )

            # Get narration duration
            from mutagen.mp3 import MP3
            narration_duration = MP3(str(full_audio)).info.length

            progress_bar.progress(40, text="🎵 Đang tạo/process nhạc nền...")

            # Generate/process music
            processed_music = audio_dir / "_bgm_processed.mp3"
            music_provider.get_music(
                duration_seconds=narration_duration,
                output_path=processed_music,
            )

            progress_bar.progress(60, text="🎵 Đang ghép audio + nhạc nền...")

            # Mix audio
            mixer = AudioMixer(
                narration_volume=1.0,
                music_volume=1.0,  # Volume already applied
            )

            mixed_output = audio_dir / "narration_with_music.mp3"
            mixer.mix(
                narration_path=full_audio,
                music_path=processed_music,
                output_path=mixed_output,
            )

            progress_bar.progress(100, text="✅ Hoàn tất!")

            st.success("🎵 Audio đã ghép nhạc nền thành công!", icon="🎉")

            # Clean up temp files
            if music_path and music_path.exists():
                music_path.unlink(missing_ok=True)
            if processed_music.exists():
                processed_music.unlink(missing_ok=True)

            st.rerun()

        except Exception as e:
            progress_bar.progress(100, text="❌ Thất bại!")
            st.error(f"Lỗi khi ghép nhạc nền: {e}", icon="🚨")
            st.exception(e)


# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)
with nav_col1:
    st.page_link("pages/5_📄_Export Narration.py", label="← Quay lại Export Narration", use_container_width=True)
with nav_col2:
    st.page_link("pages/7_🖼️_Tạo Ảnh.py", label="🖼️ Tạo Ảnh →", use_container_width=True)
