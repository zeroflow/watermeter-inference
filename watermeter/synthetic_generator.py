"""Synthetic training data generator for watermeter digits and arrows."""

import logging
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
