# `assets/narrators/` — ảnh nhân vật kể chuyện (người dẫn)

Bỏ file ảnh (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`) vào **thư mục này** là xong.

- Studio sẽ liệt kê nó trong panel **Thu vien anh → "Nhan vat ke chuyen (nguoi dan)"**,
  ngay sau khi bấm **Lam moi thu vien** (không cần sửa JSON, không cần khởi động lại).
- Chọn ảnh đó (bấm đúp, hoặc kéo thả lên khung hình) sẽ ghi vào `script.json`:

  ```json
  "story_frame": {
    "enabled": true,
    "show_narrator": true,
    "image_file": "assets/narrators/<ten_file>.png"
  }
  ```

- **Tên file chính là tên hiển thị** (gạch dưới thành khoảng trắng: `co_giao.png` → `co giao`).
- Ảnh nên là PNG **nền trong suốt**, nhân vật đứng ở **mép dưới khung** (chân ở đáy ảnh),
  tỉ lệ dọc — Studio đặt nhân vật vào dải bên phải khung kể chuyện.

## Hai thư mục, hai phạm vi

| Vị trí | Dùng cho |
|---|---|
| `<repo>/assets/narrators/` (thư mục này) | **mọi project** — ảnh dùng chung |
| `projects/<ten>/assets/narrators/` | **chỉ project đó** — project được đọc trước, nên ảnh cùng tên ở đây sẽ thắng |

Cả hai đều được ghi vào script theo dạng `assets/narrators/...`, vì pipeline giải đường dẫn
theo **workspace (thư mục chứa script.json) trước, rồi tới gốc repo** — nên một đường dẫn dùng
được ở cả hai chỗ.

## Khi nào cần `assets/characters/characters.json` (registry)

Ảnh trong thư mục này là nhân vật kể chuyện **đứng yên**. Nếu cần nhân vật **nói chuyện**
(miệng đóng/mở theo lời đọc) hoặc **đổi tư thế** luân phiên, hãy mô tả trong registry và chọn
theo **key** — registry vẫn được liệt kê trong cùng danh sách:

```json
// assets/characters/characters.json
{
  "co_giao": {
    "image_file": "assets/narrators/co_giao.png",
    "mouth": { "closed": "assets/narrators/co_giao_mouth_closed.png",
               "open":   "assets/narrators/co_giao_mouth_open.png" },
    "poses": [ { "image_file": "assets/narrators/co_giao_2.png" } ]
  }
}
```

Entry trùng với ảnh trong thư mục (cùng tên file) sẽ chỉ hiện **một lần** trong danh sách —
bản của registry, vì nó mang theo cả miệng và poses.
