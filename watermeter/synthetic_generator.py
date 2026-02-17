"""Synthetic training data generator for watermeter digits and arrows."""

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
_FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf"

DIGIT_CLASSES = [str(i) for i in range(10)]
ARROW_CLASSES = [f"{i}.{j}" for i in range(10) for j in range(10)]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [_FONT_PATH, _FALLBACK_FONT_PATH]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class DigitRenderer:
    WIDTH = 20
    HEIGHT = 32

    def __init__(self):
        self._font = _load_font(28)

    def render(self, digit_class: str) -> Image.Image:
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}. Must be one of {DIGIT_CLASSES}")

        img = Image.new("RGB", (self.WIDTH, self.HEIGHT), "white")
        draw = ImageDraw.Draw(img)

        bbox = draw.textbbox((0, 0), digit_class, font=self._font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (self.WIDTH - text_w) / 2 - bbox[0]
        y = (self.HEIGHT - text_h) / 2 - bbox[1]

        draw.text((x, y), digit_class, fill="black", font=self._font)
        return img


class ArrowRenderer:
    SIZE = 100

    def render(self, arrow_class: str) -> Image.Image:
        if arrow_class not in ARROW_CLASSES:
            raise ValueError(f"Invalid arrow class: {arrow_class!r}")

        value = float(arrow_class)
        angle_deg = (value / 10.0) * 360.0

        img = Image.new("RGB", (self.SIZE, self.SIZE), "white")
        draw = ImageDraw.Draw(img)
        cx, cy = self.SIZE / 2, self.SIZE / 2
        radius = self.SIZE / 2 - 5

        # Major tick marks
        for tick in range(10):
            tick_angle = math.radians((tick / 10.0) * 360.0 - 90)
            outer_r = radius
            inner_r = radius - 10
            x1 = cx + inner_r * math.cos(tick_angle)
            y1 = cy + inner_r * math.sin(tick_angle)
            x2 = cx + outer_r * math.cos(tick_angle)
            y2 = cy + outer_r * math.sin(tick_angle)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=2)

        # Minor tick marks
        for tick in range(100):
            if tick % 10 == 0:
                continue
            tick_angle = math.radians((tick / 100.0) * 360.0 - 90)
            outer_r = radius
            inner_r = radius - 4
            x1 = cx + inner_r * math.cos(tick_angle)
            y1 = cy + inner_r * math.sin(tick_angle)
            x2 = cx + outer_r * math.cos(tick_angle)
            y2 = cy + outer_r * math.sin(tick_angle)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=1)

        # Center dot
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill="black")

        # Red pointer
        pointer_angle = math.radians(angle_deg - 90)
        pointer_len = radius - 14
        px = cx + pointer_len * math.cos(pointer_angle)
        py = cy + pointer_len * math.sin(pointer_angle)
        draw.line([(cx, cy), (px, py)], fill="red", width=3)

        # Arrowhead triangle
        tip_size = 4
        perp_angle = pointer_angle + math.pi / 2
        t1 = (px, py)
        t2 = (px - tip_size * math.cos(perp_angle) - tip_size * math.cos(pointer_angle),
              py - tip_size * math.sin(perp_angle) - tip_size * math.sin(pointer_angle))
        t3 = (px + tip_size * math.cos(perp_angle) - tip_size * math.cos(pointer_angle),
              py + tip_size * math.sin(perp_angle) - tip_size * math.sin(pointer_angle))
        draw.polygon([t1, t2, t3], fill="red")

        return img
