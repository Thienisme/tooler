"""
✍️ Tạo Story - Tạo truyện mystery từ đề tài
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components.theme import inject_theme, render_step_indicator

st.set_page_config(
    page_title="Tạo Story - AI Mystery",
    page_icon="✍️",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">✍️ Tạo Story</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Chuyển đổi ý tưởng đề tài thành kịch bản truyện trinh thám chi tiết qua AI</div>', unsafe_allow_html=True)

render_step_indicator(1, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Load topics ──────────────────────────────────────────────
TOPICS_FILE = PROJECT_ROOT / "data" / "mystery_topics.json"


def load_topics():
    if not TOPICS_FILE.exists():
        return []
    data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    return data.get("topics", [])


def save_topics(data):
    TOPICS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


topics = load_topics()

if not topics:
    st.warning("Chưa tìm thấy danh sách đề tài trong hệ thống.", icon="⚠️")
    st.stop()

# ── Select topic ─────────────────────────────────────────────
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
        "🎯 Chọn đề tài cần sinh story",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = topics[selected_idx]

# ── Topic Detail Display ─────────────────────────────────────
st.write("")
st.subheader("📋 Chi tiết đề tài đã chọn", divider="red")

status = selected_topic.get("status", "pending")
badge_class = (
    "status-badge-generated" if status == "generated" else "status-badge-pending"
)

col_left, col_right = st.columns(2)

with col_left:
    with st.container(border=True):
        st.markdown(f"**📌 Tiêu đề:** {selected_topic['title']}")
        st.markdown(f"**🆔 Topic ID:** `{selected_topic['id']}`")
        st.markdown(
            f"**Trạng thái:** <span class='{badge_class}'>{status.upper()}</span>",
            unsafe_allow_html=True,
        )
        st.write("")
        st.markdown(f"**🔍 Thể loại:** `{selected_topic.get('crime_type', 'N/A')}`")
        st.markdown(
            f"**🔎 Phương pháp:** `{selected_topic.get('investigation_type', 'N/A')}`"
        )

with col_right:
    with st.container(border=True):
        st.markdown(
            f"**📖 Premise:**\n> {selected_topic.get('premise', 'N/A')}"
        )
        st.markdown(f"**🏰 Bối cảnh (Setting):** {selected_topic.get('setting', 'N/A')}")
        st.markdown(f"**🕵️ Nhân vật chính:** {selected_topic.get('protagonist', 'N/A')}")
        st.markdown(
            f"**❓ Bí ẩn trung tâm:** {selected_topic.get('central_mystery', 'N/A')}"
        )

st.write("")

# ── Generation Section ───────────────────────────────────────
if selected_topic.get("status") == "generated":
    st.info("Đề tài này đã có story được tạo trước đó.", icon="ℹ️")
    st.page_link(
        "pages/3_📖_Xem Story.py",
        label="📖 Xem Story",
        use_container_width=True,
    )
else:
    with st.container(border=True):
        st.markdown("### 🚀 Tiến hành tạo Story")
        st.caption(
            "Hệ thống sẽ kết nối với **Google Gemini AI** để khởi tạo kịch bản, tuyến nhân vật và các story beats."
        )

        if st.button(
            "✨ Khởi tạo Story ngay", type="primary", use_container_width=True
        ):
            progress_bar = st.progress(0, text="Đang chuẩn bị...")

            try:
                progress_bar.progress(10, text="Đang tải modules...")
                from ai_mystery_story.application.generators.gemini_story_generator import (
                    GeminiStoryGenerator,
                )
                from ai_mystery_story.infrastructure.ai.gemini_provider import (
                    GeminiProvider,
                )
                from ai_mystery_story.infrastructure.repositories.json_story_repository import (
                    JsonStoryRepository,
                )

                progress_bar.progress(25, text="Đang khởi tạo kết nối Gemini AI...")
                provider = GeminiProvider()
                generator = GeminiStoryGenerator(provider=provider, max_retries=2)
                repository = JsonStoryRepository(
                    projects_dir=PROJECT_ROOT / "projects"
                )

                progress_bar.progress(40, text="🤖 Gemini AI đang suy luận kịch bản...")
                story = generator.generate(
                    title=selected_topic["title"],
                    premise=selected_topic["premise"],
                    setting=selected_topic["setting"],
                    crime_type=selected_topic.get("crime_type", "murder"),
                    investigation_type=selected_topic.get(
                        "investigation_type", "unknown"
                    ),
                    protagonist=selected_topic.get("protagonist", "detective"),
                    central_mystery=selected_topic.get("central_mystery", ""),
                    twist_type=selected_topic.get("twist_type", "unknown"),
                    ending_type=selected_topic.get("ending_type", "resolved"),
                    target_duration_minutes=60,
                )

                progress_bar.progress(80, text="💾 Đang lưu story vào hệ thống...")
                repository.save(story, topic_id=selected_topic["id"])

                selected_topic["status"] = "generated"
                selected_topic["story_id"] = str(story.id)
                save_topics({"topics": topics})

                progress_bar.progress(100, text="✅ Hoàn tất!")
                st.balloons()
                st.success("Story đã được tạo thành công!", icon="🎉")

                # Summary Dashboard
                st.subheader("hông số kịch bản đã tạo", divider="green")

                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    with st.container(border=True):
                        st.metric("🆔 Story ID", str(story.id)[:8] + "...")
                with m2:
                    with st.container(border=True):
                        st.metric("👥 Nhân vật", len(story.blueprint.characters))
                with m3:
                    with st.container(border=True):
                        st.metric("🎬 Story Beats", len(story.blueprint.beats))
                with m4:
                    with st.container(border=True):
                        st.metric(
                            "⏱️ Thời lượng",
                            f"{story.blueprint.target_duration_minutes} phút",
                        )

                st.write("")
                st.subheader("🚀 Bước tiếp theo", divider="gray")
                st.page_link(
                    "pages/3_📖_Xem Story.py",
                    label="📖 Xem Story →",
                    use_container_width=True,
                )

            except Exception as e:
                progress_bar.progress(100, text="❌ Thất bại!")
                st.error(f"❌ Xảy ra lỗi trong quá trình xử lý: {e}", icon="🚨")
                st.exception(e)

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()
n1, n2 = st.columns(2)
with n1:
    st.page_link("pages/1_📋_Topics.py", label="← Quay lại Topics", use_container_width=True)
with n2:
    if selected_topic.get("status") == "generated":
        st.page_link("pages/3_📖_Xem Story.py", label="Xem Story →", use_container_width=True)