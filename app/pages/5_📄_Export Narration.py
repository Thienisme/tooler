"""
📄 Export Narration - Xuất lời thuyết minh ra file txt (có chỉnh sửa)
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
    page_title="Export Narration - AI Mystery",
    page_icon="📄",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">📄 Export Narration</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Xem trước, chỉnh sửa và xuất lời thuyết minh ra file văn bản</div>', unsafe_allow_html=True)

render_step_indicator(2, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

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
    """Save story to JSON file"""
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
        "🎯 Chọn Story để xuất lời thuyết minh",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
        index=default_index,
    )

selected_topic = generated_topics[selected_idx]
story = load_story(selected_topic["id"])

if story is None:
    st.error("Không tìm thấy dữ liệu story của dự án này.", icon="🚨")
    st.stop()

if story.narration is None:
    st.warning("Story này chưa có dữ liệu Narration.", icon="⚠️")
    st.page_link(
        "pages/4_🎙️_Narration.py",
        label="🎙️ Tạo Narration",
    )
    st.stop()

# ── Story Overview Dashboard ──────────────────────────────────
segments = story.narration.segments
narration_text = story.narration.full_text().strip()
narration_text = narration_text.replace("\n\n", "\n")

st.write("")
st.subheader(f"📖 {story.blueprint.title}", divider="blue")

col1, col2, col3 = st.columns(3)
with col1:
    with st.container(border=True):
        st.metric("🧩 Thuyết minh Segments", len(segments))
with col2:
    with st.container(border=True):
        st.metric("📝 Tổng số Ký tự", f"{len(narration_text):,}")
with col3:
    with st.container(border=True):
        st.metric("🔤 Tổng số Từ", f"{len(narration_text.split()):,}")

st.write("")

# ── Edit Full Text Section ──────────────────────────────────
st.subheader("✏️ Chỉnh sửa Văn bản (Edit)", divider="orange")

with st.container(border=True):
    st.markdown("### 📝 Chỉnh sửa Toàn bộ Văn bản")
    st.caption("Bạn có thể sửa trực tiếp toàn bộ text narration. Nhấn **💾 Lưu thay đổi** để lưu vào hệ thống.")

    new_full_text = st.text_area(
        "Nội dung lời thuyết minh",
        value=narration_text,
        height=400,
        key="export_narration_edit",
    )

    col_save1, col_save2, col_save3 = st.columns([1, 1, 2])
    with col_save1:
        if st.button("💾 Lưu Toàn bộ", type="primary", use_container_width=True):
            # Update all segments' text proportionally
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
            save_story(story, selected_topic["id"])
            st.success(f"Đã lưu {len(new_segments)} segments!", icon="✅")
            st.rerun()

    with col_save2:
        if st.button("🔄 Khôi phục gốc", use_container_width=True):
            st.rerun()

st.write("")

# ── Edit Individual Segments ─────────────────────────────────
st.subheader("📝 Chỉnh sửa từng Segment", divider="violet")

with st.container(border=True):
    st.caption("Mở rộng mỗi segment để chỉnh sửa nội dung riêng lẻ.")

    for i, seg in enumerate(segments):
        with st.expander(f"Segment #{i + 1} - {seg.title}", expanded=False):
            with st.form(f"seg_edit_export_{i}"):
                new_seg_title = st.text_input("📌 Tiêu đề Segment", value=seg.title, key=f"export_seg_title_{i}")
                new_seg_text = st.text_area(
                    "📝 Nội dung",
                    value=seg.text,
                    height=200,
                    key=f"export_seg_text_{i}",
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
                    save_story(story, selected_topic["id"])
                    st.success(f"Đã lưu Segment #{i + 1}!", icon="✅")
                    st.rerun()

st.write("")

# ── Preview Section ──────────────────────────────────────────
st.subheader("👀 Xem trước Văn bản (Preview)", divider="gray")

# Refresh text after potential edits
narration_text = story.narration.full_text().strip()
narration_text = narration_text.replace("\n\n", "\n")

with st.container(border=True):
    tab1, tab2 = st.tabs(["📄 Toàn bộ Văn bản (Full Text)", "📝 Chia theo Segment"])

    with tab1:
        st.text_area(
            "Nội dung lời thuyết minh",
            narration_text,
            height=420,
            disabled=True,
            help="Toàn bộ nội dung narration đã được làm sạch xuống dòng",
        )

    with tab2:
        for i, segment in enumerate(story.narration.segments, 1):
            with st.expander(f"Segment #{i} - {segment.title}"):
                st.write(segment.text)

st.write("")

# ── Export & Download Section ────────────────────────────────
st.subheader("💾 Xuất & Tải xuống (Export & Download)", divider="green")

output_path = PROJECTS_DIR / selected_topic["id"] / "narration_full.txt"

with st.container(border=True):
    col_exp1, col_exp2 = st.columns(2)

    with col_exp1:
        st.markdown("#### 📤 Xuất file lên Máy chủ (Server)")
        st.caption("Lưu file văn bản trực tiếp vào thư mục dự án `projects/<topic_id>/narration_full.txt`")
        if st.button("💾 Ghi file narration_full.txt", type="primary", use_container_width=True):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(narration_text + "\n", encoding="utf-8")
            st.success("Đã ghi file thành công!", icon="✅")
            st.rerun()

    with col_exp2:
        st.markdown("#### ⬇️ Tải xuống Máy cục bộ (Client)")
        st.caption("Download file `.txt` trực tiếp về máy tính của bạn")
        st.download_button(
            label="⬇️ Tải file TXT về máy",
            data=narration_text,
            file_name=f"{selected_topic['id']}_narration.txt",
            mime="text/plain",
            use_container_width=True,
        )

    if output_path.exists():
        st.write("")
        st.info(f"**File đã lưu trên hệ thống:** `{output_path}`", icon="📁")

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)
with nav_col1:
    st.page_link("pages/4_🎙️_Narration.py", label="← Quay lại Narration", use_container_width=True)
with nav_col2:
    st.page_link("pages/6_🔊_Audio.py", label="🔊 Tạo Audio →", use_container_width=True)
