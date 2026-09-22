"""
📋 Topics - Xem, quản lý đề tài mystery (Gộp Topics + Quản lý Topics)
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
from components.theme import inject_theme, render_step_indicator, render_project_status_bar

st.set_page_config(
    page_title="Topics - AI Mystery",
    page_icon="📋",
    layout="wide",
)

inject_theme()

# ── Load & Save ─────────────────────────────────────────────
TOPICS_FILE = PROJECT_ROOT / "data" / "mystery_topics.json"


@st.cache_data
def load_topics():
    if not TOPICS_FILE.exists():
        return {"topics": []}
    data = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    return data


def save_topics(data_payload):
    TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOPICS_FILE.write_text(
        json.dumps(data_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


data = load_topics()
topics = data.get("topics", [])

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">📋 Kho Đề Tài</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Quản lý, tìm kiếm, thêm mới và tạo kịch bản từ kho đề tài AI</div>', unsafe_allow_html=True)

# Step indicator
render_step_indicator(0, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Tabs ─────────────────────────────────────────────────────
tab_view, tab_manage, tab_add = st.tabs([
    "📖 Xem & Tạo Story",
    "✏️ Quản lý (Sửa/Xóa)",
    "➕ Thêm Topic mới",
])

# ══════════════════════════════════════════════════════════════
# TAB 1: XEM & TẠO STORY
# ══════════════════════════════════════════════════════════════
with tab_view:
    if not topics:
        st.markdown('''
        <div class="empty-state">
            <div class="empty-state-icon">📋</div>
            <div class="empty-state-title">Chưa có đề tài nào</div>
            <div class="empty-state-desc">Hãy thêm topic mới từ tab "➕ Thêm Topic mới"</div>
        </div>
        ''', unsafe_allow_html=True)
        st.stop()

    # Metrics
    total = len(topics)
    pending_count = sum(1 for t in topics if t.get("status", "pending") == "pending")
    generated_count = total - pending_count

    m1, m2, m3 = st.columns(3)
    with m1:
        with st.container(border=True):
            st.metric("📦 Tổng số đề tài", total)
    with m2:
        with st.container(border=True):
            st.metric("⏳ Chờ xử lý", pending_count)
    with m3:
        with st.container(border=True):
            st.metric("✅ Đã tạo Story", generated_count)

    st.write("")

    # Filters
    with st.container(border=True):
        col_search, col_filter = st.columns([2, 1])
        with col_search:
            search = st.text_input(
                "🔍 Tìm kiếm đề tài",
                placeholder="Nhập tên đề tài, nội dung...",
                key="topics_search",
            )
        with col_filter:
            status_filter = st.segmented_control(
                "Lọc trạng thái",
                options=["Tất cả", "pending", "generated"],
                default="Tất cả",
                key="topics_filter",
            )

    # Apply filters
    filtered = topics
    if status_filter and status_filter != "Tất cả":
        filtered = [t for t in filtered if t.get("status", "pending") == status_filter]
    if search:
        sl = search.lower()
        filtered = [
            t for t in filtered
            if sl in t.get("title", "").lower()
            or sl in t.get("premise", "").lower()
        ]

    st.write("")
    st.subheader(f"Danh sách đề tài ({len(filtered)})", divider="red")

    if not filtered:
        st.info("Không tìm thấy đề tài phù hợp.", icon="🔍")
        st.stop()

    for topic in filtered:
        topic_id = topic.get("id", "N/A")
        title = topic.get("title", "Không có tiêu đề")
        status = topic.get("status", "pending")
        crime_type = topic.get("crime_type", "N/A")
        premise = topic.get("premise", "")
        badge_class = "status-badge-generated" if status == "generated" else "status-badge-pending"

        with st.expander(f"{'✅' if status == 'generated' else '⏳'} **{title}** — `{topic_id}`"):
            col_main, col_side = st.columns([2.5, 1])

            with col_main:
                st.markdown(f"**📖 Tiền đề:**\n> {premise}")
                st.markdown(f"**🕵️ Nhân vật chính:** {topic.get('protagonist', 'N/A')}")
                st.markdown(f"**❓ Bí ẩn:** {topic.get('central_mystery', 'N/A')}")

            with col_side:
                with st.container(border=True):
                    st.markdown(f"**Trạng thái:** <span class='{badge_class}'>{status.upper()}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Loại án:** `{crime_type}`")
                    st.markdown(f"**Điều tra:** `{topic.get('investigation_type', 'N/A')}`")
                    st.markdown(f"**Twist:** `{topic.get('twist_type', 'N/A')}`")
                    st.divider()

                    # Project status bar for this topic
                    has_story = topic.get("status") == "generated"
                    has_narration = False
                    has_audio = False
                    has_images = False
                    has_video = False
                    
                    project_dir = PROJECT_ROOT / "projects" / topic_id
                    if project_dir.exists():
                        if (project_dir / "story.json").exists():
                            try:
                                story_data = json.loads((project_dir / "story.json").read_text(encoding="utf-8"))
                                if story_data.get("narration"):
                                    has_narration = True
                            except Exception:
                                pass
                        if (project_dir / "audio").exists():
                            has_audio = True
                        if (project_dir / "images").exists():
                            has_images = True
                        if (project_dir / "video").exists():
                            has_video = True
                    
                    render_project_status_bar(topic_id, has_story, has_narration, has_audio, has_images, has_video)
                    
                    st.write("")
                    if topic.get("story_id"):
                        st.page_link(
                            "pages/3_📖_Xem Story.py",
                            label="📖 Xem Story",
                            use_container_width=True,
                        )
                    else:
                        st.page_link(
                            "pages/2_✍️_Tạo Story.py",
                            label="✍️ Tạo Story",
                            use_container_width=True,
                        )

# ══════════════════════════════════════════════════════════════
# TAB 2: QUẢN LÝ (SỬA / XÓA)
# ══════════════════════════════════════════════════════════════
with tab_manage:
    st.subheader("✏️ Chỉnh sửa / Xóa Topic", divider="orange")

    if not topics:
        st.info("Chưa có topic nào.", icon="ℹ️")
    else:
        topic_labels = [f"[{t['id']}] {t['title']}" for t in topics]
        selected_idx = st.selectbox(
            "🎯 Chọn Topic cần thao tác",
            range(len(topics)),
            format_func=lambda i: topic_labels[i],
            key="edit_topic_selector",
        )
        selected_topic = topics[selected_idx]

        with st.container(border=True):
            with st.form("edit_topic_form"):
                col1, col2 = st.columns(2)
                with col1:
                    edit_title = st.text_input("📌 Tiêu đề", value=selected_topic["title"])
                    edit_crime_type = st.text_input("🔪 Crime type", value=selected_topic.get("crime_type", ""))
                    edit_investigation_type = st.text_input("🔍 Investigation type", value=selected_topic.get("investigation_type", ""))
                with col2:
                    edit_protagonist = st.text_input("🕵️ Protagonist", value=selected_topic.get("protagonist", ""))
                    edit_twist_type = st.text_input("🔄 Twist type", value=selected_topic.get("twist_type", ""))
                    edit_ending_type = st.text_input("🎬 Ending type", value=selected_topic.get("ending_type", ""))

                edit_premise = st.text_area("💡 Premise", value=selected_topic.get("premise", ""), height=90)
                edit_setting = st.text_area("🏙️ Setting", value=selected_topic.get("setting", ""), height=80)
                edit_central_mystery = st.text_area("🧩 Central mystery", value=selected_topic.get("central_mystery", ""), height=70)

                st.write("")
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    submitted_edit = st.form_submit_button("💾 Cập nhật", type="primary", use_container_width=True)
                with col_btn2:
                    delete_edit = st.form_submit_button("🗑️ Xóa Topic", type="secondary", use_container_width=True)

                if submitted_edit:
                    selected_topic["title"] = edit_title.strip()
                    selected_topic["crime_type"] = edit_crime_type.strip()
                    selected_topic["investigation_type"] = edit_investigation_type.strip()
                    selected_topic["protagonist"] = edit_protagonist.strip()
                    selected_topic["twist_type"] = edit_twist_type.strip()
                    selected_topic["ending_type"] = edit_ending_type.strip()
                    selected_topic["premise"] = edit_premise.strip()
                    selected_topic["setting"] = edit_setting.strip()
                    selected_topic["central_mystery"] = edit_central_mystery.strip()
                    save_topics({"topics": topics})
                    st.success(f"Đã cập nhật `{selected_topic['id']}`", icon="✅")
                    st.rerun()

                if delete_edit:
                    deleted_id = selected_topic["id"]
                    topics.pop(selected_idx)
                    save_topics({"topics": topics})
                    st.success(f"Đã xóa `{deleted_id}`", icon="✅")
                    st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB 3: THÊM MỚI
# ══════════════════════════════════════════════════════════════
with tab_add:
    st.subheader("➕ Thêm Topic mới", divider="blue")

    with st.container(border=True):
        with st.form("add_topic_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_id = st.text_input("🆔 Topic ID", value=f"mystery-{len(topics):03d}")
                new_title = st.text_input("📌 Tiêu đề", placeholder="Cuộc gọi lúc 23:47")
                new_crime_type = st.selectbox("🔪 Crime Type", [
                    "murder", "disappearance", "kidnapping", "theft", "identity_theft",
                    "fraud", "blackmail", "cold_case", "conspiracy", "attempted_murder",
                ])
                new_investigation_type = st.selectbox("🔍 Investigation Type", [
                    "impossible_timeline", "hidden_evidence", "anonymous_message",
                    "contradictory_witnesses", "false_identity", "locked_room",
                    "cold_case_reconstruction", "digital_evidence",
                ])
            with col2:
                new_protagonist = st.text_input("🕵️ Protagonist", placeholder="điều tra viên hình sự")
                new_twist_type = st.selectbox("🔄 Twist Type", [
                    "false_timeline", "victim_is_not_who_they_seem",
                    "investigator_is_manipulated", "hidden_identity",
                    "crime_was_staged", "multiple_people_involved",
                ])
                new_ending_type = st.selectbox("🎬 Ending Type", [
                    "fully_resolved", "bittersweet", "final_reversal",
                    "ambiguous", "justice",
                ])

            new_premise = st.text_area("💡 Premise", placeholder="Một người đàn ông gọi điện cho cảnh sát...", height=90)
            new_setting = st.text_area("🏙️ Setting", placeholder="Khu chung cư cũ tại quận Đống Đa...", height=80)
            new_central_mystery = st.text_area("🧩 Central Mystery", placeholder="Ai là người thực sự gọi báo án?", height=70)

            submitted_add = st.form_submit_button("➕ Lưu Topic mới", type="primary", use_container_width=True)

            if submitted_add:
                if not new_title.strip():
                    st.error("Vui lòng nhập Tiêu đề!", icon="🚨")
                elif any(t["id"] == new_id for t in topics):
                    st.error(f"ID '{new_id}' đã tồn tại!", icon="🚨")
                else:
                    new_topic = {
                        "id": new_id,
                        "title": new_title.strip(),
                        "premise": new_premise.strip(),
                        "setting": new_setting.strip(),
                        "crime_type": new_crime_type,
                        "investigation_type": new_investigation_type,
                        "protagonist": new_protagonist.strip(),
                        "central_mystery": new_central_mystery.strip(),
                        "twist_type": new_twist_type,
                        "ending_type": new_ending_type,
                        "status": "pending",
                        "story_id": None,
                    }
                    topics.append(new_topic)
                    save_topics({"topics": topics})
                    st.success(f"Đã thêm `{new_id}`", icon="✅")
                    st.rerun()
