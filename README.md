# 🕵️ AI Mystery Story

Hệ thống tự động tạo video truyện trinh thám bằng AI.

## 🛠️ Cài đặt

### Yêu cầu
- Python >= 3.12
- ffmpeg (đã config static binary trong `bin/`)

### Bước 1: Tạo virtual environment & cài dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux/Mac
pip install -e .
```

### Bước 2: Cấu hình API Key

Tạo file `.env` ở gốc dự án:

```bash
GEMINI_API_KEY=your-api-key-here
```

> Lấy API key tại: https://aistudio.google.com/apikey

### (Tùy chọn) Bước 3: Cấu hình Fish Speech (giọng đọc cảm xúc)

```bash
FISH_SPEECH_API_KEY=your-fish-api-key-here
```

> Lấy API key tại: https://fish.audio/app/api-keys

---

## 🚀 Chạy dự án

### Cách 1: Dùng script có sẵn (khuyến nghị)

```bash
# Khởi động Streamlit app
./start.sh

# Truy cập: http://localhost:8501

# Tắt app
pkill -f "streamlit run"
```

### Cách 2: Chạy thủ công

```bash
source .venv/bin/activate
cd app
streamlit run app.py --server.port 8501 --server.headless true --server.address 0.0.0.0
```

### Cách 3: Chạy CLI (từng bước)

```bash
source .venv/bin/activate

# Bước 1: Tạo story
python generate_story.py mystery-000

# Bước 2: Tạo narration
python generate_narration.py gemini mystery-000

# Bước 3: Export narration
python export_narration.py mystery-000

# Bước 4: Tạo audio
python generate_audio.py edge mystery-000

# Hoặc chạy full pipeline
python generate_pipeline.py mystery-000
```

---

## 📁 Cấu trúc dự án

```
├── app/                          # Streamlit Frontend
│   ├── app.py                    # Trang chủ
│   └── pages/
│       ├── 1_📋_Topics.py        # Danh sách đề tài
│       ├── 2_✍️_Tạo Story.py     # Tạo story
│       ├── 3_📖_Xem Story.py     # Xem story
│       ├── 4_🎙️_Narration.py    # Tạo lời thuyết minh
│       ├── 5_🔊_Audio.py         # Tạo & nghe audio
│       ├── 6_🔄_Pipeline.py      # Pipeline tự động (có duyệt)
│       ├── 7_📄_Export Narration.py
│       ├── 8_🎬_Tạo Video.py
│       ├── 9_🧪_Test Voice.py    # Thử giọng
│       ├── 10_⚙️_Settings.py     # Cài đặt
│       └── 11_📝_Quản lý Topics.py
│
├── src/ai_mystery_story/         # Core Backend
│   ├── domain/                   # Story, Narration models
│   ├── application/              # Services
│   └── infrastructure/           # TTS, AI providers
│
├── data/
│   └── mystery_topics.json       # 70+ đề tài có sẵn
│
├── generate_*.py                 # CLI scripts
├── bin/                          # ffmpeg static binary
├── .env                          # API keys (gitignored)
├── start.sh                      # Script khởi động app
└── pyproject.toml                # Dependencies
```

---

## 🔧 Cấu hình Voice

### Edge TTS (mặc định - miễn phí)

```
EDGE_TTS_VOICE=vi-VN-NamMinhNeural    # Giọng nam
EDGE_TTS_VOICE=vi-VN-HoaiMyNeural     # Giọng nữ
EDGE_TTS_RATE=-7%                      # Tốc độ
EDGE_TTS_PITCH=-2Hz                    # Cao độ
```

### Fish Speech (giọng cảm xúc - miễn phí)

```
FISH_SPEECH_API_KEY=your-key
FISH_SPEECH_MODEL=s2.1-pro-free
```

Emotion tags: `[whisper]`, `[pause]`, `[surprised]`, `[angry]`, `[sad]`, `[shocked]`

---

## 📌 Lệnh thường dùng

| Lệnh | Mô tả |
|---|---|
| `./start.sh` | Khởi động app |
| `pkill -f "streamlit run"` | Tắt app |
| `source .venv/bin/activate` | Bật virtual environment |
| `python generate_pipeline.py mystery-000` | Chạy full pipeline |
| `pip install -e .` | Cài lại dependencies |

---

## 🎬 autovid — pipeline video thuyết minh (7/7 stage)

Pipeline riêng cho **video thuyết minh dài 8–20 phút** (ảnh tĩnh + Ken Burns +
overlay chữ + voiceover + nhạc/SFX + phụ đề), khác với luồng truyện audio
(`generate_*.py`, `render_vieneu_long.py`). Đầu vào là một `script.json`.

```bash
python autovid.py --help
```

### 7 stage

| # | Stage | Việc làm | Output |
|---|---|---|---|
| 1 | `validate` | kiểm schema, ảnh, font, SFX, ffmpeg, dung lượng đĩa, thời lượng dự kiến | `output/validation_report.json` |
| 2 | `tts` | synth từng câu, đo thời lượng thật + khoảng lặng, tính pause, dựng timeline | `audio/voiceover_full.wav`, `output/timeline.json`, `output/tts_report.json`, `output/pacing_report.json` |
| 3 | `images` | scale frame 1 lần (đủ headroom cho Ken Burns) + đo độ nét/khối/màu | `output/prepared_images/`, `output/image_report.json` |
| 4 | `assembly` | render từng scene (Ken Burns + overlay chữ) rồi nối lại | `output/preview_video.mp4` (im lặng), `output/assembly_report.json` |
| 5 | `mix` | loop + fade nhạc nền theo độ dài video, đặt từng SFX đúng mốc thời gian, cộng 3 stem | `audio/stem_music.wav`, `audio/stem_sfx.wav`, `audio/mix.wav`, `output/mix_report.json` |
| 6 | `render` | mux hình + tiếng thành file giao (video **copy**, chỉ encode audio AAC) | `output/final_video.mp4`, `output/render_report.json`, `output/quality_report.json` |
| 7 | `captions` | dựng phụ đề từ chính số đo của stage 2 (hoặc `--asr` bằng faster-whisper) rồi gắn vào video | `output/captions.srt`, `output/final_video_subtitled.mp4`, `output/captions_report.json` |

### Chạy full pipeline

```bash
S=projects/<topic-id>/script.json

python autovid.py validate   "$S"            # thêm --skip-engine-check khi máy chưa có vieneu
python autovid.py tts        "$S"            # --backend fake để test khi chưa có model
python autovid.py images     "$S"
python autovid.py assembly   "$S"
python autovid.py mix        "$S"            # --skip-music để nghe thử voice + SFX
python autovid.py render     "$S"            # --voice-only để bỏ nhạc/SFX, --dry-run để xem trước
python autovid.py captions   "$S"            # --burn để đốt phụ đề vào hình
```

Stage sau đọc lại sản phẩm của stage trước, và mọi stage đều ghi tiến trình vào
`output/state.json` nên chạy lại sẽ dùng lại phần đã xong (cache clip scene,
cache câu thoại…).

### Định dạng nội dung (content → script.json)

> 📘 Muốn biết **cần gửi gì và phải tạo những ảnh gì** (kèm shot list mẫu 15 ảnh cho video 3 phút,
> prompt tạo ảnh, checklist nghiệm thu): xem [`docs/autovid-content-guide.md`](docs/autovid-content-guide.md).

**Cách 1 (khuyến nghị cho người viết): chỉ cần text + ảnh**

```bash
python tools/make_project_from_text.py \
    --text narration.txt \
    --images projects/mystery-001/images \
    --out projects/<topic-id>-autovid \
    --title "Tên tập" --genre trinhtham \
    --music data/music/mystery_biggest_discovery.mp3 \
    --scene-seconds 20
```

Quy ước đầu vào:

| Mục | Quy ước |
|---|---|
| Text | UTF-8. **Mỗi đoạn cách nhau bằng dòng trống = 1 scene** (đoạn quá dài tự cắt theo câu, đoạn < 6s tự gộp) |
| Tiêu đề chương | Dòng `[[ Tên chương ]]` hoặc `Chương 3` / `Hồi 12` → thành tiêu đề overlay + section break (ngắt nhịp dài) |
| Ảnh | 1 thư mục (hoặc glob), đọc theo thứ tự tên. Ít ảnh hơn scene thì ảnh được dùng lại vòng (có báo) |
| Nhạc | 1 file; ngắn hơn video sẽ được loop ở stage `mix` |
| Genre | `trinhtham`→**Minh Quân Pro**, `ma`→Đức Trí, `tutien`→Ngọc Huyền, `ngonlu`→Mỹ Duyên (`--voice` để đặt tay) |

Công cụ tự dựng scene, Ken Burns, transition, pacing rồi **chạy luôn validator** để báo ngay nếu nội dung
chưa đạt (ví dụ `runtime_short` khi ước tính < 8 phút).

**Cách 2: tự viết `script.json`** — các khối và giới hạn cứng:

| Khối | Field | Ghi chú |
|---|---|---|
| `video_metadata` | `title`, `author`, `resolution` (`"1920x1080"`), `fps`, `language` | fps ∈ {24,25,30,50,60} |
| `tts_config` | `engine` (`vieneu`), `voice`, `speed` (0.5–2.0), `granularity` (`sentence`/`scene`), `max_chars_per_chunk` | `sentence` = mỗi câu 1 lần đọc → pause engine điều khiển nhịp |
| `audio_config` | `background_music`, `background_volume` (≤ 0.3, khuyến nghị 0.12), `master_volume` (LUFS, vd −14), `fade_in_seconds`, `fade_out_seconds` | nhạc là đường dẫn tính từ workspace rồi tới repo root |
| `pacing` | `auto_pause{enabled, per_sentence, min_pause_ms, max_pause_ms, respect_tts_natural_pause}`, `section_breaks` (id scene), `custom_pauses` (`{"6": 2500}`) | ngắt nhịp dài ở section break |
| `scenes[]` | `id`, `text`, `image_file` **hoặc** `image_prompt`, `pause_after_ms` (≤ 10000), `transition_in{type,duration}`, `ken_burns{enabled,type,start_scale,end_scale}`, `text_overlays[]`, `sfx[]` | xem dưới |

| Field trong scene | Giá trị hợp lệ |
|---|---|
| `ken_burns.type` | `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `none`; scale 1.0–1.5 (pan cần ≥ 1.02 mới thấy chuyển động) |
| `transition_in.type` | `cut`, `fade`, `fade_fast`, `fade_slow`, `dissolve`, `slide_left/right/up/down`, `wipe_left/right/up/down`, `zoom`, `whip_pan`, `pixelize`, `blur`, `circle_open/close`, `squeeze_h/v`, `flash_white/black`, `diag_tl/br`; `duration` ≤ 0.5s |
| `text_overlays[]` | `text`, `font`, `font_size`, `color`, `stroke_color`, `stroke_width`, `position`, `start_offset_ms`, `end_offset_ms`, `animation` (`fade_in`/`pop`/`typewriter`/`slide_in`/`none`), `animation_duration_ms`; tối đa 2 overlay cùng lúc, đọc được ≥ 2000ms, ≥ 40px @1080p |
| `sfx[]` | `file`, `time_offset_ms` (tính từ đầu scene), `volume` ≤ 0.8; cách nhau ≥ 5s |

> Chú ý: `id` phải **tăng dần** và không trùng. Thiếu `image_file` mà có `image_prompt` thì stage 1 chỉ cảnh báo, nhưng phải có ảnh thật trước stage 3.

### Nhân vật hoạt hình (character layers) — nhân vật chính của bạn

Bạn đưa **1 file PNG nền trong suốt** (đã cắt sẵn, chân chạm mép dưới), pipeline ghép lên ảnh nền
và neo **theo câu thoại thật** (stage 2 đã đo từng câu), còn tiếng động do stage `mix` đặt:

```json
"characters": [
  {
    "image_file": "assets/characters/host_point.png",
    "preset": "boing",
    "at_sentence": 2,
    "for_sentences": 3,
    "x": 0.22, "y": 0.92, "height": 0.42,
    "enter": {"type": "drop_bounce", "from": "top", "duration_ms": 900},
    "idle":  {"type": "bob_sway", "amplitude_px": 16, "period_s": 2.4},
    "exit":  {"type": "shrink_out", "duration_ms": 350},
    "sfx":   {"file": "data/sfx/boing.mp3", "volume": 0.5}
  }
]
```

| Nhóm | Giá trị |
|---|---|
| `enter.type` | `none`, `fade_in`, `pop` (nở ra có overshoot), `fly_in` (bay vào), `slide_in` (trượt vào), `drop_bounce` (rơi + nảy), `spin_in` (xoay khi vào), `zoom_in` + `from` = `left/right/top/bottom/top_left/top_right/bottom_left/bottom_right/center` |
| `exit.type` | `none`, `fade_out` (mờ đi), `shrink_out` (nhỏ rồi biến mất), `fly_out`, `slide_out`, `drop_out`, `spin_out`, `zoom_out` + `to` |
| `idle.type` | `none`, `bob` (nhún nhẹ), `sway` (lắc lư), `bob_sway`, `shake` (run rẩy), `talk` (nhún nhanh như đang nói) — `amplitude_px` ≤ 120, `period_s` ≥ 0.2 |
| `preset` | `pop`, `boing`, `whoosh`, `ta_da`, `sneak`, `ninja` — cả một "miếng hài" điền sẵn enter/idle/exit/sfx; field viết tay luôn thắng preset |
| Neo thời gian | `at_sentence` (câu thứ N trong scene, 1-based) + `for_sentences`, hoặc `start_offset_ms`/`end_offset_ms` |
| Vị trí | `x`/`y` theo tỉ lệ khung (`y: 0.92` = chân đứng mép dưới), `height` = chiều cao nhân vật / chiều cao khung (0.05–1.0), `flip` để lật |
| `impact` | cú punch-in cả scene: `{"at_sentence": 4, "intensity": 0.12, "shake_px": 14, "flash": "white", "duration_ms": 450}` |

Nhiều nhân vật trong cùng scene = nhiều phần tử `characters` (đối thoại 2 nhân vật, hoặc 1 nhân vật
kiểu PIP ở góc khung với `height: 0.28, y: 0.35`).

Xem trước **tất cả hiệu ứng** (dựng bằng chính code pipeline, không phải renderer riêng):

```bash
python3 tools/preview_character_effects.py          # -> tmp/character_effects/effects.mp4 (1:04)
python3 tools/preview_character_effects.py --character projects/<id>/assets/characters/nhan-vat.png
python3 tools/make_sfx_pack.py                      # kho SFX hài: whoosh, boing, ta_da, pop, thud, ding…
```

### Kiểm tra nhanh trên workspace giả

```bash
python tools/make_demo_project.py --scenes 8 --out tmp/autovid_e2e --force   # --break-it để thử nhánh lỗi
python autovid.py validate tmp/autovid_e2e/script.json --skip-engine-check
# ... chạy tiếp như trên với tmp/autovid_e2e/script.json
```

### Nghe thử và chọn giọng (audition_voices.py)

VieNeu có **29 preset** (kèm alias) và `validate` đọc đúng catalog của engine đang cài, nên bạn
có thể dùng tên mới nhất như `Minh Quân Pro`, `Mai Anh`, `Trúc Ly`…

```bash
.venv/bin/python tools/audition_voices.py                       # cả 29 giọng, mỗi clip tự xưng tên
.venv/bin/python tools/audition_voices.py --explainer-only      # chỉ phong cách tin tức/tự nhiên
.venv/bin/python tools/audition_voices.py --voices "Minh Quân Pro,Mai Anh" --speed 0.95
```

Kết quả mặc định ở `projects/voice_auditions/`: `all_voices.mp3`, `index.txt` (kèm tốc độ đọc
thật tính bằng ký tự/giây và loudness), và từng clip `NN_Tên-Giọng.mp3`. Có cache nên lần sau
thêm 1-2 giọng chỉ mất ~1 phút.

> Chọn theo tốc độ: **16–19 ký tự/s** là dễ theo nhất cho nội dung giải thích. Giọng >20 ký tự/s
> (Minh Quân Pro 20.1, Trúc Ly ~21, Thùy Dung ~20) nên đặt `"speed": 0.92`; giọng <15 (Minh Đức,
> Đức Trí, Kim Thanh) sẽ làm video dài thêm ~20%.

### Môi trường VieNeu (giọng thật)

```bash
python3 -m venv .venv
.venv/bin/pip install vieneu pillow      # chỉ 2 gói autovid cần
.venv/bin/python autovid.py validate projects/<id>/script.json   # không cần --skip-engine-check nữa
.venv/bin/python autovid.py tts      projects/<id>/script.json   # giọng thật
```

- Model mặc định là **VieNeu-TTS v3 Turbo** (48kHz, ONNX/CPU, không cần GPU, torch-free) và
dùng cache `~/.cache/huggingface` — nếu máy đã có `models--pnnbao-ump--VieNeu-TTS-v3-Turbo` thì không phải tải lại (~495MB).
- Tốc độ ước lượng: ~6s CPU/1 câu ngắn, ~20-40x realtime cho máy 32 luồng → 3 phút audio ≈ 2-3 phút chạy.
- `speed` **không được SDK v3 Turbo áp dụng** (đo thực tế: speed 1.0 và 1.5 cho ra độ dài như nhau),
nên pipeline vẫn time-stretch bằng ffmpeg `atempo` như cũ — không bị nhân đôi tốc độ.

### Ghi chú

- `output/quality_report.json` gộp cả 7 report stage thành một bản "ship được hay chưa",
  kèm mọi warning còn sót (ví dụ `background_music_looped`, `runtime_short`).
- Mix không normalize mù: chỉ chỉnh gain khi lệch khỏi `master_volume` quá 0.5 LUFS và
  chỉ limit khi true peak vượt trần –1.5 dBFS, cả hai đều được ghi lại trong report.
- ffmpeg bundle không có `drawtext`; chữ overlay do PIL render sẵn, phụ đề burn dùng
  libass (`subtitles` filter), còn mặc định là soft sub `mov_text` (không re-encode hình).

### Test

```bash
python -m unittest discover -s tests -p "test_*.py"    # 228 test, gồm stage1–stage7 của autovid
```

---

## 🖼️ Chèn logo (apply_logo.py)

Che watermark của AI tạo ảnh bằng logo kênh. **2 chế độ:**

| | Chế độ Gemini (mặc định) | Chế độ Dola (`--dola`) |
|---|---|---|
| Dùng cho | Ảnh tạo từ Gemini (dấu ✦) | Ảnh tạo từ Dola AI (chữ "Dola AI") |
| Logo mặc định | `projects/logo/logo.png` | `projects/logo/logo-laoto-bip.png` (với `--tutien`) |
| Vị trí | Lệch vào trong (+45px/+65px) để che dấu ✦ | Sát góc dưới phải (lệ 2px), đè thẳng chữ |
| Scale mặc định | 0.09 (sàn 90px) | 0.25 (25% chiều rộng ảnh) |

> ⚠️ Lệnh **ghi đè trực tiếp** lên ảnh — giữ bản sạch ở chỗ khác nếu cần.

### Chèn cho cả topic (thumbnail + toàn bộ images/)

```bash
# Ảnh Gemini
.venv/bin/python apply_logo.py <topic-id>

# Ảnh Dola AI + logo Lão Tổ Bíp (khuyên dùng cho truyện tu tiên)
.venv/bin/python apply_logo.py tt-lathien-1-20 --tutien --dola
```

### Chèn cho 1 ảnh bất kỳ

```bash
# Ảnh Gemini: logo mặc định, hoặc --tutien để dùng logo Lão Tổ Bíp
.venv/bin/python apply_logo.py --image path/to/anh-gemini.jpg
.venv/bin/python apply_logo.py --image path/to/anh-gemini.jpg --tutien

# Ảnh Dola AI: bắt buộc có --dola
.venv/bin/python apply_logo.py --image path/to/anh-dola.png --tutien --dola

# Chỉ định logo riêng (ưu tiên cao hơn --tutien)
.venv/bin/python apply_logo.py --image path/to/anh.png --logo path/to/logo.png --dola

# Chỉnh cỡ logo (0.05 → 0.9) — ví dụ to hơn nếu chữ còn ló
.venv/bin/python apply_logo.py --image path/to/anh-dola.png --tutien --dola --scale 0.3
```

### Ghi chú
- **Đường dẫn có dấu cách phải bọc ngoặc kép**: `--image "projects/.../images copy/ảnh.png"`
- Logo PNG cần **nền trong suốt**; logo tải từ Dola về thường bị nướng nền caro + chữ "Dola AI" — phải làm sạch trước khi dùng
- Nếu chữ "Dola AI" còn ló góc trái sau khi chèn → tăng `--scale 0.27`
- `logo-tutien.png` (logo cũ) đã ngừng dùng, thay bằng `logo-laoto-bip.png`

// bash
# Tu-tien story
.venv/bin/python render_vieneu_long.py -p <project> -g tutien
 
# Horror story
.venv/bin/python render_vieneu_long.py -p <project> -g ma
 
# Mystery story
.venv/bin/python render_vieneu_long.py -p <project> -g trinhtham
 
# Or pick any voice manually if you prefer your original way
.venv/bin/python render_vieneu_long.py -p <project> -v "Đức Trí"

Ví dụ: 
// bash
# 1. Tạo audio MP3 (giọng Ngọc Huyền theo thể loại tu tiên)
.venv/bin/python render_vieneu_long.py -p tt-lathien-1-20 -g tutien

### Ghép Intro, Outro (Sau khi tạo xong MP3)
.venv/bin/python assemble_audio.py tt-lathien-1-20 --tutien
 
# 2. Tạo video MP4 (không phụ đề)
.venv/bin/python generate_video.py tt-lathien-1-20 fast --no-subtitles


### Chèn tên chương vào thumbnail
python add_chapter.py tt-lathien-1-20

