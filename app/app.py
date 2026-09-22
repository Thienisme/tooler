"""
AI Mystery Story - Frontend
Truyện trinh thám tự động bằng AI
"""

import sys
from pathlib import Path

# Add project root to path so we can import src modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

# Inject theme
sys.path.insert(0, str(Path(__file__).resolve().parent))
from components.theme import inject_theme

st.set_page_config(
    page_title="AI Mystery Story",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 AI Mystery Story")
    st.caption("v2.0 • Automated Detective Video Engine")
    st.divider()

    st.markdown("### 📖 Quy trình")
    st.markdown(
        """
**📝 Thủ công:**
1. 📋 Topics — Quản lý đề tài
2. ✍️ Tạo Story — Sinh kịch bản
3. 📖 Xem Story — Đọc & sửa
4. 🎙️ Narration — Tạo thuyết minh
5. 📄 Export — Review text
6. 🔊 Audio — Tạo voice-over
7. 🖼️ Tạo Ảnh — Sinh ảnh minh họa
8. 🎬 Tạo Video — Ghép video

**⚡ Tự động:**
- 🔄 Pipeline — Chạy tất cả
        """
    )

    st.divider()

    # Project quick status
    projects_dir = PROJECT_ROOT / "projects"
    if projects_dir.exists():
        project_count = sum(1 for d in projects_dir.iterdir() if d.is_dir())
        audio_count = sum(
            1 for d in projects_dir.iterdir()
            if d.is_dir() and (d / "audio").exists()
        )
        video_count = sum(
            1 for d in projects_dir.iterdir()
            if d.is_dir() and (d / "video").exists()
        )
        st.caption(f"📊 {project_count} stories • {audio_count} audio • {video_count} video")
    
    st.caption("System Status: 🟢 Online")

# ── Main Page ────────────────────────────────────────────────

st.markdown('<div class="hero-title">🔍 AI Mystery Story</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-subtitle">Hệ thống tạo video truyện trinh thám tự động bằng AI</div>',
    unsafe_allow_html=True,
)

st.write("")

# ── Metric Dashboard ─────────────────────────────────────────
projects_dir = PROJECT_ROOT / "projects"
generated_stories = 0
audio_count = 0
video_count = 0
narration_count = 0

if projects_dir.exists():
    for d in projects_dir.iterdir():
        if d.is_dir():
            if (d / "story.json").exists():
                generated_stories += 1
                # Check for narration in story.json
                try:
                    import json
                    story_data = json.loads((d / "story.json").read_text(encoding="utf-8"))
                    if story_data.get("narration"):
                        narration_count += 1
                except Exception:
                    pass
            if (d / "audio").exists():
                audio_count += 1
            if (d / "video").exists():
                video_count += 1

# Topics count
topics_file = PROJECT_ROOT / "data" / "mystery_topics.json"
topic_count = 0
pending_count = 0
if topics_file.exists():
    try:
        topics_data = json.loads(topics_file.read_text(encoding="utf-8"))
        topic_list = topics_data.get("topics", [])
        topic_count = len(topic_list)
        pending_count = sum(1 for t in topic_list if t.get("status") == "pending")
    except Exception:
        pass

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    with st.container(border=True):
        st.metric("📋 Topics", topic_count, f"{pending_count} pending")
with m2:
    with st.container(border=True):
        st.metric("📖 Stories", generated_stories)
with m3:
    with st.container(border=True):
        st.metric("🎙️ Narrations", narration_count)
with m4:
    with st.container(border=True):
        st.metric("🔊 Audios", audio_count)
with m5:
    with st.container(border=True):
        st.metric("🎬 Videos", video_count)

st.write("")

# ── Tech Stack ───────────────────────────────────────────────
st.subheader("🔧 Công nghệ cốt lõi")
c1, c2, c3, c4 = st.columns(4)

techs = [
    ("🤖 AI Text", "Google Gemini"),
    ("🗣️ Text-to-Speech", "Edge TTS (Microsoft)"),
    ("🎞️ Audio Assembly", "FFmpeg Engine"),
    ("🎨 Frontend", "Streamlit Framework"),
]

with c1:
    with st.container(border=True):
        st.markdown(f"**{techs[0][0]}**")
        st.caption(techs[0][1])
with c2:
    with st.container(border=True):
        st.markdown(f"**{techs[1][0]}**")
        st.caption(techs[1][1])
with c3:
    with st.container(border=True):
        st.markdown(f"**{techs[2][0]}**")
        st.caption(techs[2][1])
with c4:
    with st.container(border=True):
        st.markdown(f"**{techs[3][0]}**")
        st.caption(techs[3][1])

st.write("")

# ── Pipeline Flow ────────────────────────────────────────────
st.subheader("🔄 Quy trình xử lý")
st.markdown(
    """
    <div class="pipeline-box">
        📋 Topic ➔ <b>Gemini</b> (Story) ➔ <b>Gemini</b> (Narration) ➔ <b>Edge TTS</b> (Audio) ➔ <b>Pollinations</b> (Images) ➔ <b>FFmpeg</b> (Video)
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")

# ── Quick Actions ────────────────────────────────────────────
st.subheader("🚀 Bắt đầu ngay")

q1, q2, q3, q4 = st.columns(4)
with q1:
    st.page_link("pages/1_📋_Topics.py", label="📋 Xem Topics", use_container_width=True)
with q2:
    st.page_link("pages/11_🔄_Pipeline.py", label="🔄 Chạy Pipeline", use_container_width=True)
with q3:
    st.page_link("pages/9_🧪_Test Voice.py", label="🧪 Test Voice", use_container_width=True)
with q4:
    st.page_link("pages/10_⚙️_Settings.py", label="⚙️ Settings", use_container_width=True)
