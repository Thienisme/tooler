# Demo Story Inside

Project nay la mau to chuc asset cho cac project video tiep theo.

## Cau truc asset

```text
projects/demo_story_inside/
├── images/
│   └── backgrounds/          # Anh canh nen dang duoc script su dung
├── assets/
│   ├── characters/           # Sprite nhan vat; PNG trong suot neu ghep len canh
│   └── source/
│       ├── character_sheets/ # Sheet goc, khong chinh sua
│       └── cutout_tests/
│           ├── input/        # Anh dau vao thu nghiem
│           └── output/       # Ket qua tach nen thu nghiem
├── audio/                    # Audio da tao cho project
├── cache/                    # Cache pipeline, khong sua tay
├── output/                   # Video va bao cao render
└── script.json
```

Anh canh phang/anh scene duoc tham chieu bang `scenes[].image_file`. Sprite nhan vat duoc tham chieu trong `scenes[].characters[].image_file`. Anh nguon va ket qua thu nghiem khong nen duoc tham chieu truc tiep trong script; chi dua ban da duyet vao `assets/characters/` hoac `images/backgrounds/`.

## Asset hien tai

- Nen canh dang dung: `images/backgrounds/village_gate.png`.
- Sprite nhan vat dang dung: `assets/characters/village_crowd.png`, `assets/characters/thi_no.png`, `assets/characters/chi_pheo.png`.
- Cac sprite nhan vat con lai nam trong `assets/characters/`, san sang de dung khi can.
- `assets/source/character_sheets/village_character_sheet.png` la sheet tham khao 5 cot x 3 hang, co chu thich tren anh. Day khong phai sheet 3:2 gom 6 o cua `tools/split_character_sheet.py`; khong chay splitter do len sheet nay.
- Anh va ket qua thu nghiem tach nen nam trong `assets/source/cutout_tests/` de giu lai ban goc va de so sanh.

## Quy trinh cho sheet nhan vat moi

1. Luu sheet goc trong `assets/source/character_sheets/`.
2. Chi dung `tools/split_character_sheet.py` neu sheet co dung ti le 3:2 va 6 o deu, 3 cot x 2 hang. Tool cat theo toa do co dinh, khong tu nhan dien nhan vat.
3. Ghi ket qua crop vao khu vuc thu nghiem va tach nen cho cac PNG goc:

```bash
.venv/bin/python tools/split_character_sheet.py \
  projects/demo_story_inside/assets/source/character_sheets/character_sheet.png \
  --out projects/demo_story_inside/assets/source/cutout_tests/output/character_parts \
  --names character_01 character_02 character_03 character_04 character_05 character_06

.venv/bin/python tools/remove_background.py \
  projects/demo_story_inside/assets/source/cutout_tests/output/character_parts \
  --glob "character_[0-9][0-9].png" --suffix _cut
```

4. Kiem tra tung ket qua. Chi copy/doi ten cac sprite da duyet vao `assets/characters/`, roi cap nhat `script.json` dung duong dan moi. Khong chay lai voi glob `*.png` trong thu muc da co ket qua `_cut`, vi no co the xu ly ca dau ra cu.
5. Dat anh nen scene da duyet vao `images/backgrounds/`, roi cap nhat `scenes[].image_file`.

## Render

Sau khi sua `script.json` hoac thay asset, chay lai cac stage pipeline can thiet de cap nhat `output/preview_video.mp4`. Cac file trong `cache/` va `output/` la ket qua sinh tu pipeline, khong phai nguon asset.

## Doi bo cuc giua cac scene

`story_frame` o cap script la bo cuc mac dinh. Them `"story_frame": false` vao scene can bo khung TV va narrator cua khung; scene khong co truong nay van ke thua khung TV. Vi du mot scene ke chuyen co khung, mot scene doi thoai full-screen, roi quay lai khung:

```json
{
  "story_frame": { "use": "ke_su", "style": "tv_retro" },
  "scenes": [
    {
      "id": 1,
      "text": "Nguoi dan chuyen mo canh trong khung TV.",
      "image_file": "projects/demo_story_inside/images/backgrounds/image.png"
    },
    {
      "id": 2,
      "text": "Chi Pheo va Thi No doi thoai tren toan man hinh.",
      "image_file": "projects/demo_story_inside/images/backgrounds/image01.png",
      "story_frame": false,
      "characters": [
        { "image_file": "projects/demo_story_inside/assets/characters/chi_pheo.png", "x": 0.40, "y": 0.90, "height": 0.42 },
        { "image_file": "projects/demo_story_inside/assets/characters/thi_no.png", "x": 0.62, "y": 0.90, "height": 0.38 }
      ]
    },
    {
      "id": 3,
      "text": "Nguoi dan chuyen tro lai de ket noi cau chuyen.",
      "image_file": "projects/demo_story_inside/images/backgrounds/image02.png"
    }
  ]
}
```

`story_frame: false` chi tat frame va narrator duoc inject boi frame; cac nhan vat scene van render nhu thuong. Nen dung toi da hai nhan vat trong scene doi thoai va canh chinh x/y theo noi dung background. Audio can co mot doan narration khop voi moi scene khi chay lai TTS/assembly.