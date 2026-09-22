"""
🖼️ Tạo Ảnh - Tạo ảnh minh họa cho từng Beat story
"""

import json
import re
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
    page_title="Tạo Ảnh - AI Mystery",
    page_icon="🖼️",
    layout="wide",
)

inject_theme()

st.markdown('<div class="hero-title" style="font-size:2.2rem;">🖼️ Tạo Ảnh</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">Tự động sinh ảnh minh họa cho từng Beat story bằng AI (Pollinations.ai)</div>', unsafe_allow_html=True)

render_step_indicator(5, ["Topics", "Story", "Narration", "Audio", "Ảnh", "Video"])

# ── Config ───────────────────────────────────────────────────
PROJECTS_DIR = PROJECT_ROOT / "projects"


def load_topics():
    topics_file = PROJECT_ROOT / "data" / "mystery_topics.json"
    if not topics_file.exists():
        return []
    data = json.loads(topics_file.read_text(encoding="utf-8"))
    return data.get("topics", [])


def load_story(topic_id):
    story_file = PROJECTS_DIR / topic_id / "story.json"
    if not story_file.exists():
        return None
    from ai_mystery_story.infrastructure.serializers.story_serializer import (
        StorySerializer,
    )
    data = json.loads(story_file.read_text(encoding="utf-8"))
    return StorySerializer.from_dict(data)


# ── Helpers for image listing & manual upload ────────────────
VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def list_project_images(images_dir: Path) -> list:
    """List all valid images in the project images dir (sorted by name)."""
    if not images_dir.exists():
        return []
    return sorted(
        p for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_IMAGE_EXTS
    )


def detect_beat_number(filename: str, max_beat: int):
    """
    Detect beat number from an uploaded filename.

    Supports: beat_03.jpg, beat 3.png, anh-2.jpeg, 5.jpg ...
    Returns beat number (1-based) or None if undetectable / out of range.
    """
    stem = Path(filename).stem.lower()

    match = re.search(r"beat[\s_\-]*(\d{1,3})", stem)
    if not match:
        match = re.search(r"(\d{1,3})", stem)

    if not match:
        return None

    number = int(match.group(1))
    if 1 <= number <= max_beat:
        return number
    return None


# ── Load topics ──────────────────────────────────────────────
topics = load_topics()
generated_topics = [t for t in topics if t.get("status") == "generated"]

if not generated_topics:
    st.warning("Chưa có story nào. Hãy tạo story trước!", icon="⚠️")
    st.page_link("pages/2_✍️_Tạo Story.py", label="✍️ Tạo Story")
    st.stop()

# ── Select topic ─────────────────────────────────────────────
topic_options = {t["id"]: t["title"] for t in generated_topics}
topic_ids = list(topic_options.keys())
topic_labels = [f"[{tid}] {topic_options[tid]}" for tid in topic_ids]

with st.container(border=True):
    selected_idx = st.selectbox(
        "🎯 Chọn Story để tạo ảnh",
        range(len(topic_ids)),
        format_func=lambda i: topic_labels[i],
    )

selected_topic = generated_topics[selected_idx]
topic_id = selected_topic["id"]
story = load_story(topic_id)

if story is None:
    st.error("Không tìm thấy story.", icon="🚨")
    st.stop()

if story.narration is None:
    st.warning("Story chưa có narration. Hãy tạo narration trước!", icon="⚠️")
    st.page_link("pages/4_🎙️_Narration.py", label="🎙️ Tạo Narration")
    st.stop()

# ── Story Info ───────────────────────────────────────────────
st.write("")
st.subheader(f"📖 {story.blueprint.title}", divider="red")

narration_text = story.narration.full_text()
m1, m2, m3 = st.columns(3)
with m1:
    st.metric("📝 Độ dài narration", f"{len(narration_text):,} ký tự")
with m2:
    st.metric("🧩 Số segments", len(story.narration.segments))
with m3:
    total_duration = sum(b.target_duration_minutes for b in story.blueprint.beats)
    st.metric("⏱️ Thời lượng dự kiến", f"{total_duration} phút")

# ── Step 1: Review Full Text ─────────────────────────────────
st.write("")
st.subheader("📝 Bước 1: Review toàn bộ Text", divider="blue")

with st.container(border=True):
    st.info("Đọc lại toàn bộ narration trước khi tạo ảnh. Đảm bảo nội dung đã chính xác.", icon="ℹ️")

    st.text_area(
        "Nội dung narration đầy đủ",
        value=narration_text,
        height=400,
        disabled=True,
        key="review_text",
    )

    st.caption(f"📊 {len(narration_text):,} ký tự | {len(narration_text.split()):,} từ")

# ── Step 2: Preview Image Prompts (Beat-based) ──────────────
st.write("")
st.subheader("🎨 Bước 2: Xem trước Prompt cho từng Beat", divider="orange")

with st.container(border=True):
    st.info("Mỗi Beat sẽ được tạo 1 ảnh, duration ảnh = duration Beat. Ảnh sẽ sync với nội dung audio.", icon="ℹ️")

    # Prepare beats data with narration text
    beats_data = []
    for beat in story.blueprint.beats:
        # Find corresponding narration segment
        narration_text_for_beat = ""
        for seg in story.narration.segments:
            if seg.order == beat.order:
                narration_text_for_beat = seg.text
                break
        
        beats_data.append({
            "order": beat.order,
            "title": beat.title,
            "summary": beat.summary,
            "text": narration_text_for_beat or beat.summary,
            "duration_minutes": beat.target_duration_minutes,
        })
    
    # Cache prompts in session to avoid calling Gemini on every rerun
    # (key includes narration length so edits auto-invalidate the cache)
    prompts_cache_key = f"image_prompts_{topic_id}_{len(narration_text)}"
    cached_prompts = st.session_state.get(prompts_cache_key)

    col_cache, col_regen = st.columns([3, 1])
    with col_cache:
        st.caption("💾 Prompt được sinh 1 lần và lưu tạm trong phiên. Bấm **Sinh lại Prompt** nếu bạn vừa sửa story/narration.")
    with col_regen:
        if st.button("🔄 Sinh lại Prompt", use_container_width=True):
            for key in [k for k in st.session_state if str(k).startswith("image_prompts_")]:
                del st.session_state[key]
            st.rerun()

    if cached_prompts is not None:
        prompts = cached_prompts["prompts"]
        thumbnail_prompt = cached_prompts["thumbnail"]
    else:
        from ai_mystery_story.infrastructure.image.image_prompt_generator import (
            ImagePromptGenerator,
        )

        with st.spinner("🤖 Đang phân tích nội dung để sinh prompt ảnh bằng Gemini..."):
            prompt_gen = ImagePromptGenerator(use_gemini=True)

            prompts = prompt_gen.generate_prompts_from_beats(
                beats=beats_data,
                story_title=story.blueprint.title,
                story_premise=story.blueprint.premise,
            )

            thumbnail_prompt = prompt_gen.generate_thumbnail_prompt(
                story_title=story.blueprint.title,
                story_premise=story.blueprint.premise,
                narration_text=narration_text,
            )

            st.session_state[prompts_cache_key] = {
                "prompts": prompts,
                "thumbnail": thumbnail_prompt,
            }

    # Display prompts with duration info
    total_image_duration = 0
    for i, p in enumerate(prompts, 1):
        duration_min = p["duration_seconds"] / 60
        total_image_duration += p["duration_seconds"]
        with st.expander(
            f"🖼️ Beat {p['beat_order']}: {p['beat_title']} ({duration_min:.0f} phút)",
            expanded=False,
        ):
            st.markdown(f"**Prompt:** `{p['prompt']}`")
            st.markdown(f"**Text preview:** {p['text_preview']}")
            st.markdown(f"**Duration:** {duration_min:.0f} phút ({p['duration_seconds']:.0f}s)")
            st.markdown(f"**Filename:** `{p['filename']}`")
            st.markdown(f"**Seed:** `{p['seed']}`")

    # Thumbnail prompt
    with st.expander(f"🖼️ Thumbnail: {thumbnail_prompt['filename']}", expanded=False):
        st.markdown(f"**Prompt:** `{thumbnail_prompt['prompt']}`")
        st.markdown(f"**Seed:** `{thumbnail_prompt['seed']}`")

    # Summary
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🖼️ Tổng số ảnh", len(prompts) + 1)
    with col2:
        st.metric("⏱️ Tổng duration ảnh", f"{total_image_duration/60:.0f} phút")
    with col3:
        st.metric("⏱️ Duration audio", f"{total_duration} phút")

# ── Step 3: Generate Images ──────────────────────────────────
st.write("")
st.subheader("🚀 Bước 3: Tạo Ảnh", divider="green")

images_dir = PROJECTS_DIR / topic_id / "images"
total_beats = len(story.blueprint.beats)
existing_images = list_project_images(images_dir)

if existing_images:
    st.success(f"Đã có {len(existing_images)} ảnh trong thư mục images/", icon="✅")

tab_auto, tab_manual = st.tabs(["🤖 Tự động tạo bằng AI", "📤 Tải ảnh thủ công"])

# ══════════════════════════════════════════════════════════════
# TAB A: AUTO GENERATION (Pollinations.ai)
# ══════════════════════════════════════════════════════════════
with tab_auto:
    if existing_images:
        # Show existing images
        with st.container(border=True):
            cols = st.columns(4)
            for i, img in enumerate(existing_images[:8]):
                with cols[i % 4]:
                    st.image(str(img), caption=img.name, use_container_width=True)

        if st.button("🔄 Tạo lại tất cả ảnh", type="secondary", use_container_width=True):
            st.session_state["generate_images"] = True
            st.rerun()
    else:
        st.info("Chưa có ảnh nào. Bấm nút bên dưới để tạo.", icon="ℹ️")

    # Generate button
    if st.button("✨ Tạo Ảnh cho tất cả Beat + Thumbnail", type="primary", use_container_width=True):
        st.session_state["generate_images"] = True
        st.rerun()

# ══════════════════════════════════════════════════════════════
# TAB B: MANUAL UPLOAD
# ══════════════════════════════════════════════════════════════
with tab_manual:
    # Beat coverage status
    missing_beats = [
        i for i in range(1, total_beats + 1)
        if not any(f"beat_{i:02d}" in p.name for p in existing_images)
    ]

    with st.container(border=True):
        if missing_beats:
            missing_labels = ", ".join(f"beat_{i:02d}" for i in missing_beats)
            st.warning(f"**Thiếu ảnh cho {len(missing_beats)} beat:** {missing_labels}", icon="⚠️")
        else:
            st.success(f"Cả {total_beats} beat đều đã có ảnh!", icon="✅")

    st.info(
        "Upload ảnh từ máy — hệ thống sẽ tự đặt tên theo chuẩn `beat_NN.jpg` "
        "để trang **Tạo Video** nhận diện được. Đặt số beat trong tên file "
        "(ví dụ `beat_03.jpg`, `anh-3.png`) để tự động gán đúng vị trí. "
        "Có thể trộn với ảnh tự động tạo: chỉ upload các beat còn thiếu.",
        icon="📤",
    )

    beat_files = st.file_uploader(
        f"Ảnh các Beat ({total_beats} beats)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="manual_beat_upload",
    )

    thumb_file = st.file_uploader(
        "Ảnh Thumbnail",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=False,
        key="manual_thumb_upload",
    )

    assignments = {}
    submitted = False
    allow_overwrite = False

    if beat_files:
        options = [f"beat_{i:02d}" for i in range(1, total_beats + 1)] + ["thumbnail", "__skip__"]
        option_labels = {f"beat_{i:02d}": f"Beat {i:02d}" for i in range(1, total_beats + 1)}
        option_labels["thumbnail"] = "🖼️ Thumbnail"
        option_labels["__skip__"] = "⏭️ Bỏ qua"

        with st.form("manual_assign_form", border=True):
            st.markdown("**Xác nhận vị trí gán cho từng ảnh:**")

            for idx, uploaded in enumerate(beat_files):
                detected = detect_beat_number(uploaded.name, total_beats)
                default_value = f"beat_{detected:02d}" if detected else "__skip__"
                selected = st.selectbox(
                    f"`{uploaded.name}` ({uploaded.size // 1024} KB)",
                    options=options,
                    index=options.index(default_value),
                    format_func=lambda opt: option_labels.get(opt, opt),
                    key=f"assign_{idx}_{uploaded.name}",
                )
                assignments[uploaded.name] = selected

            allow_overwrite = st.checkbox(
                "⚠️ Cho phép ghi đè lên ảnh đã tồn tại cùng tên",
                value=False,
            )
            submitted = st.form_submit_button(
                "💾 Lưu ảnh vào thư mục images/",
                type="primary",
                use_container_width=True,
            )

    if thumb_file is not None and not beat_files:
        st.caption(f"🖼️ Thumbnail: `{thumb_file.name}` sẽ được lưu thành `thumbnail{Path(thumb_file.name).suffix.lower()}`")

    if submitted:
        # Validate duplicate targets
        used_targets = {}
        conflict_files = []
        for fname, target in assignments.items():
            if target == "__skip__":
                continue
            if target in used_targets:
                conflict_files.append(f"`{target}` ← {used_targets[target]} & {fname}")
            else:
                used_targets[target] = fname

        has_thumb_target = thumb_file is not None

        if conflict_files:
            st.error("Trùng vị trí gán — hãy sửa lại: " + " | ".join(conflict_files), icon="🚨")
        elif not used_targets and not has_thumb_target:
            st.warning("Chưa có ảnh nào được gán vị trí.", icon="⚠️")
        else:
            images_dir.mkdir(parents=True, exist_ok=True)
            saved, skipped = [], []

            for uploaded in beat_files:
                target = assignments.get(uploaded.name, "__skip__")
                if target == "__skip__":
                    continue
                ext = Path(uploaded.name).suffix.lower()
                target_path = images_dir / f"{target}{ext}"
                if target_path.exists() and not allow_overwrite:
                    skipped.append(target_path.name)
                    continue
                target_path.write_bytes(uploaded.getvalue())
                saved.append(target_path.name)

            if has_thumb_target:
                ext = Path(thumb_file.name).suffix.lower()
                thumb_path = images_dir / f"thumbnail{ext}"
                if thumb_path.exists() and not allow_overwrite:
                    skipped.append(thumb_path.name)
                else:
                    thumb_path.write_bytes(thumb_file.getvalue())
                    saved.append(thumb_path.name)

            parts = []
            if saved:
                parts.append(f"Đã lưu {len(saved)} ảnh: **{', '.join(saved)}**")
            if skipped:
                parts.append(f"Bỏ qua (đã tồn tại, chưa bật ghi đè): {', '.join(skipped)}")
            st.session_state["manual_save_result"] = "\n\n".join(parts)

            # Reset uploaders after successful save
            for key in ("manual_beat_upload", "manual_thumb_upload"):
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    # Show result from previous save (survives rerun)
    save_result = st.session_state.pop("manual_save_result", None)
    if save_result:
        st.success(save_result, icon="🎉")

# ── Execute Generation ───────────────────────────────────────
if st.session_state.get("generate_images", False):
    st.warning("ĐANG TẠO ẢNH - Vui lòng không đóng trang!", icon="⚠️")

    progress_bar = st.progress(0, text="Đang khởi tạo...")
    log_container = st.empty()
    logs = []

    def add_log(msg):
        logs.append(msg)
        log_container.code("\n".join(logs[-15:]), language=None)

    try:
        from ai_mystery_story.infrastructure.image.pollinations_image_generator import (
            PollinationsImageGenerator,
        )

        generator = PollinationsImageGenerator(width=2560, height=1440)

        # Generate beat images
        total = len(prompts) + 1  # +1 for thumbnail
        all_prompts = prompts + [thumbnail_prompt]

        for i, p in enumerate(all_prompts):
            progress = int(80 * (i / total))
            progress_bar.progress(progress, text=f"🖼️ Đang tạo ảnh {i + 1}/{total}...")

            output_path = images_dir / p["filename"]
            add_log(f"[{i + 1}/{total}] Generating {p['filename']}...")

            try:
                generator.generate(
                    prompt=p["prompt"],
                    output_path=output_path,
                    seed=p["seed"],
                )
                add_log(f"  ✅ {p['filename']} saved")
            except Exception as e:
                add_log(f"  ❌ {p['filename']} failed: {e}")

        progress_bar.progress(100, text="✅ Hoàn tất!")
        add_log("=" * 40)
        add_log("🎉 Đã tạo xong tất cả ảnh!")

        st.session_state["generate_images"] = False
        st.balloons()
        st.success(f"Đã tạo {total} ảnh!", icon="🎉")
        st.rerun()

    except Exception as e:
        progress_bar.progress(100, text="❌ Thất bại!")
        add_log(f"❌ Error: {e}")
        st.error(f"Lỗi: {e}", icon="🚨")
        st.exception(e)
        st.session_state["generate_images"] = False

# ── Step 4: Go to Video ──────────────────────────────────────
st.write("")
st.subheader("🎬 Bước 4: Tạo Video", divider="gray")

if existing_images or (images_dir.exists() and any(images_dir.iterdir())):
    st.success("Ảnh đã sẵn sàng! Có thể tạo video.", icon="✅")
    st.page_link(
        "pages/8_🎬_Tạo Video.py",
        label="🎬 Tạo Video ngay",
        use_container_width=True,
    )
else:
    st.info("Hãy tạo ảnh trước khi tạo video.", icon="ℹ️")

# ── Navigation Footer ────────────────────────────────────────
st.write("")
st.divider()

nav_col1, nav_col2 = st.columns(2)
with nav_col1:
    st.page_link("pages/6_🔊_Audio.py", label="← Quay lại Audio", use_container_width=True)
with nav_col2:
    st.page_link("pages/8_🎬_Tạo Video.py", label="🎬 Tạo Video →", use_container_width=True)
