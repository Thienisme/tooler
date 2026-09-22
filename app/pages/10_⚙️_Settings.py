"""
⚙️ Settings - Cài đặt dự án
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
from components.theme import inject_theme

st.set_page_config(
    page_title="Settings - AI Mystery",
    page_icon="⚙️",
    layout="wide",
)

inject_theme()

# ── Header ───────────────────────────────────────────────────
st.markdown('<div class="hero-title" style="font-size:2.2rem;">⚙️ Settings</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Cấu hình giọng đọc, thông số hệ thống và quản lý API</div>', unsafe_allow_html=True)

# ── Load current .env ────────────────────────────────────────
ENV_FILE = PROJECT_ROOT / ".env"


def load_env():
    if not ENV_FILE.exists():
        return {}
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def save_env(env_data: dict):
    lines = []
    for key, value in env_data.items():
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


env = load_env()

# ══════════════════════════════════════════════════════════════
# SECTION 1: VOICE SETTINGS (Ưu tiên cao - hiển thị đầu tiên)
# ══════════════════════════════════════════════════════════════
st.subheader("🎤 Giọng đọc & EdgeTTS", divider="orange")

with st.container(border=True):
    with st.form("voice_settings_form"):
        col_v1, col_v2, col_v3 = st.columns(3)

        with col_v1:
            voice = st.selectbox(
                "🎙️ Giọng mặc định",
                ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"],
                index=0
                if env.get("EDGE_TTS_VOICE", "vi-VN-NamMinhNeural")
                == "vi-VN-NamMinhNeural"
                else 1,
                help="Chọn giọng Nam hoặc Nữ Tiếng Việt",
            )

        with col_v2:
            rate = st.text_input(
                "⚡ Tốc độ (Rate)",
                value=env.get("EDGE_TTS_RATE", "-7%"),
                help="VD: -5%, -10%, +5%",
            )

        with col_v3:
            pitch = st.text_input(
                "🎵 Cao độ (Pitch)",
                value=env.get("EDGE_TTS_PITCH", "-2Hz"),
                help="VD: -1Hz, -3Hz, +2Hz",
            )

        st.markdown("---")

        col_c1, col_c2, col_c3 = st.columns(3)

        with col_c1:
            max_chars = st.number_input(
                "📏 Ký tự tối đa / Chunk",
                value=int(env.get("EDGE_TTS_MAX_CHARS", "1300")),
                min_value=100,
                max_value=5000,
                step=100,
                help="Chia nhỏ văn bản nếu quá dài",
            )

        with col_c2:
            chunk_pause = st.number_input(
                "⏱️ Nghỉ giữa Chunk (ms)",
                value=int(env.get("EDGE_TTS_CHUNK_PAUSE_MS", "150")),
                min_value=0,
                max_value=1000,
                step=50,
                help="Khoảng nghỉ giữa hai đoạn audio",
            )

        with col_c3:
            tts_timeout = st.number_input(
                "⏳ Timeout TTS (giây)",
                value=int(env.get("EDGE_TTS_TIMEOUT_SECONDS", "120")),
                min_value=30,
                max_value=600,
                step=10,
                help="Thời gian chờ tối đa mỗi request",
            )

        st.write("")
        submitted_voice = st.form_submit_button(
            "💾 Lưu Voice Settings", type="primary", use_container_width=True
        )

        if submitted_voice:
            env["EDGE_TTS_VOICE"] = voice
            env["EDGE_TTS_RATE"] = rate
            env["EDGE_TTS_PITCH"] = pitch
            env["EDGE_TTS_MAX_CHARS"] = str(max_chars)
            env["EDGE_TTS_CHUNK_PAUSE_MS"] = str(chunk_pause)
            env["EDGE_TTS_TIMEOUT_SECONDS"] = str(tts_timeout)
            save_env(env)
            st.success("Đã lưu Voice Settings!", icon="✅")
            st.rerun()

st.write("")

# ══════════════════════════════════════════════════════════════
# SECTION 2: FISH SPEECH SETTINGS
# ══════════════════════════════════════════════════════════════
st.subheader("🎭 Fish Speech (Giọng cảm xúc)", divider="violet")

from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
    VIETNAMESE_VOICES,
    DEFAULT_VOICE_NAME,
)

saved_ref = env.get("FISH_REFERENCE_ID", "")
current_voice_name = DEFAULT_VOICE_NAME
for name, ref_id in VIETNAMESE_VOICES.items():
    if ref_id == saved_ref:
        current_voice_name = name
        break

with st.container(border=True):
    with st.form("fish_speech_form"):
        col_f1, col_f2 = st.columns(2)

        with col_f1:
            fish_speed = st.number_input(
                "⚡ Tốc độ (Speed)",
                value=float(env.get("FISH_SPEED", "1.0")),
                min_value=0.5,
                max_value=2.0,
                step=0.1,
                help="1.0 = tốc độ thường",
            )

            fish_voice_name = st.selectbox(
                "🎙️ Giọng Việt",
                list(VIETNAMESE_VOICES.keys()),
                index=list(VIETNAMESE_VOICES.keys()).index(current_voice_name)
                if current_voice_name in VIETNAMESE_VOICES else 0,
            )

        with col_f2:
            fish_model = st.selectbox(
                "🤖 Model",
                ["s2.1-pro-free", "s2.1-pro", "s2-pro"],
                index=0 if env.get("FISH_MODEL", "s2.1-pro-free") == "s2.1-pro-free" else 1,
                help="s2.1-pro-free: Miễn phí",
            )

            st.markdown("**📢 Giọng có sẵn:**")
            for name in VIETNAMESE_VOICES.keys():
                st.caption(f"• {name}")

        st.write("")
        submitted_fish = st.form_submit_button(
            "💾 Lưu Fish Speech", type="primary", use_container_width=True
        )

        if submitted_fish:
            env["FISH_MODEL"] = fish_model
            env["FISH_SPEED"] = str(fish_speed)
            env["FISH_REFERENCE_ID"] = VIETNAMESE_VOICES[fish_voice_name]
            save_env(env)
            st.success("Đã lưu Fish Speech Settings!", icon="✅")
            st.rerun()

st.write("")

# ══════════════════════════════════════════════════════════════
# SECTION 3: PIPELINE SETTINGS
# ══════════════════════════════════════════════════════════════
st.subheader("🔄 Pipeline", divider="gray")

with st.container(border=True):
    with st.form("pipeline_settings_form"):
        col_p1, col_p2 = st.columns(2)

        with col_p1:
            gemini_delay = st.number_input(
                "⏳ Delay giữa Request Gemini (giây)",
                value=int(env.get("GEMINI_NARRATION_DELAY_SECONDS", "10")),
                min_value=0,
                max_value=60,
                step=1,
                help="Tránh Rate Limit",
            )

        with col_p2:
            pipeline_delay = st.number_input(
                "⏳ Delay giữa Bước Pipeline (giây)",
                value=int(env.get("PIPELINE_DELAY_SECONDS", "10")),
                min_value=0,
                max_value=60,
                step=1,
                help="Nghỉ giữa các bước chạy tự động",
            )

        st.write("")
        submitted_pipeline = st.form_submit_button(
            "💾 Lưu Pipeline Settings", type="primary", use_container_width=True
        )

        if submitted_pipeline:
            env["GEMINI_NARRATION_DELAY_SECONDS"] = str(gemini_delay)
            env["PIPELINE_DELAY_SECONDS"] = str(pipeline_delay)
            save_env(env)
            st.success("Đã lưu Pipeline Settings!", icon="✅")
            st.rerun()

st.write("")


