"""
🎙️ Narration - Tạo lời thuyết minh từ story
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
    page_title="Narration - AI Mystery",
    page_icon="🎙️",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">🎙️ Tạo Narration</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Chuyển đổi kịch bản Story Beats thành lời thuyết minh diễn cảm</div>', unsafe_allow_html=True)

render_step_indicator(2, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Load story ───────────────────────────────────────────────
PROJECTS_DIR = PROJECT_ROOT / "projects"
GEMINI_DELAY = int(os.getenv("GEMINI_NARRATION_DELAY_SECONDS", "10"))


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
        "🎯 Chọn Story để sinh lời thuyết minh",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = generated_topics[selected_idx]
story = load_story(selected_topic["id"])

if story is None:
    st.error("Không tìm thấy dữ liệu story của dự án này.", icon="🚨")
    st.stop()

# ── Story Overview Dashboard ──────────────────────────────────
st.write("")
st.subheader(f"📖 {story.blueprint.title}", divider="red")

m1, m2, m3 = st.columns(3)
with m1:
    with st.container(border=True):
        st.metric("🎬 Story Beats", len(story.blueprint.beats))
with m2:
    with st.container(border=True):
        st.metric("⏱️ Thời lượng dự kiến", f"{story.blueprint.target_duration_minutes} phút")
with m3:
    with st.container(border=True):
        status_text = "✅ Đã tạo" if story.narration else "⏳ Chưa khởi tạo"
        st.metric("🎙️ Trạng thái Narration", status_text)

st.write("")

# ── Narration Status & Controls ──────────────────────────────
is_regenerating = st.session_state.get("regenerate_narration", False)

if story.narration is not None and not is_regenerating:
    segments = story.narration.segments

    with st.container(border=True):
        st.success("Dự án này đã có bản thuyết minh Narration hoàn chỉnh!", icon="✅")

        c1, c2 = st.columns(2)
        with c1:
            with st.container(border=True):
                st.metric("🧩 Số lượng Segments", len(segments))
        with c2:
            with st.container(border=True):
                st.metric("📝 Tổng số ký tự", f"{len(story.narration.full_text()):,} ký tự")

        st.write("")

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🔄 Tạo lại Narration (Ghi đè)", use_container_width=True):
                st.session_state["regenerate_narration"] = True
                st.rerun()
        with col_btn2:
            st.page_link(
                "pages/6_🔊_Audio.py",
                label="🔊 Tạo Audio",
                use_container_width=True,
            )

else:
    if is_regenerating:
        st.warning("BẠN ĐANG TRONG CHẾ ĐỘ TẠO LẠI NARRATION. Dữ liệu cũ sẽ bị ghi đè.", icon="⚠️")

    with st.container(border=True):
        st.markdown("### 🚀 Khởi tạo Lời Thuyết Minh bằng Gemini AI")
        st.caption("AI sẽ chia nhỏ kịch bản thành các phân đoạn diễn giải (Segments) phù hợp cho đọc đọc thuyết minh.")

        c1, c2 = st.columns(2)
        with c1:
            st.info("**Model Engine:** Google Gemini AI", icon="ℹ️")
        with c2:
            st.info(f"**Request Delay:** {GEMINI_DELAY} giây / request (tránh Rate Limit)", icon="ℹ️")

        st.write("")

        if st.button("✨ Bắt đầu tạo Narration", type="primary", use_container_width=True):
            progress_bar = st.progress(0, text="Đang chuẩn bị...")

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

                progress_bar.progress(15, text="Đang kết nối dịch vụ Gemini AI...")
                provider = GeminiProvider()

                progress_bar.progress(35, text="Đang lập kế hoạch phân đoạn Narration...")
                plan_generator = NarrationPlanGenerator()
                narration_generator = NarrationGenerator(
                    provider=provider,
                    request_delay_seconds=GEMINI_DELAY,
                )
                service = NarrationService(
                    plan_generator=plan_generator,
                    narration_generator=narration_generator,
                )

                progress_bar.progress(55, text="🤖 Gemini AI đang viết lời thuyết minh từng segment...")
                story = service.generate(story)

                progress_bar.progress(85, text="💾 Đang lưu bản Narration vào hệ thống...")
                repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
                repository.save(story, topic_id=selected_topic["id"])

                progress_bar.progress(100, text="✅ Hoàn tất!")

                st.session_state["regenerate_narration"] = False
                st.balloons()
                st.success("🎉 Narration đã được tạo thành công!", icon="🎉")

                st.rerun()

            except Exception as e:
                progress_bar.progress(100, text="❌ Thất bại!")
                st.error(f"❌ Xảy ra lỗi trong quá trình tạo Narration: {e}", icon="🚨")
                st.exception(e)

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)
with nav_col1:
    st.page_link("pages/3_📖_Xem Story.py", label="← Quay lại Xem Story", use_container_width=True)
with nav_col2:
    if story.narration is not None:
        st.page_link("pages/5_📄_Export Narration.py", label="📄 Export Narration →", use_container_width=True)