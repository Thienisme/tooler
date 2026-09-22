"""
📖 Xem Story - Xem và chỉnh sửa nội dung truyện chi tiết
"""

import json
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
    page_title="Xem Story - AI Mystery",
    page_icon="📖",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">📖 Xem & Sửa Story</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Xem, chỉnh sửa Blueprint, Nhân vật và Story Beats trước khi tiếp tục</div>', unsafe_allow_html=True)

render_step_indicator(1, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

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


def save_story(story, topic_id):
    from ai_mystery_story.infrastructure.repositories.json_story_repository import (
        JsonStoryRepository,
    )

    repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
    repository.save(story, topic_id=topic_id)


def load_topics():
    topics_file = PROJECT_ROOT / "data" / "mystery_topics.json"
    if not topics_file.exists():
        return []
    data = json.loads(topics_file.read_text(encoding="utf-8"))
    return data.get("topics", [])


topics = load_topics()
generated_topics = [t for t in topics if t.get("status") == "generated"]

if not generated_topics:
    st.warning("Chưa có story nào được tạo trong hệ thống. Hãy tạo story trước!", icon="⚠️")
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
        "🔍 Chọn Story để kiểm tra & chỉnh sửa",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = generated_topics[selected_idx]
story = load_story(selected_topic["id"])

if story is None:
    st.error("Không tìm thấy tệp tin `story.json` của dự án này.", icon="🚨")
    st.stop()

blueprint = story.blueprint

st.write("")
st.subheader(f"🎬 {blueprint.title}", divider="red")

# Metric Dashboard
m1, m2, m3, m4 = st.columns(4)
with m1:
    with st.container(border=True):
        st.metric("📖 Story Beats", len(blueprint.beats))
with m2:
    with st.container(border=True):
        st.metric("👥 Nhân vật", len(blueprint.characters))
with m3:
    with st.container(border=True):
        st.metric("⏱️ Thời lượng dự kiến", f"{blueprint.target_duration_minutes} phút")
with m4:
    with st.container(border=True):
        st.metric("🎭 Dạng Twist", blueprint.twist_type)

st.write("")

# ── Tabs Content ─────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 Blueprint", "👥 Characters", "📖 Beats", "🎙️ Narration"]
)

# ══════════════════════════════════════════════════════════════
# TAB 1: BLUEPRINT (EDIT)
# ══════════════════════════════════════════════════════════════
with tab1:
    st.markdown("### ✏️ Chỉnh sửa Blueprint")

    with st.form("blueprint_edit_form"):
        col_a, col_b = st.columns(2)

        with col_a:
            new_premise = st.text_area(
                "📖 Tiền đề (Premise)",
                value=blueprint.premise,
                height=120,
            )
            new_setting = st.text_area(
                "🏰 Bối cảnh (Setting)",
                value=blueprint.setting,
                height=80,
            )
            new_central_mystery = st.text_area(
                "❓ Bí ẩn trung tâm",
                value=blueprint.central_mystery,
                height=80,
            )

        with col_b:
            new_protagonist = st.text_input(
                "🕵️ Nhân vật chính",
                value=blueprint.protagonist,
            )
            new_crime_type = st.text_input(
                "🔍 Loại án",
                value=blueprint.crime_type,
            )
            new_investigation_type = st.text_input(
                "🔎 Phương pháp điều tra",
                value=blueprint.investigation_type,
            )
            new_twist_type = st.text_input(
                "🌀 Thể loại Twist",
                value=blueprint.twist_type,
            )
            new_ending_type = st.text_input(
                "🏁 Kiểu kết thúc",
                value=blueprint.ending_type,
            )
            new_duration = st.number_input(
                "⏱️ Thời lượng dự kiến (phút)",
                value=blueprint.target_duration_minutes,
                min_value=5,
                max_value=120,
                step=5,
            )

        if st.form_submit_button("💾 Lưu Blueprint", type="primary", use_container_width=True):
            from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint

            story.blueprint = StoryBlueprint(
                title=blueprint.title,
                premise=new_premise,
                setting=new_setting,
                protagonist=new_protagonist,
                crime_type=new_crime_type,
                investigation_type=new_investigation_type,
                central_mystery=new_central_mystery,
                twist_type=new_twist_type,
                ending_type=new_ending_type,
                target_duration_minutes=new_duration,
                characters=blueprint.characters,
                beats=blueprint.beats,
            )
            save_story(story, selected_topic["id"])
            st.success("Đã lưu Blueprint!", icon="✅")
            st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB 2: CHARACTERS (EDIT)
# ══════════════════════════════════════════════════════════════
with tab2:
    st.markdown(f"### Danh sách nhân vật ({len(blueprint.characters)})")

    with st.form("characters_edit_form"):
        new_characters = []
        for i, character in enumerate(blueprint.characters):
            new_char = st.text_input(
                f"🎭 Nhân vật #{i + 1}",
                value=character,
                key=f"char_edit_{i}",
            )
            new_characters.append(new_char)

        # Add new character
        st.markdown("---")
        new_char_input = st.text_input(
            "➕ Thêm nhân vật mới",
            placeholder="Nhập tên nhân vật mới...",
            key="new_char_input",
        )

        col_save, col_delete = st.columns(2)
        with col_save:
            if st.form_submit_button("💾 Lưu Nhân vật", type="primary", use_container_width=True):
                if new_char_input.strip():
                    new_characters.append(new_char_input.strip())
                # Filter empty
                new_characters = [c for c in new_characters if c.strip()]
                blueprint.characters = new_characters
                save_story(story, selected_topic["id"])
                st.success(f"Đã lưu {len(new_characters)} nhân vật!", icon="✅")
                st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB 3: BEATS (EDIT)
# ══════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### Chi tiết các Story Beats")
    st.caption("Mở rộng mỗi Beat để chỉnh sửa nội dung.")

    total_duration = 0
    for i, beat in enumerate(blueprint.beats):
        total_duration += beat.target_duration_minutes
        with st.expander(f"Beat {beat.order}: **{beat.title}** ({beat.target_duration_minutes} phút)"):
            with st.form(f"beat_edit_form_{i}"):
                col_b1, col_b2 = st.columns(2)

                with col_b1:
                    new_beat_title = st.text_input("📌 Tiêu đề Beat", value=beat.title)
                    new_beat_purpose = st.text_area("🎯 Mục đích kịch bản", value=beat.purpose, height=80)

                with col_b2:
                    new_beat_duration = st.number_input(
                        "⏱️ Thời lượng (phút)",
                        value=beat.target_duration_minutes,
                        min_value=1,
                        max_value=30,
                        step=1,
                    )

                new_beat_summary = st.text_area("📝 Nội dung tóm tắt", value=beat.summary, height=150)

                if st.form_submit_button(f"💾 Lưu Beat #{beat.order}", type="secondary"):
                    from ai_mystery_story.domain.story.story_beat import StoryBeat

                    blueprint.beats[i] = StoryBeat(
                        order=beat.order,
                        title=new_beat_title,
                        summary=new_beat_summary,
                        purpose=new_beat_purpose,
                        target_duration_minutes=new_beat_duration,
                    )
                    save_story(story, selected_topic["id"])
                    st.success(f"Đã lưu Beat #{beat.order}!", icon="✅")
                    st.rerun()

    st.write("")
    st.caption(f"⏱️ Tổng thời lượng lên kế hoạch: **{total_duration} phút**")

# ══════════════════════════════════════════════════════════════
# TAB 4: NARRATION (VIEW)
# ══════════════════════════════════════════════════════════════
with tab4:
    if story.narration is None:
        st.info("Chưa có dữ liệu Narration (Lời thuyết minh) cho story này.", icon="💡")
        st.page_link(
            "pages/4_🎙️_Narration.py",
            label="🎙️ Tạo Narration",
        )
    else:
        segments = story.narration.segments

        n1, n2 = st.columns(2)
        with n1:
            with st.container(border=True):
                st.metric("🧩 Số lượng Segment", len(segments))
        with n2:
            with st.container(border=True):
                st.metric("📝 Tổng số ký tự", f"{len(story.narration.full_text()):,}")

        st.write("")
        st.markdown("#### Chi tiết các đoạn thuyết minh (Segments)")

        for i, segment in enumerate(segments, 1):
            with st.expander(f"Segment #{i} - {segment.title}"):
                st.write(segment.text)

        st.write("")
        st.markdown("#### 📄 Toàn văn bài thuyết minh (Full Narration)")
        st.text_area(
            label="Full Text",
            value=story.narration.full_text(),
            height=350,
            disabled=True,
            label_visibility="collapsed",
        )

        st.info("💡 Để chỉnh sửa Narration, hãy sang trang **📄 Export Narration**.", icon="💡")

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)

with nav_col1:
    st.page_link("pages/1_📋_Topics.py", label="← Quay lại Topics", use_container_width=True)

with nav_col2:
    if story.narration is None:
        st.page_link("pages/4_🎙️_Narration.py", label="🎙️ Tạo Narration →", use_container_width=True)
    else:
        st.page_link("pages/5_📄_Export Narration.py", label="📄 Export Narration →", use_container_width=True)
