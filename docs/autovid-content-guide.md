# Hướng dẫn cung cấp nội dung để dựng 1 video autovid

Tài liệu này nói rõ: **bạn cần gửi gì**, **lời kể phải viết thế nào**, và **cần tạo những ảnh gì**.
Sau đó tôi dựng workspace và chạy 7 stage (voice → ảnh → video → nhạc → xuất bản → phụ đề).

---

## 1. Gửi cho tôi những gì

| # | Thứ cần gửi | Bắt buộc | Định dạng | Ghi chú |
|---|---|---|---|---|
| 1 | **Lời kể** | ✅ | 1 file `.txt` UTF-8 | Mỗi đoạn cách nhau 1 dòng trống = 1 scene. Xem mục 2 |
| 2 | **Ảnh** | ✅ | 1 thư mục, ảnh 16:9 ≥ 1920×1080 | Đặt tên theo số: `01.jpg`, `02.jpg`… Xem mục 3 |
| 3 | **Nhạc nền** | ⬜ | 1 file `.mp3`/`.wav` | Nên dài ≥ độ dài video để không bị loop (nghe thấy mối nối) |
| 4 | **Tên tập** | ⬜ | text | Mặc định lấy tên file text |
| 5 | **Thể loại / giọng / tốc độ** | ⬜ | text | Mặc định: `trinhtham` → **Minh Quân Pro @ 0.92** |
| 6 | **SFX** | ⬜ | file + mốc (giây trong scene) + mức < 0.8 | Chỉ khi cần tiếng động (whoosh, sấm…), cách nhau ≥ 5s |
| 7 | **Tiêu đề chương** | ⬜ | trong text | Dòng `[[ Tên chương ]]` hoặc `Chương 3` → thành tiêu đề overlay + ngắt nhịp |
| 8 | **Phụ đề** | ⬜ | text | Mặc định có (soft sub, bật/tắt được trên player) |
| 9 | **Nhân vật hoạt hình** | ⬜ | 1–n file PNG **nền trong suốt** | Nhân vật chính dẫn chuyện (bay vào, trượt vào, nhún nhảy, biến mất). Xem mục 4 |

**Mẫu tin nhắn (copy-paste):**

```
Tên tập: ...
Thể loại: trinhtham | ma | tutien | ngonlu
Giọng: (mặc định Minh Quân Pro) — hoặc tên giọng khác trong bộ nghe thử
Tốc độ: 0.92 (mặc định) — hoặc 0.85 / 1.0
Nhạc: (tên file hoặc "không")
SFX: (có/không, file + vị trí)
Phụ đề: có/không
Nhân vật: (file PNG trong suốt, hoặc "không")
File lời kể: <đường dẫn>.txt
Thư mục ảnh: <đường dẫn>
```

Cách gửi: copy file vào repo rồi nói đường dẫn, ví dụ `projects/<topic-id>/narration.txt` và
`projects/<topic-id>/images/`.

---

## 2. Lời kể — quy tắc viết

1. **Đoạn = scene.** Mỗi đoạn (cách nhau 1 dòng trống) là 1 ảnh + 1 nhịp. Đoạn dài quá thì hệ thống
   tự cắt theo câu; đoạn ngắn dưới 6 giây thì tự gộp vào đoạn sau.
2. **Độ dài mỗi đoạn: 250–350 ký tự** (≈ 14–19 giây) — đủ để người xem kịp nhìn ảnh rồi đổi.
3. **Câu nên < 200 ký tự.** Câu > 250 ký tự bị chia thành 2 phụ đề; câu < 12 ký tự ("Vâng.")
   tạo phụ đề nhấp nháy.
4. **Đừng hard-wrap trong đoạn** — xuống dòng trong đoạn sẽ bị gộp lại thành 1 dòng.
5. **Viết số/ký hiệu bằng chữ khi cần đọc đúng**: "phần trăm" thay cho `%`, "mười lăm tháng mười"
   thay cho `15/10`. Số như `113` nên viết "một-một-ba" hoặc "một trăm mười ba" tuỳ cách đọc bạn muốn.
6. **Tiêu đề chương**: dòng riêng `[[ Phần 2: Đêm định mệnh ]]`.

**Bảng độ dài (đo thực tế với Minh Quân Pro @ 0.92 ≈ 18.3 ký tự/giây):**

| Thời lượng video | Ký tự lời kể | Số đoạn (~300 ký tự) | Số ảnh cần |
|---|---|---|---|
| 3 phút (demo) | ~3.300 | 11–13 | 12–15 |
| 5 phút | ~5.500 | 18–20 | 18–20 |
| 8 phút | ~8.800 | 28–30 | 28–32 |
| 13 phút | ~14.300 | 45–48 | 40–48 |
| 20 phút | ~22.000 | 70–75 | 60–70 |

> Quy tắc ảnh: **1 ảnh cho 15–20 giây** (≈ 1 ảnh / 300–360 ký tự).

---

## 3. Ảnh — tạo những gì

### 3.1 Yêu cầu kỹ thuật (bắt buộc)
- **16:9, tối thiểu 1920×1080** — ảnh nhỏ hơn khung sẽ bị upscale và mờ (đúng lỗi của bộ ảnh
  `mystery-001` 1024×572: 15/15 ảnh bị cảnh báo `image_upscaled`).
- **Chừa lề an toàn ~10%** quanh mép: Ken Burns zoom/pan 1.08–1.14 nên luôn cắt bớt mép.
- **Không để chữ quan trọng trong ảnh** — chữ trên video là overlay riêng, dễ đè lên nhau.
- **Một phong cách, một tông màu** cho cả video; đủ tương phản để chữ trắng/đen đọc được.
- Nếu ảnh có watermark AI: chèn logo kênh bằng `apply_logo.py` (xem README) **sau khi** đã có ảnh.

### 3.2 Quy trình xác định "cần ảnh gì"
```bash
# Sau khi tôi dựng script.json từ lời kể của bạn:
python tools/make_image_briefs.py projects/<topic-id>/script.json
```
File `projects/<topic-id>/image_briefs.md` sinh ra gồm:
- **Cần tạo / cần bổ sung**: file thiếu, file nhỏ hơn khung, ảnh bị dùng lại quá nhiều scene.
- **Danh sách ảnh**: mỗi file → trạng thái, kích thước, dùng cho scene nào, **cột Brief để bạn điền**.
- **Nội dung từng scene**: lời kể của từng scene để bạn vẽ cho khớp.

### 3.3 Prompt mẫu để tạo ảnh (Gemini / Dola / Midjourney…)
```
<BỐI CẢNH: 1 câu mô tả cảnh>, <nhân vật nếu có>, <thời điểm: đêm/mưa/sương>,
<phong cách: cinematic still, Vietnamese setting, muted cold teal-amber palette, film grain>,
16:9, wide shot, negative space ở 1/3 khung cho chữ overlay, không chữ trong ảnh,
không watermark, không logo, không viền.
```
Bộ ảnh nên dùng **cùng một khối phong cách** (đổi mỗi phần bối cảnh) để video trông liền mạch.

### 3.4 Shot list làm mẫu — demo 3 phút "Khu chung cư Hoa Mai" (15 scene)
| Scene | Ảnh | Brief để vẽ/generate |
|---|---|---|
| 1 | `scene_001.png` | Toàn cảnh khu chung cư cũ 5 tầng, sơn vàng ố bong tróc, sương đêm Hà Nội, phố vắng, tông lạnh xanh xám, không người |
| 2 | `scene_002.png` | Cận mặt tiền: tường rêu ẩm, sơn bong từng mảng, bó dây điện/cáp chằng chịt như mạng nhện |
| 3 | `scene_003.png` | Hành lang hẹp tối, cửa sổ nhỏ nhìn ra phố đông xe ban đêm, ánh đèn vàng nhạt, cảm giác ồn ào dội vào |
| 4 | `scene_004.png` | Phòng trực ban cảnh sát 113 ban đêm: bàn máy, đèn bàn, màn hình, một sĩ quan đang nghe điện thoại |
| 5 | `scene_005.png` | Cận cảnh bàn tay nhấc ống nghe, gương mặt sĩ quan căng thẳng, ống nghe đen nổi bật trên nền tối |
| 6 | `scene_006.png` | Ống nghe buông lơi trên bàn, màn hình hiện cuộc gọi kết thúc, tông tối tương phản mạnh (tiếng "Á!" + đổ vỡ) |
| 7 | `scene_007.png` | Phòng làm việc Đội CSHS quận Hai Bà Trưng ban đêm, hồ sơ ngổn ngang, sĩ quan cấp đại uý đứng chỉ huy |
| 8 | `scene_008.png` | Chân dung nam 35–40 tuổi, kính cận gọng mảnh, ánh mắt sắc, biểu cảm trầm, tông tối |
| 9 | `scene_009.png` | Bàn họp có sơ đồ hiện trường, vài sĩ quan khẩn trương, đèn phòng họp ban đêm |
| 10 | `scene_010.png` | Xe cảnh sát lao qua phố khuya, đèn ưu tiên kéo vệt sáng, hơi nhòe chuyển động |
| 11 | `scene_011.png` | Cầu thang bộ hẹp và dốc, bóng người chạy lên, đèn pin cầm tay, tường cũ |
| 12 | `scene_012.png` | Cận cảnh ổ khoá thông minh phát sáng trên cánh cửa gỗ xập xệ — tương phản hiện đại/cũ nát |
| 13 | `scene_013.png` | Bên trong căn hộ nhỏ bừa bộn, đồ đạc ngổn ngang, ánh sáng từ ngoài hắt vào |
| 14 | `scene_014.png` | Cận cảnh hai ngón tay đặt lên cổ nạn nhân, tông tối lạnh, căng thẳng |
| 15 | `scene_015.png` | Nạn nhân nằm, cổ siết bởi sợi cáp mạng xanh lục — chi tiết là điểm nhấn của ảnh |

> 15 ảnh cho 3 phút ≈ 12 giây/ảnh — hơi dày so với chuẩn 15–20s/ảnh, nên với video demo này có thể
> gộp còn 10–12 ảnh bằng cách cho 2 scene liền nhau dùng chung 1 ảnh (sửa `image_file` trong `script.json`).

---

## 4. Nhân vật hoạt hình — nhân vật chính của bạn

Nếu muốn có **nhân vật dẫn chuyện** đè lên ảnh nền (bay vào, trượt vào, nhún nhảy khi nói, biến mất),
bạn chỉ cần đưa **ảnh PNG đã cắt nền trong suốt** — nền ảnh/AI vẫn giữ như hiện tại.

### 4.1 Ảnh nhân vật cần đạt gì
- **PNG có alpha** (nền trong suốt), không phải JPG, **không có viền/hộp trắng**.
- **Chân (hoặc đáy hình) chạm mép dưới** của ảnh — pipeline neo theo "chân" để đặt nhân vật đứng trên sàn.
- **Cao ≥ 1000px** ở chiều cao nhân vật (video 1080p dùng `height: 0.42` ≈ 450px, đủ nét).
- Nhân vật nhìn thẳng / 3/4, **không bị cắt cụt tay chân**; thừa lề trên một chút để tóc/đầu không dính sát mép.
- Nên có **2–3 biến thể**: đứng thường, đang chỉ tay, đang nhún vai (như `assets/characters/host_*.png`).

### 4.2 Xem trước toàn bộ hiệu ứng trước khi chốt
```bash
python3 tools/preview_character_effects.py                                   # cả 30 hiệu ứng -> tmp/character_effects/effects.mp4
python3 tools/preview_character_effects.py --character <ảnh PNG của bạn>     # thử trên chính nhân vật của bạn
python3 tools/preview_character_effects.py --effect boing,drop_bounce,spin_in
python3 tools/make_demo_character.py                                         # nếu chưa có art, tạo nhân vật mẫu
python3 tools/make_sfx_pack.py                                               # kho SFX hài: whoosh, boing, ta_da, pop…
```

### 4.3 Hiệu ứng đang có (đúng những gì bạn hỏi)

| Nhóm | Có sẵn |
|---|---|
| **Vào** | bay vào (`fly_in`), trượt vào (`slide_in`), rơi-nảy (`drop_bounce`), nở ra có overshoot (`pop`), xoay khi vào (`spin_in`), phóng to (`zoom_in`), mờ dần hiện (`fade_in`) — mỗi kiểu chọn được **từ 8 hướng** |
| **Ra / biến mất** | tan biến mờ (`fade_out`), teo nhỏ rồi mất (`shrink_out`), bay ra (`fly_out`), trượt ra (`slide_out`), rơi khỏi khung (`drop_out`), xoay ra (`spin_out`), thu nhỏ nhanh (`zoom_out`) |
| **Đang nói (idle)** | nhún nhảy (`bob`), lắc lư (`sway`), kết hợp (`bob_sway`), giật run rẩy (`shake`), nhún nhanh như đang nói (`talk`) |
| **Neo theo câu thoại** | hiện ở câu N (`at_sentence`) và ở lại N câu (`for_sentences`) — khớp đúng lúc giọng đọc tới đó |
| **Miếng hài đóng gói** | `preset`: `pop`, `boing`, `whoosh`, `ta_da`, `sneak`, `ninja` (mỗi preset = enter + idle + exit + SFX) |
| **Đối thoại / PIP** | nhiều nhân vật cùng scene; nhân vật phụ đặt góc khung (`height: 0.28`, `y: 0.35`) làm cutaway |
| **Cú giật cả khung** | `impact`: punch-in + rung khung (`shake_px`) + **flash trắng/đen** đúng vào câu nhấn |
| **Chuyển cảnh** | 25 kiểu: `cut`, `fade` (+`fade_fast/slow`), `dissolve`, trượt 4 hướng, **wipe 4 hướng**, `zoom`, `whip_pan` (vuốt nhanh), `pixelize`, `blur`, `circle_open/close`, `squeeze_h/v`, `flash_white/black`, chéo `diag_tl/br` |
| **SFX hài** | `whoosh`, `boing`, `pop`, `ta_da`, `thud`, `ding`, `sparkle`, `sneak`, `swoosh_long` (đặt đúng lúc nhân vật tiếp đất/chạm khung) |

### 4.4 Khai báo trong `script.json`
```json
"characters": [{
  "image_file": "assets/characters/host_point.png",
  "preset": "boing",
  "at_sentence": 2, "for_sentences": 3,
  "x": 0.22, "y": 0.92, "height": 0.42,
  "enter": {"type": "fly_in", "from": "bottom_left", "duration_ms": 600},
  "idle":  {"type": "talk", "amplitude_px": 8, "period_s": 0.45},
  "exit":  {"type": "shrink_out", "duration_ms": 400},
  "sfx":   {"file": "data/sfx/whoosh.mp3", "volume": 0.5}
}]
```
Chi tiết từng field (kèm giới hạn hợp lệ) nằm ở README mục **"Nhân vật hoạt hình"**.
Nếu chưa muốn nhân vật, thêm `--no-characters` khi chạy `assembly` để bỏ toàn bộ lớp này.

---

## 5. Nhạc và SFX

- **Nhạc**: 1 file, nên dài ≥ video. Nhạc ngắn hơn sẽ bị loop và **có thể nghe thấy mối nối**
  (cảnh báo `background_music_looped`). Mức mặc định 0.12 (≈ 1/8 giọng đọc); > 0.3 là bị cảnh báo.
- **SFX**: `{"file": "assets/sfx/whoosh.mp3", "time_offset_ms": 400, "volume": 0.6}` trong scene.
  Mốc tính **từ đầu scene**; hai SFX cách nhau < 5s sẽ bị cảnh báo là quá dày.
- Nhạc/SFX được trộn ở stage `mix`; mix tự đo loudness và chỉ chỉnh khi lệch quá 0.5 LUFS,
  tự limit nếu true peak vượt −1.5 dBFS (có ghi lại trong `mix_report.json`).

---

## 6. Quy trình từ nội dung → video

```bash
# 0. Môi trường (1 lần)
python3 -m venv .venv && .venv/bin/pip install vieneu pillow

# 1. Dựng workspace từ text + ảnh (tự sinh script.json + chạy validator)
python tools/make_project_from_text.py \
    --text projects/<id>/narration.txt --images projects/<id>/images \
    --out projects/<id>-autovid --title "Tên tập" --genre trinhtham \
    --music data/music/<nhac>.mp3 --scene-seconds 20

# 2. Xem cần ảnh gì (tuỳ chọn, sau khi có script.json)
python tools/make_image_briefs.py projects/<id>-autovid/script.json

# 3. Chạy 7 stage
S=projects/<id>-autovid/script.json
.venv/bin/python autovid.py validate "$S"     # kiểm schema + ảnh + font + đĩa + thời lượng dự kiến
.venv/bin/python autovid.py tts      "$S"     # giọng đọc từng câu + đo thật + timeline
.venv/bin/python autovid.py images   "$S"     # scale frame + đo chất lượng ảnh
.venv/bin/python autovid.py assembly "$S"     # Ken Burns + overlay + nối clip (video im lặng)
.venv/bin/python autovid.py mix      "$S"     # giọng + nhạc + SFX -> audio/mix.wav
.venv/bin/python autovid.py render   "$S"     # mux thành output/final_video.mp4
.venv/bin/python autovid.py captions "$S"     # phụ đề (mặc định soft sub; --burn để đốt vào hình)
```

**Thời gian chạy thực tế** (máy 32 luồng CPU, video 1080p30):

| Video | TTS | Assembly | Mix | Render + Captions |
|---|---|---|---|---|
| 3 phút | ~2,5 phút | ~40s | ~15s | ~15s |
| 13 phút | ~11 phút | ~3 phút | ~1 phút | ~1 phút |

---

## 7. Checklist nghiệm thu trước khi đăng

- [ ] `output/quality_report.json` → `"status": "pass"`, `errors: 0`.
- [ ] `totals.issues` không còn `image_upscaled` (ảnh đủ nét) — nếu còn, thay ảnh ≥ 1920×1080.
- [ ] `output/mix_report.json` → loudness **−14 ± 1 LUFS**, true peak **≤ −1 dBFS**.
- [ ] `output/captions_report.json` → `cue_too_short` = 0 (hoặc chấp nhận được với bạn).
- [ ] Nghe 30s đầu, 30s giữa, 30s cuối (fade out 3s) — không vỡ tiếng, nhạc không lấn giọng.
- [ ] Xem lại đúng chỗ chuyển cảnh có transition (fade) — không bị "nhảy" hình.
- [ ] Nhân vật (nếu có): hiện/ẩn **đúng lúc câu thoại**, không đè lên chữ overlay, SFX khớp lúc tiếp đất.
- [ ] Thời lượng nằm trong 8–20 phút (nếu không thấy cảnh báo `runtime_short`/`runtime_long`).
