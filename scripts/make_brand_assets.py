#!/usr/bin/env python3
"""Generate the Cheta brand assets: a bot profile picture and a welcome banner.

Run this rather than editing the PNGs by hand, so the assets are reproducible.

    python3 scripts/make_brand_assets.py

Outputs:
    assets/cheta-icon.png     512x512, for the Telegram bot profile picture
    assets/cheta-welcome.png  1280x640, sent when someone starts a conversation

Telegram crops profile pictures to a circle, so the mark keeps its content well
inside the centre. Text is drawn with a system font; if none is found the script
says so rather than rendering something broken.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "assets"

BACKGROUND = (10, 10, 10)
SURFACE = (20, 20, 20)
ACCENT = (53, 208, 186)
ACCENT_DIM = (46, 92, 86)
TEXT = (237, 237, 237)
MUTED = (138, 138, 138)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise SystemExit("[FAIL] no usable font found; install dejavu or liberation")


def make_icon(path: Path, size: int = 512) -> None:
    image = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image, "RGBA")
    centre = size / 2

    # Concentric rings: the memory layers an agent consolidates over time.
    for index, radius in enumerate((int(size * 0.40), int(size * 0.31), int(size * 0.22))):
        box = (centre - radius, centre - radius, centre + radius, centre + radius)
        draw.ellipse(box, outline=ACCENT_DIM + (150 - index * 40,), width=max(2, size // 90))

    # Nodes on the outer ring: discrete remembered facts.
    outer = size * 0.40
    for step in range(9):
        angle = (2 * math.pi / 9) * step - math.pi / 2
        node_x = centre + outer * math.cos(angle)
        node_y = centre + outer * math.sin(angle)
        dot = size * 0.018
        draw.ellipse(
            (node_x - dot, node_y - dot, node_x + dot, node_y + dot),
            fill=ACCENT + (230,),
        )

    # The monogram. anchor="mm" centres on the glyph's own metrics rather than
    # on a bounding box computed from the origin, which is why the first version
    # sat visibly high and left of centre.
    font = load_font(int(size * 0.40))
    draw.text((centre, centre), "C", font=font, fill=TEXT, anchor="mm")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    print(f"[OK] {path.relative_to(REPO_ROOT)} {image.size[0]}x{image.size[1]}")


def make_welcome(path: Path, width: int = 1280, height: int = 640) -> None:
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image, "RGBA")

    # A quiet band so the banner does not read as a flat rectangle.
    draw.rectangle((0, 0, width, int(height * 0.62)), fill=SURFACE)

    title_font = load_font(148)
    tagline_font = load_font(44)
    label_font = load_font(32)

    title = "CHETA"
    left, top, right, bottom = draw.textbbox((0, 0), title, font=title_font)
    draw.text(
        (width * 0.08 - left, height * 0.20 - top), title, font=title_font, fill=TEXT
    )

    draw.line(
        (width * 0.08, height * 0.20 + (bottom - top) + 26, width * 0.36, height * 0.20 + (bottom - top) + 26),
        fill=ACCENT,
        width=6,
    )

    # Not "remembers you between chats": that is the hackathon premise, which
    # every entrant claims. The differentiator is curation, so the banner names it.
    draw.text(
        (width * 0.08, height * 0.585),
        "every chatbot remembers",
        font=tagline_font,
        fill=MUTED,
    )
    draw.text(
        (width * 0.08, height * 0.665),
        "this one decides what to forget",
        font=tagline_font,
        fill=ACCENT,
    )

    labels = ("TELEGRAM", "CLI", "BROWSER", "EXTENSION")
    x = width * 0.08
    for label in labels:
        draw.text((x, height * 0.79), label, font=label_font, fill=ACCENT_DIM)
        advance = draw.textbbox((0, 0), label, font=label_font)[2] + 42
        x += advance

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    print(f"[OK] {path.relative_to(REPO_ROOT)} {image.size[0]}x{image.size[1]}")


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    make_icon(ASSETS / "cheta-icon.png")
    make_welcome(ASSETS / "cheta-welcome.png")
    print("[OK] assets written. Set the profile picture by hand in @BotFather.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
