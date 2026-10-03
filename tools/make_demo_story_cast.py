"""Extract a small transparent cast from the existing character reference sheet."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "assets" / "Figure" / "image.png"
CAST = {
    "chi_pheo": (260, 34, 444, 257),
    "thi_no": (464, 42, 612, 257),
    "giao_thu": (270, 326, 412, 532),
    "tu_lang": (648, 322, 784, 532),
    "ly_cuong": (806, 312, 994, 532),
}


def remove_connected_paper(image: Image.Image) -> Image.Image:
    """Remove connected paper and antialias the character's outside edge."""
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def is_paper(x: int, y: int) -> bool:
        red, green, blue, _ = pixels[x, y]
        return red > 190 and green > 155 and blue > 105 and red - green < 45

    def add_if_paper(x: int, y: int) -> None:
        index = y * width + x
        if not visited[index] and is_paper(x, y):
            visited[index] = 1
            queue.append((x, y))

    for x in range(width):
        add_if_paper(x, 0)
        add_if_paper(x, height - 1)
    for y in range(height):
        add_if_paper(0, y)
        add_if_paper(width - 1, y)

    while queue:
        x, y = queue.popleft()
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
        if x:
            add_if_paper(x - 1, y)
        if x + 1 < width:
            add_if_paper(x + 1, y)
        if y:
            add_if_paper(x, y - 1)
        if y + 1 < height:
            add_if_paper(x, y + 1)

    background = image.load()
    edge_pixels: list[tuple[int, int, tuple[int, int, int]]] = []
    for y in range(height):
        for x in range(width):
            if background[x, y][3] == 0:
                continue
            paper_neighbors = [
                background[nx, ny][:3]
                for ny in range(max(0, y - 1), min(height, y + 2))
                for nx in range(max(0, x - 1), min(width, x + 2))
                if background[nx, ny][3] == 0
            ]
            if paper_neighbors:
                paper = tuple(
                    round(sum(color[channel] for color in paper_neighbors) / len(paper_neighbors))
                    for channel in range(3)
                )
                edge_pixels.append((x, y, paper))

    for x, y, paper in edge_pixels:
        red, green, blue, _ = background[x, y]
        observed = (red, green, blue)
        foreground_neighbors = [
            background[nx, ny][:3]
            for ny in range(max(0, y - 1), min(height, y + 2))
            for nx in range(max(0, x - 1), min(width, x + 2))
            if background[nx, ny][3] > 0
        ]
        if not foreground_neighbors:
            continue

        foreground = max(
            foreground_neighbors,
            key=lambda color: sum((color[channel] - paper[channel]) ** 2 for channel in range(3)),
        )
        direction = tuple(foreground[channel] - paper[channel] for channel in range(3))
        denominator = sum(channel * channel for channel in direction)
        if denominator == 0:
            continue
        alpha = sum(
            (observed[channel] - paper[channel]) * direction[channel]
            for channel in range(3)
        ) / denominator
        if not 0.08 < alpha < 0.98:
            continue

        recovered = tuple(
            max(
                0,
                min(
                    255,
                    round(
                        paper[channel]
                        + (observed[channel] - paper[channel]) / alpha
                    ),
                ),
            )
            for channel in range(3)
        )
        background[x, y] = (*recovered, round(alpha * 255))

    bounds = image.getchannel("A").getbbox()
    return image.crop(bounds) if bounds else image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "projects" / "demo_story_inside" / "assets",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sheet = Image.open(SOURCE).convert("RGB")
    cast: dict[str, Image.Image] = {}
    for name, box in CAST.items():
        cast[name] = remove_connected_paper(sheet.crop(box))
        cast[name].save(args.out / f"{name}.png")

    audience = [cast[name] for name in ("giao_thu", "tu_lang", "ly_cuong")]
    heights = (195, 180, 200)
    figures = []
    for figure, target_height in zip(audience, heights):
        target_width = round(figure.width * target_height / figure.height)
        figures.append(
            figure.resize((target_width, target_height), Image.Resampling.LANCZOS)
        )

    gap = 8
    strip_width = sum(figure.width for figure in figures) + gap * (len(figures) - 1)
    strip_height = max(heights)
    strip = Image.new("RGBA", (strip_width, strip_height), (0, 0, 0, 0))
    x = 0
    for figure in figures:
        strip.alpha_composite(figure, (x, strip_height - figure.height))
        x += figure.width + gap
    strip.save(args.out / "village_crowd.png")


if __name__ == "__main__":
    main()
