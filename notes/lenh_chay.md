# 📒 SỔ TAY LỆNH — autovid (cập nhật 01/10/2026)

Mọi lệnh chạy **từ thư mục gốc dự án** (`.../Tooler`). Venv: `.venv/`.
Render **luôn kèm throttle** để không ngập máy (16 core/31GB, desktop cần thở):

```bash
nice -n 19 ionice -c 3 .venv/bin/python autovid.py <stage> <script.json> --quiet --threads 6
```

---

## 1) Pipeline 7 stage (autovid.py)

| Stage | Lệnh | Làm gì |
|---|---|---|
| 1. validate | `autovid.py validate <script.json>` | soi kịch bản, báo lỗi cấu hình |
| 2. tts | `autovid.py tts <script.json> --quiet` | đọc giọng + đo khoảng lặng + timeline.json (cache từng câu) |
| 3. images | `autovid.py images <script.json> --quiet` | scale ảnh + chấm chất lượng |
| 4. assembly | `autovid.py assembly <script.json> --quiet --threads 6` | render từng cảnh (Ken Burns + khung + nhân vật + chữ) → preview_video.mp4 |
| 5. mix | `autovid.py mix <script.json> --quiet` | trộn giọng + nhạc + SFX → audio/mix.wav |
| 6. render | `autovid.py render <script.json> --quiet` | ghép hình + tiếng → **output/final_video.mp4** + quality_report |
| 7. captions | `autovid.py captions <script.json>` | phụ đề .rt gắn vào video |

- `--threads 6` chỉ có ở assembly (giới hạn thread x264, chữ ký cache tính cả threads).
- Cache trong `cache/` — xoá để render lại từ đầu; đổi script/khung/threads là tự render lại đúng phần thay đổi.
- `assembly --dry-run` xem kế hoạch frame không encode.

---

## 2) Khung kể chuyện (story frame) — trong script.json

```json
"story_frame": {
    "use": "ke_cuoi",
    "style": "border"
}
```

- `use` — narrator đứng ngoài khung bên phải, 6 template:
  `my_host`, `story_host`, `ke_su` (bình tĩnh), `ke_cuoi` (cười lớn), `ke_soke` (sốc/lắc), `ke_chay` (chạy/nói nhanh).
- `style` — 6 lựa chọn:
  | style | look |
  |---|---|
  | `border` | kem + viền đậm (mặc định, "look v2") |
  | `none` | panel trơn không viền |
  | `polaroid` | giấy ảnh, rãnh ảnh + lề dày dưới |
  | `tv_retro` | TV hoạt hình đỏ: màn hình RỘNG (1165×755), bảng điều khiển đáy, ăng-ten, **rung lắc nhẹ** + **đèn nhấp nháy xanh↔đỏ** (sheet `tv_lamp_blink.png`) |
  | `night` | navy đậm + viền bạc (buồn/ma) |
  | `custom` | **thiết kế của bạn** — cần `frame_png` + `art_inset` (xem mục 3) |

### Custom frame — chỉ cần đưa ảnh, tool lo phần còn lại

```bash
# Ảnh thiết kế 1920x1080 (màn hình để ĐẶC cũng được):
.venv/bin/python tools/check_story_frame_design.py <design.png> \
    --from-full --punch 160,165,970,950 --out projects/<id>/frame_ready.png
# → tool tự: cắt panel (77,65)-(1344,1015), đục lỗ cửa sổ trong suốt,
#   đo toạ độ, in sẵn block JSON để paste vào script.json.
```

Script.json sau đó:

```json
"story_frame": {
    "use": "ke_cuoi",
    "style": "custom",
    "frame_png": "projects/<id>/frame_ready.png",
    "art_inset": {"t": 0.10, "b": 0.12, "l": 0.07, "r": 0.30}
}
```

- Chỉnh toạ độ `--punch x0,y0,x1,y1` (đọc trên canvas 1920×1080) để to/nhỏ màn hình tuỳ ý.
- Đã có sẵn: `assets/frames/design_guide_full.png` (bản mẫu 3 màu: đỏ=cấm narrator, xanh lá=ô màn hình), `design_template_panel.png` (trong suốt 1267×950).
- Hướng dẫn đầy đủ: `notes/story_frame_design_guide.md`.

---

## 3) Vẽ/khoanh khung built-in

```bash
# Vẽ lại PNG khung (asset tự invalidate khi đổi file → render lại đúng phần):
.venv/bin/python tools/make_story_frame.py --size 1267x950 --style border \
    --out assets/frames/story_frame_border.png

# Đèn TV blink cần sheet 128x64 (xanh trái, đỏ phải):
#   assets/frames/tv_lamp_blink.png — mất thì TV vẫn render, đèn tắt.
# Rung/đèn cấu hình trong src/.../filters.py: STORY_FRAME_WOBBLES, STYLE_LAMPS.

# Gán/bỏ story_frame cho script có sẵn:
python3 tools/apply_story_frame.py projects/<id>/script.json --use ke_cuoi
python3 tools/apply_story_frame.py projects/<id>/script.json --remove

# Gán vị trí host cũ (my_host đứng cạnh ảnh):
python3 tools/apply_host_layout.py projects/<id>/script.json --corner left
python3 tools/apply_host_layout.py projects/<id>/script.json --remove
```

---

## 4) Nhân vật & miệng (mouth flap)

- Registry: `assets/characters/characters.json` — sửa `use`, idle, poses, mouth tại đây.
- Cắt patch miệng mở/đóng từ frame (chế độ `--box` chuẩn hơn auto):

```bash
.venv/bin/python tools/cut_mouth_patch.py <frame.png> \
    --box 229,146,342,231 --registry ten_template
```

- Cắt miệng loạt frame: `tools/cut_mouth_from_frames.py`.
- Xem trước idle/pose: `tools/preview_character_effects.py`.
- Tạo nhân vật demo: `tools/make_demo_character.py`.
- Tạo narrator my_host: `tools/make_narrator.py --out assets/narrator --height 900`.
- Tách nền: `tools/remove_background.py <anh.png> --suffix _cut`.

---

## 5) Ảnh & nội dung

```bash
# Tạo ảnh theo script (xem prompt trước để không tốn tiền):
python3 tools/generate_images.py projects/<id>/script.json --dry-run
python3 tools/generate_images.py projects/<id>/script.json

# Prompt ảnh ra file để review/dán tay:
python3 tools/make_image_briefs.py projects/<id>/script.json
```

---

## 6) Demo có sẵn (output/final_video.mp4)

| Project | Narrator | Khung |
|---|---|---|
| `demo_story_a` | ke_su | border (look v1 — chưa render lại) |
| `demo_story_b` | story_host | border (v1) |
| `demo_ke_cuoi` / `demo_ke_soke` / `demo_ke_chay` | 3 template mới | border v2 |
| `demo_style_polaroid` / `_tv` / `_night` | ke_cuoi | 3 style mới |
| `demo_style_custom` | ke_cuoi | custom (mock kiểm tra pipeline) |
| `topics-002` | ke_su | border v2 — **video thật 8:12 đã duyệt** |

Copy 1 project demo → sửa `story_frame` trong script.json → xoá `cache/` → chạy lại assembly/mix/render là có video mới.

---

## 7) Lỗi thường gặp

- `story_frame needs 'assets/frames/story_frame_X.png'` → vẽ bằng `make_story_frame.py --style X`.
- `style "custom" needs frame_png` → thêm `frame_png` vào script.json.
- Miệng không mấp máy → kiểm tra registry có `mouth.images` + cue đã truyền `mouth` (đã fix trong `resolve_character_cues`).
- VS Code bị treo khi render → thiếu throttle; luôn dùng dòng `nice -n 19 ionice -c 3 ... --threads 6`.
