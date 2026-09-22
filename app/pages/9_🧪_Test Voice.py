"""
🧪 Test Voice - Thử nghiệm giọng đọc (Edge TTS + Fish Speech)
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import edge_tts
import streamlit as st

load_dotenv()

st.set_page_config(
    page_title="Test Voice - AI Mystery",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 Test Voice (Thử nghiệm Giọng đọc)")
st.caption("So sánh Edge TTS (Microsoft) vs Fish Speech (AI Voice)")

# ── TTS Engine Selection ─────────────────────────────────────
st.subheader("🎙️ Chọn Engine TTS", divider="blue")

tts_engine = st.radio(
    "Chọn engine TTS",
    [
        "Edge TTS (Miễn phí, nhanh, giọng Microsoft)",
        "VieNeu-TTS (Miễn phí, local, dấu VN chuẩn nhất)",
        "Fish Speech (Miễn phí, giọng Việt kể chuyện, cần API key)",
    ],
    horizontal=True,
)

# ── Comparison Table ─────────────────────────────────────────
with st.expander("📊 So sánh 3 Engine", expanded=False):
    st.markdown("""
| Tiêu chí | Edge TTS | VieNeu-TTS | Fish Speech |
|---|---|---|---|
| **Giọng Việt** | 2 giọng (Microsoft) | 23 giọng (3 miền, đọc truyện) | 7 giọng Việt kể chuyện |
| **Chất lượng** | ⭐⭐⭐⭐ Giọng chuẩn | ⭐⭐⭐⭐⭐ 48kHz tự nhiên | ⭐⭐⭐⭐ Truyền cảm |
| **Dấu tiếng Việt** | ✅ Chuẩn | ✅✅ Chuẩn nhất (phonemizer VN) | ⚠️ Hay mất dấu |
| **Tốc độ** | ⚡ Rất nhanh | 🐢 Chậm ~1-2x thời lượng | 🐢 Chậm hơn Edge |
| **Rate/Pitch** | ✅ Rate + Pitch | ✅ Speed | ✅ Speed |
| **API key** | ❌ Không cần | ❌ Không cần (chạy local) | ✅ Cần (miễn phí) |
| **Chạy local** | ❌ Cloud | ✅ Có | ❌ Cloud |
    """)

# ── Edge TTS Settings ────────────────────────────────────────
if "Edge TTS" in tts_engine:
    st.subheader("⚙️ Cấu hình Edge TTS", divider="gray")

    @st.cache_data
    def get_voices():
        voices = asyncio.run(edge_tts.VoicesManager.create())
        vi_voices = [v for v in voices.voices if v["Locale"].startswith("vi-VN")]
        return vi_voices

    vi_voices = get_voices()

    if "voice_rate" not in st.session_state:
        st.session_state["voice_rate"] = "-7%"
    if "voice_pitch" not in st.session_state:
        st.session_state["voice_pitch"] = "-2Hz"

    with st.container(border=True):
        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            voice_options = {v["ShortName"]: v["FriendlyName"] for v in vi_voices}
            selected_voice = st.selectbox(
                "🎙️ Chọn Voice (Tiếng Việt)",
                list(voice_options.keys()),
                format_func=lambda x: f"{x} - {voice_options[x]}",
            )

        with col2:
            rate = st.text_input(
                "⚡ Tốc độ (Rate)",
                value=st.session_state["voice_rate"],
                help="Tỷ lệ tốc độ đọc. VD: -5%, -10%, +5%",
            )
            st.session_state["voice_rate"] = rate

        with col3:
            pitch = st.text_input(
                "🎵 Cao độ (Pitch)",
                value=st.session_state["voice_pitch"],
                help="Tần số giọng. VD: -1Hz, -3Hz, +2Hz",
            )
            st.session_state["voice_pitch"] = pitch

    st.write("")

    # Quick Presets
    st.subheader("🎛️ Thiết lập nhanh", divider="gray")

    with st.container(border=True):
        col_p1, col_p2, col_p3, col_p4 = st.columns(4)

        with col_p1:
            if st.button("📌 Mặc định", use_container_width=True):
                st.session_state["voice_rate"] = "-7%"
                st.session_state["voice_pitch"] = "-2Hz"
                st.rerun()

        with col_p2:
            if st.button("⚡ Nhanh", use_container_width=True):
                st.session_state["voice_rate"] = "-3%"
                st.session_state["voice_pitch"] = "-0Hz"
                st.rerun()

        with col_p3:
            if st.button("🐢 Chậm", use_container_width=True):
                st.session_state["voice_rate"] = "-12%"
                st.session_state["voice_pitch"] = "-4Hz"
                st.rerun()

        with col_p4:
            if st.button("✨ Trẻ", use_container_width=True):
                st.session_state["voice_rate"] = "-5%"
                st.session_state["voice_pitch"] = "+2Hz"
                st.rerun()

# ── Fish Speech Settings ─────────────────────────────────────
elif "VieNeu" in tts_engine:
    st.subheader("⚙️ Cấu hình VieNeu-TTS", divider="gray")

    from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
        VIENEU_VOICES,
    )

    with st.container(border=True):
        st.info(
            "💡 VieNeu-TTS v3 Turbo chạy ngay trên máy bạn (CPU, 48kHz). "
            "Giọng đọc truyện tự nhiên nhất, dấu tiếng Việt chuẩn. "
            "Lần đầu tải model ~282MB, các lần sau tải trong ~15-60s.",
            icon="ℹ️",
        )

        col1, col2 = st.columns(2)

        with col1:
            vieneu_voice = st.selectbox(
                "🎙️ Giọng Việt (built-in)",
                list(VIENEU_VOICES.keys()),
                index=1,
                help="Thái Sơn (Nam·Nam kể chuyện) hợp kinh dị; Mỹ Duyên (Nữ·Nam đọc truyện)",
            )

        with col2:
            vieneu_speed = st.slider(
                "⚡ Tốc độ (Speed)",
                min_value=0.5,
                max_value=2.0,
                value=1.0,
                step=0.05,
                help="0.9-1.0: Chậm rãi, trầm lắng (phù hợp kinh dị)",
            )

        st.markdown("**📢 Giọng built-in:**")
        for name in VIENEU_VOICES:
            st.caption(f"• {name}")

else:
    st.subheader("⚙️ Cấu hình Fish Speech", divider="gray")

    from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
        VIETNAMESE_VOICES,
    )

    with st.container(border=True):
        st.info(
            "💡 Fish Speech dùng giọng Việt được train riêng cho kể chuyện. "
            "Trầm, ấm, truyền cảm hơn giọng Microsoft. "
            "Lấy API key miễn phí tại: https://fish.audio/app/api-keys",
            icon="ℹ️",
        )

        col1, col2 = st.columns(2)

        with col1:
            fish_api_key = st.text_input(
                "🔑 Fish Audio API Key",
                value="",
                type="password",
                help="Lấy key miễn phí tại fish.audio",
            )

            fish_model = st.selectbox(
                "🤖 Model",
                ["s2.1-pro-free", "s2.1-pro", "s2-pro"],
                index=0,
                help="s2.1-pro-free: Miễn phí",
            )

            fish_voice = st.selectbox(
                "🎙️ Giọng Việt Kể Chuyện",
                list(VIETNAMESE_VOICES.keys()),
                index=0,
                help="Chọn giọng phù hợp mood truyện",
            )

        with col2:
            fish_speed = st.slider(
                "⚡ Tốc độ (Speed)",
                min_value=0.5,
                max_value=2.0,
                value=0.9,
                step=0.1,
                help="0.8-0.9: Chậm, trầm lắng (phù hợp kinh dị)",
            )

            st.write("")
            st.markdown("**📢 Giọng Việt Kể Chuyện:**")
            for name in VIETNAMESE_VOICES:
                st.caption(f"• {name}")

# ── Sample Text & Generation ─────────────────────────────────
st.write("")
st.subheader("📝 Văn bản Thử nghiệm", divider="orange")

default_text = """Con ngõ sâu hút ở Khâm Thiên giống như một cái giếng cạn. Xung quanh, những ngôi nhà cao tầng mọc lên như những bức tường bê tông xám xịt, nuốt chửng gần như toàn bộ ánh nắng mặt trời.

Căn nhà ẩm thấp, xộc lên mùi vữa mục và ẩm mốc lâu ngày...

Cho đến đúng hai giờ sáng.

Minh bừng tỉnh. Đôi mắt anh mở to trong bóng tối.

Không phải vì một cơn ác mộng. Mà vì một âm thanh vừa vang lên ngay phía trên mặt mình.

Một tiếng động gọn lỏn, đục ngầu... giống như gót chân trần vừa nảy xuống sàn gỗ."""

with st.container(border=True):
    sample_text = st.text_area(
        "Nội dung văn bản mẫu",
        value=default_text,
        height=200,
        help="Nhập đoạn văn bản bất kỳ để kiểm tra chất lượng đọc",
    )

    st.write("")

    if st.button("🔊 Tạo Audio Thử Nghiệm", type="primary", use_container_width=True):
        output_dir = PROJECT_ROOT / "voice_tests"
        output_dir.mkdir(exist_ok=True)

        if "Edge TTS" in tts_engine:
            with st.spinner("⏳ Đang tạo audio bằng Edge TTS..."):
                try:
                    safe_rate = rate.replace("%", "p")
                    safe_pitch = pitch.replace("Hz", "hz")
                    output_file = output_dir / f"test_edge_{selected_voice}_{safe_rate}_{safe_pitch}.mp3"

                    async def generate_edge():
                        communicate = edge_tts.Communicate(
                            text=sample_text,
                            voice=selected_voice,
                            rate=rate,
                            pitch=pitch,
                        )
                        await communicate.save(str(output_file))

                    asyncio.run(generate_edge())

                    st.success(f"Đã tạo: `{output_file.name}`", icon="✅")

                    col_res1, col_res2 = st.columns([3, 1])
                    with col_res1:
                        st.audio(str(output_file), format="audio/mp3")
                    with col_res2:
                        with open(output_file, "rb") as f:
                            st.download_button(
                                label="⬇️ Tải MP3",
                                data=f.read(),
                                file_name=output_file.name,
                                mime="audio/mp3",
                                use_container_width=True,
                            )

                except Exception as e:
                    st.error(f"Lỗi: {e}", icon="🚨")
                    st.exception(e)

        elif "VieNeu" in tts_engine:
            # VieNeu-TTS (local)
            with st.spinner("⏳ Đang tạo audio bằng VieNeu-TTS (local, chậm hơn Edge)..."):
                try:
                    from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
                        VieNeuTTSProvider,
                    )

                    voice_name = VIENEU_VOICES[vieneu_voice]
                    provider = VieNeuTTSProvider(voice=voice_name, speed=vieneu_speed)

                    safe_voice = voice_name.replace(" ", "_").lower()
                    output_file = output_dir / f"test_vieneu_{safe_voice}.mp3"
                    duration = provider.generate(sample_text, output_file)

                    st.success(
                        f"Đã tạo: `{output_file.name}` ({duration:.1f}s)",
                        icon="✅",
                    )

                    col_res1, col_res2 = st.columns([3, 1])
                    with col_res1:
                        st.audio(str(output_file), format="audio/mp3")
                    with col_res2:
                        with open(output_file, "rb") as f:
                            st.download_button(
                                label="⬇️ Tải MP3",
                                data=f.read(),
                                file_name=output_file.name,
                                mime="audio/mp3",
                                use_container_width=True,
                            )

                except Exception as e:
                    st.error(f"Lỗi: {e}", icon="🚨")
                    st.exception(e)

        else:
            # Fish Speech
            if not fish_api_key:
                st.warning("⚠️ Vui lòng nhập API Key Fish Audio!", icon="⚠️")
            else:
                with st.spinner("⏳ Đang tạo audio bằng Fish Speech..."):
                    try:
                        from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
                            FishSpeechProvider,
                        )

                        provider = FishSpeechProvider(
                            api_key=fish_api_key,
                            model=fish_model,
                            speed=fish_speed,
                            voice_name=fish_voice,
                        )

                        output_file = output_dir / f"test_fish_{fish_voice.replace(' ', '_').lower()}.mp3"
                        duration = provider.generate(sample_text, output_file)

                        st.success(
                            f"Đã tạo: `{output_file.name}` ({duration:.1f}s)",
                            icon="✅",
                        )

                        col_res1, col_res2 = st.columns([3, 1])
                        with col_res1:
                            st.audio(str(output_file), format="audio/mp3")
                        with col_res2:
                            with open(output_file, "rb") as f:
                                st.download_button(
                                    label="⬇️ Tải MP3",
                                    data=f.read(),
                                    file_name=output_file.name,
                                    mime="audio/mp3",
                                    use_container_width=True,
                                )

                    except Exception as e:
                        st.error(f"Lỗi: {e}", icon="🚨")
                        st.exception(e)

st.write("")

# ── History ──────────────────────────────────────────────────
st.subheader("📁 Lịch sử File Audio Thử Nghiệm", divider="green")

test_dir = PROJECT_ROOT / "voice_tests"

if test_dir.exists():
    test_files = sorted(test_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True)
    if test_files:
        with st.container(border=True):
            st.caption(f"Hiển thị 10 file mới nhất ({len(test_files)} file)")
            for test_file in test_files[:10]:
                file_size = test_file.stat().st_size / 1024
                with st.expander(f"🔊 {test_file.name} ({file_size:.1f} KB)"):
                    st.audio(str(test_file), format="audio/mp3")
                    with open(test_file, "rb") as f:
                        st.download_button(
                            label="⬇️ Download",
                            data=f.read(),
                            file_name=test_file.name,
                            mime="audio/mp3",
                            key=f"dl_{test_file.name}",
                        )
    else:
        st.info("Chưa có file audio thử nghiệm nào.", icon="ℹ️")
else:
    st.info("Chưa có file audio thử nghiệm nào.", icon="ℹ️")
