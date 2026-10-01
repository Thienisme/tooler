# Bộ prompt tạo nhân vật dẫn truyện mới (cho khung kể chuyện)

Tạo ảnh xong thì **bảo Buffy xem và ghép** — cắt nền, patch miệng, registry và
render đều đã có tool sẵn.

## 1. Quy cách kỹ thuật (bắt buộc để ghép được)

1. **Cùng một nhân vật ở mọi frame**: cùng trang phục, màu sắc, kiểu tóc, tỷ lệ
   cơ thể. Cách chắc nhất: generate tất cả trong cùng một session, dùng ảnh đầu
   làm reference ("same character as this image, now ...").
2. **Nền trắng đặc, sạch** (temporarily white is fine — Buffy sẽ cắt nền bằng
   rembg), không đổ bóng xuống sàn, không chữ, không viền.
3. **Nhân vật nhìn/hướng về phía BÊN TRÁI khung ảnh** (vì narrator đứng bên
   phải màn hình, khung truyện bên trái). Nếu quên, mọi frame đều có thể lật
   bằng `flip: true` — nhưng tất cả phải cùng một chiều.
4. **Đầu to rõ, mặt hướng 3/4 trái**, chừa vùng miệng lớn và sạch (không bị
   tóc/râu/tai che) — để cắt patch miệng đóng/mở.
5. **Tư thế đứng thẳng, 2 chân xuống đất**, full body từ đầu tới bàn chân.
6. Tỷ lệ dọc (cao > rộng, trừ frame chỉ tay có thể rộng hơn một chút).
7. Xuất PNG nguyên bản độ phân giải cao nhất tool cho phép (≥1024px cạnh).

## 2. Danh sách frame cần tạo (tối thiểu 3, nên có 5)

| # | Tên file | Vai trò |
|---|---|---|
| 1 | `host_point.png` | **Ảnh gốc (resting)** — đang chỉ tay về trái, miệng đang nói |
| 2 | `host_relax.png` | Pose khi nghỉ — thả tay đứng tự nhiên |
| 3 | `host_point2.png` | (tuỳ chọn) chỉ tay với biểu cảm khác (mắt mở to / cười) |
| 4 | `host_excited.png` | (tuỳ chọn) pose hào hứng — 2 tay giơ lên |
| 5 | `host_thinking.png` | (tuỳ chọn) pose suy tư — tay chống cằm |

Trong đó frame 1 và 2 là cặp tối thiểu tạo hiệu ứng "vung tay chỉ" khi nói/nghỉ.

## 3. Prompt mẫu (điền theo template của bạn)

Điền vào `[TÊN NHÂN VẬT VÀ MÔ TẢ NGOẠI HÌNH]`, `[TRANG PHỤC]`, `[ĐỒ VẬT]`
theo ý bạn, còn `[HÀNH ĐỘNG]` thay bằng từng dòng dưới đây:

- **Frame 1 (chỉ tay):**
  `pointing emphatically to the LEFT with one outstretched arm and index
  finger, mouth wide open mid-sentence, leaning slightly forward`
- **Frame 2 (thả tay):**
  `standing relaxed with both arms hanging down, gentle smile, mouth closed,
  weight on one leg`
- **Frame 3 (chỉ tay kiểu 2):**
  `pointing to the LEFT again with a surprised, wide-eyed expression, other
  hand raised in astonishment`
- **Frame 4 (hào hứng):**
  `both arms thrown up in the air in excitement, laughing with mouth wide open`
- **Frame 5 (suy tư):**
  `holding their chin thoughtfully with one hand, eyes looking up-left`

Ví dụ prompt hoàn chỉnh cho frame 1:

```
A 2D cartoon illustration in a minimalist, humorous, and highly expressive
animation style, featuring a funny and goofy version of "Ông Táo kể chuyện" —
a chubby middle-aged storyteller with a round face, thick eyebrows and a small
goatee — in an incredibly comical, simplified 2D aesthetic.

The character has a rounded, slightly chunky, and slouching body with
noodle-like arms and tiny, simplified legs. The pose is highly expressive,
goofy, and awkward, showing them pointing emphatically to the LEFT with one
outstretched arm and index finger, mouth wide open mid-sentence, leaning
slightly forward.

Their face is hilarious and expressive: giant mismatched eyes with tiny
pupils, funny eyebrows, a big bulbous nose, and a goofy, gaping mouth. Their
messy hair and simple, exaggerated features add to the comedic look.

They are wearing simplified, flat-colored blue áo dài with visible stitching
and messy details, holding nothing.

The artwork features thick, rough black outlines and vibrant, flat coloring
with no complex shading. Clean, solid white background to emphasize the
character design. Full body visible from head to feet. Humorous, playful, and
hand-drawn comic animation style.
```

## 4. Giao lại cho Buffy — sẽ được làm tự động

Khi bạn bảo "xong rồi", Buffy sẽ chạy chuỗi sau (đã có sẵn, không cần bạn làm gì):

```bash
# 1. Cắt nền -> host_point_cut.png ... (RGBA)
python3 tools/remove_background.py <thu-muc-anh> --suffix _cut

# 2. Cắt patch miệng đóng/mở từ frame chỉ tay (chỉ khi muốn miệng mấp máy mượt)
python3 tools/cut_mouth_from_frames.py --help   # Buffy sẽ chạy và kiểm tra hộ

# 3. Đăng ký vào assets/characters/characters.json (entry mới, ví dụ "story_host")
#    + apply vào script:
python3 tools/apply_story_frame.py projects/<id>/script.json --use <ten_moi>

# 4. Render lại demo để bạn duyệt
python3 autovid.py assembly projects/demo_story_a/script.json ...
```

Buffy sẽ kiểm tra từng frame (đúng nhân vật, đúng chiều nhìn, alpha sạch,
miệng cắt được) trước khi ghép — frame nào hỏng sẽ báo lại để bạn generate lại
chỉ frame đó thôi.
