"""
🔄 Pipeline - Chạy pipeline có duyệt + chỉnh sửa trực tiếp
"""

import json
import os
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
    page_title="Pipeline - AI Mystery",
    page_icon="🔄",
    layout="wide",
)

inject_theme()

# ── Constants ────────────────────────────────────────────────
TOPICS_FILE = PROJECT_ROOT / "data" / "mystery_topics.json"
PROJECTS_DIR = PROJECT_ROOT / "projects"
FFMPEG = PROJECT_ROOT / "bin" / "ffmpeg"
FFPROBE = PROJECT_ROOT / "bin" / "ffprobe"


# ── Helper functions ─────────────────────────────────────────
def load_topics():
    if not TOPICS_FILE.exists():
        return []
    data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    return data.get("topics", [])


def save_topics(topics_list):
    TOPICS_FILE.write_text(
        json.dumps({"topics": topics_list}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_story(topic_id):
    story_file = PROJECTS_DIR / topic_id / "story.json"
    if not story_file.exists():
        return None
    from ai_mystery_story.infrastructure.serializers.story_serializer import (
        StorySerializer,
    )

    data = json.loads(story_file.read_text(encoding="utf-8"))
    return StorySerializer.from_dict(data)


def save_story(story, topic_id):
    """Save story to JSON file"""
    from ai_mystery_story.infrastructure.repositories.json_story_repository import (
        JsonStoryRepository,
    )

    repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
    repository.save(story, topic_id=topic_id)


# ── Load topics ──────────────────────────────────────────────
topics = load_topics()

if not topics:
    st.warning("Chưa có đề tài nào trong dữ liệu. Vui lòng tạo đề tài mới!", icon="⚠️")
    st.stop()

# ── Session state init ───────────────────────────────────────
if "pipeline_step" not in st.session_state:
    st.session_state["pipeline_step"] = "select"
if "pipeline_story" not in st.session_state:
    st.session_state["pipeline_story"] = None
if "pipeline_topic_id" not in st.session_state:
    st.session_state["pipeline_topic_id"] = None
if "pipeline_edits" not in st.session_state:
    st.session_state["pipeline_edits"] = {}

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">🔄 Pipeline Tự Động</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Chạy toàn bộ quy trình: Story → Narration → Audio → Video</div>', unsafe_allow_html=True)

# ── Select topic dropdown ─────────────────────────────────────
query_params = st.query_params
default_topic = query_params.get("topic_id", None)

topic_options = {t["id"]: t["title"] for t in topics}
topic_ids = list(topic_options.keys())
topic_labels = [f"[{tid}] {topic_options[tid]}" for tid in topic_ids]

if default_topic and default_topic in topic_ids:
    default_index = topic_ids.index(default_topic)
else:
    default_index = 0

with st.container(border=True):
    selected_idx = st.selectbox(
        "🎯 Chọn Đề tài để chạy Pipeline",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
        disabled=(st.session_state["pipeline_step"] != "select"),
    )

selected_topic = topics[selected_idx]
topic_id = selected_topic["id"]

st.write("")

# ── Pipeline Stepper Progress Bar ────────────────────────────
step = st.session_state["pipeline_step"]
step_names = ["select", "review_story", "review_narration", "generating", "done"]
step_labels = ["1️⃣ Chọn đề tài", "2️⃣ Duyệt Story", "3️⃣ Duyệt Narration", "4️⃣ Tạo Media", "5️⃣ Hoàn tất"]

st.subheader("📊 Tiến trình Pipeline", divider="gray")
cols = st.columns(5)

for i, (col, label) in enumerate(zip(cols, step_labels)):
    with col:
        with st.container(border=True):
            current_idx = step_names.index(step)
            if current_idx > i:
                st.markdown(f"✅ {label}")
            elif current_idx == i:
                st.markdown(f"▶️ **{label}**")
            else:
                st.markdown(f"⏳ {label}")

st.write("")

# ══════════════════════════════════════════════════════════════
# STEP 1: SELECT TOPIC
# ══════════════════════════════════════════════════════════════
if step == "select":
    with st.container(border=True):
        st.subheader(f"📋 Đề tài đã chọn: {selected_topic['title']}", divider="blue")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Premise:** {selected_topic.get('premise', 'N/A')}")
            st.markdown(f"**Setting:** {selected_topic.get('setting', 'N/A')}")
        with col2:
            st.markdown(f"**Loại vụ án:** `{selected_topic.get('crime_type', 'N/A')}`")
            st.markdown(f"**Hình thức điều tra:** `{selected_topic.get('investigation_type', 'N/A')}`")
            st.markdown(f"**Kiểu Twist:** `{selected_topic.get('twist_type', 'N/A')}`")
            st.markdown(f"**Kết thúc:** `{selected_topic.get('ending_type', 'N/A')}`")

        st.write("")

        # Check existing story
        existing_story = load_story(topic_id)
        if existing_story is not None:
            st.success("Dữ liệu Story đã tồn tại sẵn cho đề tài này!", icon="✅")
            if st.button("📖 Duyệt Story có sẵn ngay", type="primary", use_container_width=True):
                st.session_state["pipeline_story"] = existing_story
                st.session_state["pipeline_topic_id"] = topic_id
                st.session_state["pipeline_step"] = "review_story"
                st.rerun()
        else:
            if st.button("✨ Tạo Story bằng AI & Chuyển tới Duyệt", type="primary", use_container_width=True):
                with st.spinner("🤖 Đang khởi tạo kịch bản chi tiết từ Gemini AI..."):
                    try:
                        from ai_mystery_story.application.generators.gemini_story_generator import (
                            GeminiStoryGenerator,
                        )
                        from ai_mystery_story.infrastructure.ai.gemini_provider import (
                            GeminiProvider,
                        )
                        from ai_mystery_story.infrastructure.repositories.json_story_repository import (
                            JsonStoryRepository,
                        )

                        provider = GeminiProvider()
                        generator = GeminiStoryGenerator(provider=provider, max_retries=2)
                        repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)

                        story = generator.generate(
                            title=selected_topic["title"],
                            premise=selected_topic["premise"],
                            setting=selected_topic["setting"],
                            crime_type=selected_topic.get("crime_type", "murder"),
                            investigation_type=selected_topic.get("investigation_type", "unknown"),
                            protagonist=selected_topic.get("protagonist", "detective"),
                            central_mystery=selected_topic.get("central_mystery", ""),
                            twist_type=selected_topic.get("twist_type", "unknown"),
                            ending_type=selected_topic.get("ending_type", "resolved"),
                            target_duration_minutes=60,
                        )

                        repository.save(story, topic_id=topic_id)

                        selected_topic["status"] = "generated"
                        selected_topic["story_id"] = str(story.id)
                        save_topics(topics)

                        st.session_state["pipeline_story"] = story
                        st.session_state["pipeline_topic_id"] = topic_id
                        st.session_state["pipeline_step"] = "review_story"
                        st.rerun()

                    except Exception as e:
                        st.error(f"Xảy ra lỗi khi tạo Story: {e}", icon="🚨")
                        st.exception(e)


# ══════════════════════════════════════════════════════════════
# STEP 2: REVIEW STORY (WITH EDITING)
# ══════════════════════════════════════════════════════════════
elif step == "review_story":
    story = st.session_state.get("pipeline_story")

    if story is None:
        st.error("Không tìm thấy dữ liệu story để duyệt.", icon="🚨")
        st.session_state["pipeline_step"] = "select"
        st.rerun()

    blueprint = story.blueprint

    st.subheader(f"📖 Kiểm duyệt & Chỉnh sửa Story: {blueprint.title}", divider="orange")

    # ── Editable Blueprint Fields ────────────────────────────
    with st.container(border=True):
        st.markdown("### ✏️ Chỉnh sửa Thông tin Cơ bản")
        st.caption("Bạn có thể sửa trực tiếp các trường bên dưới. Nhấn **💾 Lưu thay đổi** để lưu.")

        with st.form("story_edit_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_title = st.text_input("📌 Tiêu đề", value=blueprint.title)
                new_premise = st.text_area("📝 Tiền đề (Premise)", value=blueprint.premise, height=120)
                new_setting = st.text_area("🏟️ Bối cảnh (Setting)", value=blueprint.setting, height=120)
            with col2:
                new_central_mystery = st.text_area("❓ Bí ẩn trung tâm", value=blueprint.central_mystery, height=120)
                new_protagonist = st.text_input("🦸 Nhân vật chính", value=blueprint.protagonist)
                new_crime_type = st.text_input("🔓 Loại tội ác", value=blueprint.crime_type)
                new_twist_type = st.text_input("🌀 Kiểu Twist", value=blueprint.twist_type)
                new_ending_type = st.text_input("🏁 Kết thúc", value=blueprint.ending_type)

            submitted = st.form_submit_button("💾 Lưu thay đổi Cơ bản", type="primary", use_container_width=True)

            if submitted:
                blueprint.title = new_title
                blueprint.premise = new_premise
                blueprint.setting = new_setting
                blueprint.central_mystery = new_central_mystery
                blueprint.protagonist = new_protagonist
                blueprint.crime_type = new_crime_type
                blueprint.twist_type = new_twist_type
                blueprint.ending_type = new_ending_type
                save_story(story, topic_id)
                st.success("Đã lưu thay đổi!", icon="✅")
                st.rerun()

    st.write("")

    # ── Editable Characters ──────────────────────────────────
    with st.container(border=True):
        st.markdown("### 👥 Danh sách Nhân vật")
        st.caption("Chỉnh sửa danh sách nhân vật. Mỗi dòng là 1 nhân vật.")

        current_chars = "\n".join(blueprint.characters)
        new_chars_text = st.text_area(
            "Nhân vật (mỗi dòng 1 nhân)",
            value=current_chars,
            height=150,
            key="chars_edit",
        )

        if st.button("💾 Lưu Nhân vật", key="save_chars"):
            new_characters = [c.strip() for c in new_chars_text.strip().split("\n") if c.strip()]
            blueprint.characters = new_characters
            save_story(story, topic_id)
            st.success(f"Đã lưu {len(new_characters)} nhân vật!", icon="✅")
            st.rerun()

    st.write("")

    # ── Editable Beats ───────────────────────────────────────
    with st.container(border=True):
        st.markdown("### 📖 Chi tiết các Beats")
        total_duration = 0

        for beat_idx, beat in enumerate(blueprint.beats):
            total_duration += beat.target_duration_minutes
            with st.expander(f"Beat {beat.order}: {beat.title} ({beat.target_duration_minutes} phút)", expanded=False):
                with st.form(f"beat_edit_{beat_idx}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        new_beat_title = st.text_input("📌 Tiêu đề Beat", value=beat.title, key=f"beat_title_{beat_idx}")
                        new_beat_duration = st.number_input(
                            "⏱️ Thời lượng (phút)",
                            value=beat.target_duration_minutes,
                            min_value=1,
                            max_value=30,
                            key=f"beat_dur_{beat_idx}",
                        )
                    with col2:
                        new_beat_summary = st.text_area(
                            "📝 Nội dung tóm tắt",
                            value=beat.summary,
                            height=100,
                            key=f"beat_summary_{beat_idx}",
                        )
                        new_beat_purpose = st.text_input(
                            "🎯 Mục đích cốt truyện",
                            value=beat.purpose,
                            key=f"beat_purpose_{beat_idx}",
                        )

                    if st.form_submit_button(f"💾 Lưu Beat {beat.order}", type="secondary"):
                        # StoryBeat is frozen, so we need to replace it
                        from ai_mystery_story.domain.story.story_beat import StoryBeat

                        new_beat = StoryBeat(
                            order=beat.order,
                            title=new_beat_title,
                            summary=new_beat_summary,
                            purpose=new_beat_purpose,
                            target_duration_minutes=new_beat_duration,
                        )
                        blueprint.beats[beat_idx] = new_beat
                        save_story(story, topic_id)
                        st.success(f"Đã lưu Beat {beat.order}!", icon="✅")
                        st.rerun()

        st.caption(f"⏱️ **Tổng thời lượng dự kiến:** ~{total_duration} phút")

    # ── Decision toolbar ─────────────────────────────────────
    st.write("")
    st.subheader("✅ Quyết định Duyệt Kịch bản", divider="gray")
    col_act1, col_act2, col_act3 = st.columns(3)

    with col_act1:
        if st.button("✅ Phê duyệt & Chuyển sang Narration", type="primary", use_container_width=True):
            # Save any pending edits before proceeding
            save_story(story, topic_id)
            st.session_state["pipeline_step"] = "review_narration"
            st.rerun()

    with col_act2:
        if st.button("🔄 Tạo lại Story", use_container_width=True):
            story_file = PROJECTS_DIR / topic_id / "story.json"
            if story_file.exists():
                story_file.unlink()
            selected_topic["status"] = "pending"
            selected_topic["story_id"] = None
            save_topics(topics)
            st.session_state["pipeline_step"] = "select"
            st.rerun()

    with col_act3:
        if st.button("❌ Hủy bỏ Pipeline", use_container_width=True):
            st.session_state["pipeline_step"] = "select"
            st.session_state["pipeline_story"] = None
            st.rerun()


# ══════════════════════════════════════════════════════════════
# STEP 3: REVIEW NARRATION (WITH EDITING)
# ══════════════════════════════════════════════════════════════
elif step == "review_narration":
    story = st.session_state.get("pipeline_story")
    topic_id = st.session_state.get("pipeline_topic_id")

    if story is None:
        st.error("Không tìm thấy dữ liệu story.", icon="🚨")
        st.session_state["pipeline_step"] = "select"
        st.rerun()

    if story.narration is None:
        with st.container(border=True):
            st.subheader("🎙️ Khởi tạo Lời thuyết minh (Narration)", divider="green")
            st.markdown(f"**Story đã chọn:** {story.blueprint.title}")
            st.markdown(f"**Số lượng Beats:** {len(story.blueprint.beats)}")

            if st.button("✨ Tạo Narration bằng Gemini AI", type="primary", use_container_width=True):
                with st.spinner("🤖 Đang sinh văn bản lời thuyết minh từng beat..."):
                    try:
                        from ai_mystery_story.application.narration.narration_generator import (
                            NarrationGenerator,
                        )
                        from ai_mystery_story.application.narration.narration_plan_generator import (
                            NarrationPlanGenerator,
                        )
                        from ai_mystery_story.application.narration_service import (
                            NarrationService,
                        )
                        from ai_mystery_story.infrastructure.ai.gemini_provider import (
                            GeminiProvider,
                        )
                        from ai_mystery_story.infrastructure.repositories.json_story_repository import (
                            JsonStoryRepository,
                        )

                        provider = GeminiProvider()
                        plan_generator = NarrationPlanGenerator()
                        narration_generator = NarrationGenerator(
                            provider=provider,
                            request_delay_seconds=int(
                                os.getenv("GEMINI_NARRATION_DELAY_SECONDS", "10")
                            ),
                        )
                        service = NarrationService(
                            plan_generator=plan_generator,
                            narration_generator=narration_generator,
                        )

                        story = service.generate(story)
                        repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
                        repository.save(story, topic_id=topic_id)

                        st.session_state["pipeline_story"] = story
                        st.rerun()

                    except Exception as e:
                        st.error(f"Lỗi khi sinh Narration: {e}", icon="🚨")
                        st.exception(e)

    else:
        segments = story.narration.segments
        narration_text = story.narration.full_text()

        st.subheader(f"🎙️ Kiểm duyệt & Chỉnh sửa Narration: {story.blueprint.title}", divider="violet")

        # Stats dashboard
        m1, m2, m3 = st.columns(3)
        with m1:
            with st.container(border=True):
                st.metric("🧩 Segments", len(segments))
        with m2:
            with st.container(border=True):
                st.metric("📝 Tổng số Ký tự", f"{len(narration_text):,}")
        with m3:
            with st.container(border=True):
                st.metric("🔤 Tổng số Từ", f"{len(narration_text.split()):,}")

        # ── Edit Full Text ───────────────────────────────────
        with st.container(border=True):
            st.markdown("### ✏️ Chỉnh sửa Toàn bộ Văn bản")
            st.caption("Bạn có thể sửa trực tiếp toàn bộ text narration. Nhấn **💾 Lưu thay đổi** để lưu.")

            new_full_text = st.text_area(
                "Nội dung lời thuyết minh",
                value=narration_text,
                height=400,
                key="narration_full_edit",
            )

            if st.button("💾 Lưu Toàn bộ Văn bản", type="primary", use_container_width=True):
                # Update all segments' text proportionally
                # Simple approach: replace full text and re-split by double newlines
                new_segments_text = new_full_text.split("\n\n")

                from ai_mystery_story.domain.narration.narration_segment import (
                    NarrationSegment,
                )

                new_segments = []
                for i, seg_text in enumerate(new_segments_text):
                    seg_text = seg_text.strip()
                    if not seg_text:
                        continue
                    # Try to keep original segment title if available
                    if i < len(segments):
                        title = segments[i].title
                    else:
                        title = f"Segment {i + 1}"
                    new_segments.append(
                        NarrationSegment(order=i + 1, title=title, text=seg_text)
                    )

                story.narration.segments = new_segments
                save_story(story, topic_id)
                st.success(f"Đã lưu {len(new_segments)} segments!", icon="✅")
                st.rerun()

        st.write("")

        # ── Edit Individual Segments ─────────────────────────
        with st.container(border=True):
            st.markdown("### 📝 Chỉnh sửa từng Segment")
            st.caption("Mở rộng mỗi segment để chỉnh sửa nội dung riêng lẻ.")

            for i, seg in enumerate(segments):
                with st.expander(f"Segment #{i + 1} - {seg.title}", expanded=False):
                    with st.form(f"seg_edit_{i}"):
                        new_seg_title = st.text_input("📌 Tiêu đề Segment", value=seg.title, key=f"seg_title_{i}")
                        new_seg_text = st.text_area(
                            "📝 Nội dung",
                            value=seg.text,
                            height=200,
                            key=f"seg_text_{i}",
                        )

                        if st.form_submit_button(f"💾 Lưu Segment #{i + 1}", type="secondary"):
                            from ai_mystery_story.domain.narration.narration_segment import (
                                NarrationSegment,
                            )

                            story.narration.segments[i] = NarrationSegment(
                                order=seg.order,
                                title=new_seg_title,
                                text=new_seg_text,
                            )
                            save_story(story, topic_id)
                            st.success(f"Đã lưu Segment #{i + 1}!", icon="✅")
                            st.rerun()

        # ── Decision bar ─────────────────────────────────────
        st.write("")
        st.subheader("✅ Quyết định Duyệt Narration", divider="gray")
        col_act1, col_act2, col_act3 = st.columns(3)

        with col_act1:
            if st.button("✅ Phê duyệt & Tiến hành Tạo Audio/Video", type="primary", use_container_width=True):
                # Save any pending edits before proceeding
                save_story(story, topic_id)
                st.session_state["pipeline_step"] = "generating"
                st.rerun()

        with col_act2:
            if st.button("🔄 Tạo lại Narration", use_container_width=True):
                story.narration = None
                from ai_mystery_story.infrastructure.repositories.json_story_repository import (
                    JsonStoryRepository,
                )

                repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
                repository.save(story, topic_id=topic_id)
                st.session_state["pipeline_story"] = story
                st.rerun()

        with col_act3:
            if st.button("⬅️ Quay lại Duyệt Story", use_container_width=True):
                st.session_state["pipeline_step"] = "review_story"
                st.rerun()


# ══════════════════════════════════════════════════════════════
# STEP 4: GENERATE AUDIO + VIDEO
# ══════════════════════════════════════════════════════════════
elif step == "generating":
    story = st.session_state.get("pipeline_story")
    topic_id = st.session_state.get("pipeline_topic_id")

    if story is None:
        st.error("Không tìm thấy story.", icon="🚨")
        st.session_state["pipeline_step"] = "select"
        st.rerun()

    st.subheader("🔊 Đang tự động Tổng hợp Media (Audio & Video)", divider="red")

    with st.container(border=True):
        progress_bar = st.progress(0, text="Đang khởi tạo dịch vụ...")
        log_container = st.empty()
        logs = []

        def add_log(msg):
            logs.append(msg)
            log_container.code("\n".join(logs[-15:]), language=None)

        try:
            # ── Step 4a: Generate Audio ──────────────────────────
            progress_bar.progress(10, text="🔊 Đang gọi Edge TTS khởi tạo âm thanh...")
            add_log("🚀 Bắt đầu tiến trình sinh Voice Audio...")

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

            tts_provider = EdgeTTSProvider()
            tts_service = TTSService(provider=tts_provider)
            audio_assembly_service = AudioAssemblyService(
                assembler=FFmpegAudioAssembler(),
            )
            audio_generator = AudioGenerator(
                tts_service=tts_service,
                audio_assembly_service=audio_assembly_service,
            )

            audio_dir = PROJECTS_DIR / topic_id / "audio"

            add_log("🤖 Đang sinh file voice mp3 từng segment...")
            progress_bar.progress(25, text="🤖 Đang ghép nối các segment thành full audio...")

            audio = audio_generator.generate(
                story=story,
                output_dir=audio_dir,
            )

            repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
            repository.save(story, topic_id=topic_id)

            add_log(f"✅ Audio hoàn tất: {len(audio.segments)} segments")
            add_log(f"   Thời lượng: {audio.duration_seconds:.1f}s")
            progress_bar.progress(60, text="✅ Hoàn thành tổng hợp Audio!")

            # ── Step 4b: Generate Video (if images exist) ────────
            images_dir = PROJECTS_DIR / topic_id / "images"
            has_images = images_dir.exists() and any(images_dir.iterdir()) if images_dir.exists() else False

            if has_images:
                progress_bar.progress(65, text="🎬 Đang quét ảnh và chuẩn bị render Video FFmpeg...")
                add_log("🚀 Phát hiện tài nguyên ảnh, bắt đầu xuất Video...")

                audio_file = None
                for f in ["narration_full.mp3", "full.mp3"]:
                    if (audio_dir / f).exists():
                        audio_file = audio_dir / f
                        break
                if audio_file is None:
                    mp3_files = list(audio_dir.glob("*.mp3"))
                    if mp3_files:
                        audio_file = mp3_files[0]

                if audio_file:
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
                    duration = float(result.stdout.strip())

                    images = sorted([
                        p for p in images_dir.iterdir()
                        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                    ])

                    video_dir = PROJECTS_DIR / topic_id / "video"
                    video_dir.mkdir(parents=True, exist_ok=True)
                    output_file = video_dir / "final_fast.mp4"

                    VIDEO_WIDTH = 1920
                    VIDEO_HEIGHT = 1080
                    FPS = 30

                    # ── Beat-based rendering ──────────────────────
                    beat_durations = []
                    if story and story.blueprint.beats:
                        for beat in story.blueprint.beats:
                            beat_durations.append({
                                "order": beat.order,
                                "title": beat.title,
                                "duration_seconds": beat.target_duration_minutes * 60,
                            })

                    add_log(f"📁 Tổng số ảnh tìm thấy: {len(images)}")

                    if beat_durations:
                        total_beats = len(beat_durations)
                        add_log(f"🎬 Beat-based rendering: {total_beats} beats")
                        for bd in beat_durations:
                            add_log(f"   Beat {bd['order']}: {bd['title']} ({bd['duration_seconds']}s)")
                    else:
                        add_log("🎬 Fallback: Không có beats, dùng mode chia đều 7s")

                    temp_dir = video_dir / "_segments"
                    temp_dir.mkdir(exist_ok=True)
                    concat_file = video_dir / "_concat.txt"
                    silent_video = video_dir / "_silent.mp4"
                    segment_files = []

                    if beat_durations:
                        # ── Beat-based: mỗi beat = 1 ảnh, duration = beat duration ──
                        for beat_idx, beat in enumerate(beat_durations):
                            beat_order = beat["order"]
                            beat_duration = beat["duration_seconds"]

                            # Tìm ảnh beat_XX.jpg tương ứng
                            beat_image = None
                            for img in images:
                                if f"beat_{beat_order:02d}" in img.name:
                                    beat_image = img
                                    break
                            if beat_image is None:
                                beat_image = images[beat_idx % len(images)]

                            # Chia beat dài thành nhiều segment con (max 10s/segment)
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

                                progress = 65 + int(25 * (len(segment_files) / (total_beats * 2)))
                                progress_bar.progress(
                                    progress,
                                    text=f"🎬 Beat {beat_order}/{total_beats} (part {sub_idx + 1}/{num_sub_segments})...",
                                )
                                add_log(f"[Beat {beat_order}] {beat_image.name} ({segment_duration:.1f}s)")

                                scale_filter = f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:force_original_aspect_ratio=increase"
                                movement = (beat_idx + sub_idx) % 4
                                duration_expr = f"{segment_duration:.6f}"

                                if movement == 0:
                                    crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*t/{duration_expr}"
                                elif movement == 1:
                                    crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*(1-t/{duration_expr})"
                                elif movement == 2:
                                    crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*t/{duration_expr}:(in_h-out_h)/2"
                                else:
                                    crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*(1-t/{duration_expr}):(in_h-out_h)/2"

                                firelight_filter = "eq=brightness='0.015+0.020*sin(2*PI*t/3.7)+0.012*sin(2*PI*t/1.9)+0.008*sin(2*PI*t/5.3)'"
                                filter_complex = f"{scale_filter},{crop_filter},{firelight_filter},setsar=1,format=yuv420p"

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
                                    "ultrafast",
                                    "-crf",
                                    "28",
                                    "-pix_fmt",
                                    "yuv420p",
                                    str(segment_file),
                                ]

                                result = subprocess.run(command, capture_output=True, text=True)
                                if result.returncode != 0:
                                    add_log(f"❌ FFmpeg error at Beat {beat_order} part {sub_idx + 1}")
                                    raise RuntimeError(f"FFmpeg failed for Beat {beat_order}")
                    else:
                        # ── Fallback: chia đều 7s/ảnh (giữ logic cũ) ──
                        IMAGE_DURATION = 7
                        total_segments = int((duration + IMAGE_DURATION - 0.001) // IMAGE_DURATION)
                        if total_segments < 1:
                            total_segments = 1

                        add_log(f"🎬 Fallback render: {total_segments} segments @ {IMAGE_DURATION}s each")

                        for index in range(total_segments):
                            image = images[index % len(images)]
                            segment_start = index * IMAGE_DURATION
                            remaining = duration - segment_start
                            segment_duration = min(IMAGE_DURATION, remaining)
                            if segment_duration <= 0:
                                break

                            segment_file = temp_dir / f"segment_{index:04d}.mp4"
                            segment_files.append(segment_file)

                            progress = 65 + int(25 * (index / total_segments))
                            progress_bar.progress(
                                progress,
                                text=f"🎬 Đang render Segment Video {index + 1}/{total_segments}...",
                            )

                            scale_filter = f"scale={VIDEO_WIDTH * 2}:{VIDEO_HEIGHT * 2}:force_original_aspect_ratio=increase"
                            movement = index % 4
                            duration_expr = f"{segment_duration:.6f}"

                            if movement == 0:
                                crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*t/{duration_expr}"
                            elif movement == 1:
                                crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*(1-t/{duration_expr})"
                            elif movement == 2:
                                crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*t/{duration_expr}:(in_h-out_h)/2"
                            else:
                                crop_filter = f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(in_w-out_w)*(1-t/{duration_expr}):(in_h-out_h)/2"

                            firelight_filter = "eq=brightness='0.015+0.020*sin(2*PI*t/3.7)+0.012*sin(2*PI*t/1.9)+0.008*sin(2*PI*t/5.3)'"
                            filter_complex = f"{scale_filter},{crop_filter},{firelight_filter},setsar=1,format=yuv420p"

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
                                "ultrafast",
                                "-crf",
                                "28",
                                "-pix_fmt",
                                "yuv420p",
                                str(segment_file),
                            ]

                            result = subprocess.run(command, capture_output=True, text=True)
                            if result.returncode != 0:
                                add_log(f"❌ Xảy ra lỗi FFmpeg ở segment {index + 1}")
                                raise RuntimeError(f"FFmpeg failed for segment {index + 1}")

                    add_log("🔗 Đang ghép nối chuỗi segment video...")
                    with concat_file.open("w") as f:
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

                    add_log("🎵 Đang lồng ghép audio mp3 vào video hoàn chỉnh...")
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

                    # Cleanup
                    for segment in segment_files:
                        segment.unlink(missing_ok=True)
                    concat_file.unlink(missing_ok=True)
                    silent_video.unlink(missing_ok=True)
                    try:
                        temp_dir.rmdir()
                    except OSError:
                        pass

                    add_log(f"✅ Đã tạo thành công Video: {output_file.name}")
            else:
                add_log("⏭️ Bỏ qua tạo Video (chưa tìm thấy tài nguyên trong thư mục images/)")

            # Finish
            progress_bar.progress(100, text="🎉 Đã hoàn tất toàn bộ tiến trình Pipeline!")
            add_log("=" * 40)
            add_log("🎉 HOÀN THÀNH PIPELINE TỰ ĐỘNG SẢN XUẤT!")
            add_log("=" * 40)

            st.session_state["pipeline_step"] = "done"
            st.balloons()
            st.rerun()

        except Exception as e:
            progress_bar.progress(100, text="❌ Tiến trình gặp sự cố!")
            add_log(f"❌ Exception: {e}")
            st.error(f"Pipeline tạm ngưng do lỗi: {e}", icon="🚨")
            st.exception(e)

            if st.button("⬅️ Quay lại bước Duyệt Narration"):
                st.session_state["pipeline_step"] = "review_narration"
                st.rerun()


# ══════════════════════════════════════════════════════════════
# STEP 5: DONE
# ══════════════════════════════════════════════════════════════
elif step == "done":
    story = st.session_state.get("pipeline_story")
    topic_id = st.session_state.get("pipeline_topic_id")

    st.balloons()
    st.success("CHÚC MỪNG! Pipeline sản xuất nội dung đã hoàn tất xuất sắc!", icon="🎉")

    if story:
        with st.container(border=True):
            st.markdown("### 📊 Tổng kết Thành quả Dự án")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("📖 Story Beats", len(story.blueprint.beats))
            with c2:
                st.metric("👥 Nhân vật", len(story.blueprint.characters))
            with c3:
                st.metric("🎙️ Segments Narration", len(story.narration.segments) if story.narration else 0)
            with c4:
                st.metric("🔊 Audio MP3", "✅ Sẵn sàng")

            video_dir = PROJECTS_DIR / topic_id / "video"
            has_video = video_dir.exists() and any(video_dir.glob("*.mp4")) if video_dir.exists() else False
            if has_video:
                st.info("Video render hoàn tất và đã lưu trong thư mục video/!", icon="🎬")

        st.write("")
        st.subheader("🔗 Truy cập nhanh các Trang chức năng", divider="gray")

        btn1, btn2, btn3, btn4 = st.columns(4)
        with btn1:
            st.page_link(
                "pages/3_📖_Xem Story.py",
                label="📖 Xem Story",
                use_container_width=True,
            )
        with btn2:
            st.page_link(
                "pages/7_📄_Export Narration.py",
                label="📄 Export Narration",
                use_container_width=True,
            )
        with btn3:
            st.page_link(
                "pages/6_🔊_Audio.py",
                label="🔊 Nghe Audio",
                use_container_width=True,
            )
        with btn4:
            st.page_link(
                "pages/8_🎬_Tạo Video.py",
                label="🎬 Xem Video",
                use_container_width=True,
            )

    st.write("")
    st.markdown("---")
    if st.button("🔄 Khởi chạy Pipeline cho Đề tài Mới", type="primary", use_container_width=True):
        st.session_state["pipeline_step"] = "select"
        st.session_state["pipeline_story"] = None
        st.session_state["pipeline_topic_id"] = None
        st.rerun()
