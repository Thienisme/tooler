# Hướng dẫn thiết kế khung kể chuyện (story frame) — dành cho designer

Bạn vẽ art, pipeline lo phần kỹ thuật. Chỉ cần tuân theo **3 vùng màu** dưới đây.
Toàn bộ flow: vẽ → kiểm tra bằng tool → ghi 1 block JSON vào script.json → render.

## Cách làm nhanh nhất (khuyến nghị)

1. Mở file **`assets/frames/design_guide_full.png`** (1920×1080) trong app vẽ của bạn (Photoshop / Procreate / Krita / Canva...).
2. **Vẽ đè lên nó** bằng layer mới:
   - Vẽ **họa tiết khung thoải mái** trong vùng viền xanh (panel) — TV, polaroid, sách, truyện tranh, ván gỗ, bảng đen... tuỳ ý.
   - **Vùng ĐỎ** (bên phải, x ≥ 1344px): TUYỆT ĐỐI KHÔNG VẼ — đó là chỗ đứng của narrator (người kể chuyện ngoài khung).
   - **Vùng XANH LÁ** (bên trong panel): đây là "cửa sổ tranh" — có thể để ĐẶC (màu tối bất kỳ), tool sẽ tự đục lỗ đúng toạ độ khi bạn chạy lệnh `--punch`.
3. Xuất **PNG**, giữ nguyên kích thước **1920×1080**, lưu ví dụ: `projects/my_design/my_frame.png`.

Ngoài ra có `assets/frames/design_template_panel.png` (1267×950, toàn trong suốt) nếu bạn muốn thiết kế đúng khổ panel.

## Quy tắc bắt buộc (tool sẽ kiểm tra)

- **Vùng narrator (đỏ)**: không được có pixel nào của khung.
- **Cửa sổ tranh**: 1 khối trong suốt **liền mạch** — không chấm rời, không lỗ thủng nhỏ.
- **Lề (mat)**: cửa sổ phải cách mép panel tối thiểu ~4% mỗi bên để tranh "được đóng khung", không dán sát.
- Định dạng: **RGBA PNG**, giữ kích thước 1920×1080 (hoặc 1267×1004).

## Kiểm tra thiết kế (bắt buộc trước khi dùng)

```bash
.venv/bin/python tools/check_story_frame_design.py projects/my_design/my_frame.png
```

Tool in ra vị trí cửa sổ đo được + lỗi (nếu có). Khi **[ok]**, nó in sẵn block JSON để paste.

## Dùng thiết kế vào video

Thêm vào `script.json` của project:

```json
"story_frame": {
    "use": "ke_cuoi",
    "style": "custom",
    "frame_png": "projects/my_design/my_frame.png",
    "art_inset": {"t": 0.10, "b": 0.12, "l": 0.07, "r": 0.30}
}
```

- `use`: narrator template giữ nguyên (ke_cuoi / ke_soke / ke_chay / ke_su / story_host / my_host).
- `art_inset`: **chép đúng 4 con số** mà checker in ra — pipeline crop tranh video vào đúng lỗ cửa sổ của thiết kế bạn.
- Tỉ lệ / vị trí khung **không đổi** (panel mặc định) → các bước đưa nội dung video vào sau này hoạt động như cũ.

## Render thử

```bash
nice -n 19 ionice -c 3 .venv/bin/python autovid.py assembly projects/<id>/script.json --quiet --threads 6
nice -n 19 ionice -c 3 .venv/bin/python autovid.py mix projects/<id>/script.json --quiet
nice -n 19 ionice -c 3 .venv/bin/python autovid.py render projects/<id>/script.json --quiet
```

## Gợi ý ý tưởng thiết kế (tuỳ chọn)

TV hoạt hình (ăng-ten, núm xoay) · máy tính cổ (CRT + bàn phím) · khung ảnh polaroid (băng dính, chữ viết tay) · trang truyện tranh (bong bóng thoại trống) · bảng gỗ quán rượu · màn hình điện thoại · khung tranh vàng_baroque · bảng đen lớp học.
