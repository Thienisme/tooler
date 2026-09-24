"""
Gemini Image Generator - tạo ảnh minh họa cho autovid bằng Nano Banana.

Dùng model image của Gemini API (mặc định ``gemini-2.5-flash-image``) để sinh
ảnh 2D cartoon cho từng scene trong ``script.json``. Hỗ trợ:
- ảnh mẫu (reference image) để giữ đúng style/nhân vật,
- aspect ratio theo khung video (16:9 hoặc 9:16),
- retry có backoff cho rate limit / server error.

Cần ``GEMINI_API_KEY`` trong ``.env`` hoặc môi trường.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"

# Style mặc định cho ảnh scene 2D cartoon (dựa trên mẫu "Tạo hình nhân vật"
# trong notes/autovid-image-style.md, chuyển sang mô tả cảnh thay vì character sheet).
DEFAULT_SCENE_STYLE = """A 2D cartoon illustration in a minimalist, humorous, and highly expressive animation style, depicting a funny and goofy scene in an incredibly comical, simplified 2D aesthetic.

Characters in the scene have a rounded, slightly chunky, slouching body with noodle-like arms and tiny, simplified legs. Their poses are highly expressive, goofy and awkward. Their faces are hilarious and expressive: giant mismatched eyes with tiny pupils (or dotted eyes), funny eyebrows, a big bulbous nose, and a goofy, gaping mouth showing comic emotions (like shouting, being confused, or laughing). Messy hair and simple, exaggerated features add to the comedic look. Clothing is simplified, flat-colored with visible stitching and messy details.

Scene to illustrate: {text}

The artwork features thick, rough black outlines and vibrant, flat coloring with no complex shading. Humorous, playful, and hand-drawn comic animation style. No text, no letters, no captions, no watermark, no logo anywhere in the image."""

WIDE_COMPOSITION = (
    "Wide cinematic 16:9 composition with some negative space near the top "
    "for a title overlay."
)
VERTICAL_COMPOSITION = (
    "Vertical 9:16 composition with the main action centered and some "
    "negative space near the top for a title overlay."
)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def build_scene_prompt(
    scene_text: str,
    style: str = DEFAULT_SCENE_STYLE,
    aspect_ratio: str = "16:9",
    extra_instructions: str | None = None,
) -> str:
    """Ghép style template + lời kể scene thành prompt hoàn chỉnh.

    Style template có thể chứa ``{text}`` — chỗ chèn nội dung scene. Nếu
    template không chứa ``{text}``, nội dung scene được nối vào cuối.
    """
    if "{text}" in style:
        prompt = style.replace("{text}", scene_text.strip())
    else:
        prompt = f"{style.strip()}\n\nScene to illustrate: {scene_text.strip()}"

    composition = VERTICAL_COMPOSITION if aspect_ratio == "9:16" else WIDE_COMPOSITION
    prompt = f"{prompt}\n\n{composition}"

    if extra_instructions:
        prompt = f"{prompt}\n\n{extra_instructions.strip()}"

    return prompt


class GeminiImageGenerator:
    """Sinh 1 ảnh từ prompt bằng Gemini image model."""

    def __init__(
        self,
        model: str = DEFAULT_IMAGE_MODEL,
        api_key: str | None = None,
        max_retries: int = 4,
        request_delay: float = 2.0,
    ):
        try:
            from google import genai
            from google.genai import errors  # noqa: F401 - dùng trong _call
        except ImportError as exc:
            raise RuntimeError(
                "Chưa cài google-genai. Chạy: pip install google-genai"
            ) from exc

        self._errors = errors
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured (thêm vào .env hoặc export)"
            )

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.request_delay = request_delay
        self._last_request_at: float | None = None

    # ------------------------------------------------------------------ API

    def generate(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str = "16:9",
        reference_images: list[Path] | None = None,
    ) -> Path:
        """Sinh ảnh và lưu vào ``output_path``, trả về đường dẫn đã lưu."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        contents = self._build_contents(prompt, reference_images or [])
        config = self._build_config(aspect_ratio)

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._pace()
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                self._last_request_at = time.monotonic()

                image_bytes = self._extract_image_bytes(response)
                if image_bytes:
                    output_path.write_bytes(image_bytes)
                    return output_path

                hint = (response.text or "").strip()[:200]
                raise RuntimeError(
                    "response has no image data" + (f"; model said: {hint}" if hint else "")
                )

            except Exception as exc:  # noqa: BLE001 - retry mọi lỗi như pollinations
                last_error = exc
                if self._is_fatal(exc):
                    raise RuntimeError(f"Gemini image generation failed: {exc}") from exc

                print(f"  ⚠️ {type(exc).__name__}: {exc}")
                if attempt >= self.max_retries:
                    break
                delay = min(2 ** (attempt - 1) * 4, 60)
                print(f"  Retrying in {delay}s... (attempt {attempt + 1}/{self.max_retries})")
                time.sleep(delay)

        raise RuntimeError(
            f"Image generation failed after {self.max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------- helpers

    def _build_contents(self, prompt: str, reference_images: list[Path]):
        from google.genai import types

        parts: list = []
        for ref in reference_images:
            mime = _MIME_BY_SUFFIX.get(ref.suffix.lower())
            if not mime:
                raise RuntimeError(f"Unsupported reference image type: {ref.suffix} ({ref})")
            parts.append(
                types.Part.from_bytes(data=ref.read_bytes(), mime_type=mime)
            )
        parts.append(prompt)
        return parts

    def _build_config(self, aspect_ratio: str):
        """Dựng GenerateContentConfig, chịu được SDK cũ thiếu ImageConfig."""
        from google.genai import types

        image_config = None
        try:
            image_config = types.ImageConfig(aspect_ratio=aspect_ratio)
        except Exception:  # noqa: BLE001 - SDK cũ
            image_config = None

        for modalities in (["IMAGE"], ["TEXT", "IMAGE"], None):
            kwargs: dict = {}
            if modalities:
                kwargs["response_modalities"] = modalities
            if image_config is not None:
                kwargs["image_config"] = image_config
            try:
                return types.GenerateContentConfig(**kwargs)
            except Exception:  # noqa: BLE001 - thử tổ hợp kế tiếp
                continue

        return None

    def _extract_image_bytes(self, response) -> bytes | None:
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if not data:
                    continue
                if isinstance(data, str):
                    data = base64.b64decode(data)
                return bytes(data)
        return None

    def _is_fatal(self, exc: Exception) -> bool:
        """Lỗi auth/quota sai key không nên retry."""
        errors = self._errors
        if errors is not None and isinstance(exc, errors.ClientError):
            message = str(exc)
            if getattr(exc, "code", None) == 401:
                return True
            if "API_KEY_INVALID" in message or "API key not valid" in message:
                return True
        return False

    def _pace(self) -> None:
        if self.request_delay <= 0 or self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.request_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
