"""
Pollinations.ai Image Generator - Free, no API key needed.

Uses Pollinations.ai API to generate images from text prompts.
Supports custom seeds for reproducibility.
"""

import time
import urllib.parse
from pathlib import Path

import requests


class PollinationsImageGenerator:
    """
    Generate images using Pollinations.ai API.

    Free tier: No API key required.
    Resolution: Up to 2560x1440 (16:9).
    """

    BASE_URL = "https://image.pollinations.ai/prompt"

    def __init__(
        self,
        width: int = 2560,
        height: int = 1440,
        timeout: int = 90,
        retry_delay: int = 5,
        max_retries: int = 3,
    ):
        self.width = width
        self.height = height
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.max_retries = max_retries

    def generate(
        self,
        prompt: str,
        output_path: Path,
        seed: int | None = None,
    ) -> Path:
        """
        Generate an image from a text prompt.

        Args:
            prompt: Text description of the image
            output_path: Where to save the image
            seed: Optional seed for reproducibility

        Returns:
            Path to saved image
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build URL
        encoded_prompt = urllib.parse.quote(prompt)
        params = f"?width={self.width}&height={self.height}&nologo=true"
        if seed is not None:
            params += f"&seed={seed}"

        url = f"{self.BASE_URL}/{encoded_prompt}{params}"

        # Request with retry
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                print(
                    f"  Generating image (attempt {attempt}/{self.max_retries})..."
                )

                response = requests.get(url, timeout=self.timeout)

                if response.status_code == 200:
                    output_path.write_bytes(response.content)
                    print(
                        f"  ✅ Image saved: {output_path.name} "
                        f"({output_path.stat().st_size // 1024}KB)"
                    )
                    return output_path
                else:
                    last_error = f"HTTP {response.status_code}"
                    print(f"  ⚠️ HTTP {response.status_code}")

            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                print(f"  ❌ {last_error}")

            if attempt < self.max_retries:
                print(f"  Retrying in {self.retry_delay}s...")
                time.sleep(self.retry_delay)

        raise RuntimeError(
            f"Image generation failed after {self.max_retries} attempts: {last_error}"
        )

    def generate_batch(
        self,
        prompts: list[dict],
        output_dir: Path,
    ) -> list[Path]:
        """
        Generate multiple images.

        Args:
            prompts: List of {"prompt": str, "filename": str, "seed": int}
            output_dir: Directory to save images

        Returns:
            List of paths to saved images
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []

        for i, item in enumerate(prompts, 1):
            prompt = item["prompt"]
            filename = item.get("filename", f"image_{i:02d}.jpg")
            seed = item.get("seed", i * 42)

            output_path = output_dir / filename
            print(f"\n[{i}/{len(prompts)}] {filename}")

            try:
                self.generate(prompt, output_path, seed=seed)
                paths.append(output_path)
            except Exception as e:
                print(f"  ❌ Failed: {e}")
                paths.append(None)

            # Rate limit between requests
            if i < len(prompts):
                time.sleep(3)

        return paths
