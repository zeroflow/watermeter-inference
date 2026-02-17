"""Synthetic training data generator for watermeter digits and arrows."""

import hashlib
import logging
import math
import random
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
_FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf"

DIGIT_CLASSES = [str(i) for i in range(10)]
ARROW_CLASSES = [f"{i}.{j}" for i in range(10) for j in range(10)]


def _deterministic_seed(cls: str, i: int) -> int:
    """Deterministic hash for reproducible seed derivation."""
    h = hashlib.sha256(f"{cls}:{i}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [_FONT_PATH, _FALLBACK_FONT_PATH]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class DigitRenderer:
    WIDTH = 20
    HEIGHT = 32

    def __init__(self):
        self._font = _load_font(38)

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

    def __init__(self):
        self._num_font = _load_font(11)

    def render(self, arrow_class: str) -> Image.Image:
        if arrow_class not in ARROW_CLASSES:
            raise ValueError(f"Invalid arrow class: {arrow_class!r}")

        value = float(arrow_class)
        angle_deg = (value / 10.0) * 360.0

        img = Image.new("RGB", (self.SIZE, self.SIZE), (220, 218, 215))
        draw = ImageDraw.Draw(img)
        cx, cy = self.SIZE / 2, self.SIZE / 2
        radius = self.SIZE / 2 - 4

        # Major tick marks only (10 ticks for 0-9) — thick like real dial
        for tick in range(10):
            tick_angle = math.radians((tick / 10.0) * 360.0 - 90)
            outer_r = radius
            inner_r = radius - 10
            x1 = cx + inner_r * math.cos(tick_angle)
            y1 = cy + inner_r * math.sin(tick_angle)
            x2 = cx + outer_r * math.cos(tick_angle)
            y2 = cy + outer_r * math.sin(tick_angle)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=3)

        # Dial numbers 0-9 rendered INSIDE the tick marks
        num_radius = radius - 20  # inside the ticks
        for digit in range(10):
            num_angle = math.radians((digit / 10.0) * 360.0 - 90)
            nx = cx + num_radius * math.cos(num_angle)
            ny = cy + num_radius * math.sin(num_angle)
            text = str(digit)
            bbox = draw.textbbox((0, 0), text, font=self._num_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (nx - tw / 2 - bbox[0], ny - th / 2 - bbox[1]),
                text,
                fill="black",
                font=self._num_font,
            )

        # Red pointer — fat wedge: rectangular base at center, tapers to point
        pointer_angle = math.radians(angle_deg - 90)
        pointer_len = radius - 14
        tip_x = cx + pointer_len * math.cos(pointer_angle)
        tip_y = cy + pointer_len * math.sin(pointer_angle)

        perp_angle = pointer_angle + math.pi / 2

        # Rectangular base section (wider) from center to ~40% of length
        base_half_w = 7
        mid_frac = 0.4
        mid_half_w = 6
        mid_x = cx + pointer_len * mid_frac * math.cos(pointer_angle)
        mid_y = cy + pointer_len * mid_frac * math.sin(pointer_angle)

        # Base corners (at center)
        bx1 = cx + base_half_w * math.cos(perp_angle)
        by1 = cy + base_half_w * math.sin(perp_angle)
        bx2 = cx - base_half_w * math.cos(perp_angle)
        by2 = cy - base_half_w * math.sin(perp_angle)

        # Mid-section corners (where taper begins)
        mx1 = mid_x + mid_half_w * math.cos(perp_angle)
        my1 = mid_y + mid_half_w * math.sin(perp_angle)
        mx2 = mid_x - mid_half_w * math.cos(perp_angle)
        my2 = mid_y - mid_half_w * math.sin(perp_angle)

        # Draw as single polygon: base rect -> taper to tip
        draw.polygon(
            [
                (bx1, by1),
                (mx1, my1),
                (tip_x, tip_y),
                (mx2, my2),
                (bx2, by2),
            ],
            fill=(200, 30, 30),
        )

        # Center hub circle (dark, like the real dial pivot)
        hub_r = 6
        draw.ellipse(
            [cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r],
            fill=(60, 60, 60),
        )

        return img


class TransformPipeline:
    """Applies realistic distortions to master images."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._np_rng = np.random.RandomState(seed)

    def apply(self, img: Image.Image, mode: str = "digit") -> Image.Image:
        original_size = img.size
        arr = np.array(img)

        # --- Geometric transforms (OpenCV) ---
        # X/Y offset
        if self._rng.random() < 0.8:
            max_shift = 3 if mode == "digit" else 5
            dx = self._rng.randint(-max_shift, max_shift)
            dy = self._rng.randint(-max_shift, max_shift)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            arr = cv2.warpAffine(arr, M, (arr.shape[1], arr.shape[0]),
                                 borderMode=cv2.BORDER_REPLICATE)

        # Perspective warp
        if self._rng.random() < 0.7:
            strength = 3 if mode == "digit" else 8
            h, w = arr.shape[:2]
            pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
            pts2 = np.float32([
                [self._rng.randint(0, strength), self._rng.randint(0, strength)],
                [w - self._rng.randint(0, strength), self._rng.randint(0, strength)],
                [self._rng.randint(0, strength), h - self._rng.randint(0, strength)],
                [w - self._rng.randint(0, strength), h - self._rng.randint(0, strength)],
            ])
            M = cv2.getPerspectiveTransform(pts1, pts2)
            arr = cv2.warpPerspective(arr, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        # Fisheye / barrel distortion (arrows only)
        if mode == "arrow" and self._rng.random() < 0.8:
            h, w = arr.shape[:2]
            k1 = self._rng.uniform(0.1, 0.4)
            cx, cy = w / 2, h / 2
            Y, X = np.mgrid[0:h, 0:w]
            nx = (X.astype(np.float32) - cx) / cx
            ny = (Y.astype(np.float32) - cy) / cy
            r = np.sqrt(nx ** 2 + ny ** 2)
            nr = r * (1 + k1 * r ** 2)
            safe_r = np.maximum(r, 1e-6)
            map_x = (cx + nr * (nx / safe_r) * cx).astype(np.float32)
            map_y = (cy + nr * (ny / safe_r) * cy).astype(np.float32)
            arr = cv2.remap(arr, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)

        # --- Color transforms ---
        arr = arr.astype(np.float32)

        # Brightness
        if self._rng.random() < 0.8:
            factor = self._rng.uniform(0.7, 1.3)
            arr = arr * factor

        # Contrast
        if self._rng.random() < 0.7:
            factor = self._rng.uniform(0.8, 1.2)
            mean = arr.mean()
            arr = (arr - mean) * factor + mean

        # Color cast
        if self._rng.random() < 0.5:
            cast = np.array([
                self._rng.uniform(-10, 10),
                self._rng.uniform(-10, 10),
                self._rng.uniform(-10, 10),
            ], dtype=np.float32)
            arr = arr + cast

        # Background variation
        if self._rng.random() < 0.6:
            bg_shift = self._rng.uniform(-15, 0)
            white_mask = (arr > 200).all(axis=2)
            arr[white_mask] += bg_shift

        # Shadow
        if self._rng.random() < 0.5:
            h, w = arr.shape[:2]
            shadow_dir = self._rng.uniform(0, 2 * math.pi)
            Y, X = np.mgrid[0:h, 0:w]
            cx_s, cy_s = w / 2, h / 2
            gradient = ((X - cx_s) * math.cos(shadow_dir) + (Y - cy_s) * math.sin(shadow_dir))
            gradient = gradient / max(gradient.max() - gradient.min(), 1e-6)
            shadow_strength = self._rng.uniform(10, 30)
            arr = arr - (gradient[:, :, np.newaxis] * shadow_strength)

        # Vignetting (arrows only)
        if mode == "arrow" and self._rng.random() < 0.6:
            h, w = arr.shape[:2]
            Y, X = np.mgrid[0:h, 0:w]
            cx_v, cy_v = w / 2, h / 2
            dist = np.sqrt((X - cx_v) ** 2 + (Y - cy_v) ** 2)
            max_dist = math.sqrt(cx_v ** 2 + cy_v ** 2)
            vignette = 1.0 - (dist / max_dist) ** 2 * self._rng.uniform(0.2, 0.5)
            arr = arr * vignette[:, :, np.newaxis]

        # Clip and convert
        arr = np.clip(arr, 0, 255).astype(np.uint8)

        # Gaussian noise
        if self._rng.random() < 0.7:
            sigma = self._rng.uniform(5, 15)
            noise = self._np_rng.normal(0, sigma, arr.shape).astype(np.float32)
            arr = np.clip(arr.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        # Blur
        if self._rng.random() < 0.5:
            sigma = self._rng.uniform(0.3, 1.5)
            ksize = int(sigma * 4) | 1
            if ksize >= 3:
                arr = cv2.GaussianBlur(arr, (ksize, ksize), sigma)

        # JPEG artifacts
        if self._rng.random() < 0.6:
            quality = self._rng.randint(60, 95)
            _, encoded = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            arr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

        result = Image.fromarray(arr)
        if result.size != original_size:
            result = result.resize(original_size, Image.Resampling.BILINEAR)
        return result


class SyntheticGenerator:
    """Orchestrates synthetic data generation."""

    def __init__(self, base_dir: str = "/training"):
        self.base_dir = Path(base_dir)

    def generate(
        self,
        type: str,
        count_per_class: int = 500,
        seed: int = 42,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict:
        stats = {"digits": 0, "arrows": 0}
        types = []
        if type in ("digits", "both"):
            types.append("digits")
        if type in ("arrows", "both"):
            types.append("arrows")

        total_images = 0
        for t in types:
            classes = DIGIT_CLASSES if t == "digits" else ARROW_CLASSES
            total_images += len(classes) * count_per_class

        current = 0
        for t in types:
            if t == "digits":
                renderer = DigitRenderer()
                classes = DIGIT_CLASSES
                mode = "digit"
            else:
                renderer = ArrowRenderer()
                classes = ARROW_CLASSES
                mode = "arrow"

            gt_dir = self.base_dir / t / "ground_truth"

            for cls in classes:
                class_dir = gt_dir / cls
                class_dir.mkdir(parents=True, exist_ok=True)
                master = renderer.render(cls)

                for i in range(count_per_class):
                    img_seed = seed + _deterministic_seed(cls, i)
                    pipeline = TransformPipeline(seed=img_seed)
                    transformed = pipeline.apply(master.copy(), mode=mode)
                    filename = f"synth_{i:04d}.jpg"
                    transformed.save(str(class_dir / filename), "JPEG", quality=90)
                    stats[t] += 1
                    current += 1

                    if progress_callback and current % 10 == 0:
                        progress_callback(current, total_images, f"Generating {t} class {cls}")

        if progress_callback:
            progress_callback(total_images, total_images, "Generation complete")

        return stats

    def delete_synthetic(self, type: str) -> int:
        """Delete all synthetic images (synth_* prefix) from ground truth."""
        deleted = 0
        types = []
        if type in ("digits", "both"):
            types.append("digits")
        if type in ("arrows", "both"):
            types.append("arrows")

        for t in types:
            gt_dir = self.base_dir / t / "ground_truth"
            if not gt_dir.exists():
                continue
            for synth_file in gt_dir.rglob("synth_*.jpg"):
                synth_file.unlink()
                deleted += 1

        return deleted
