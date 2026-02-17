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

_FONT_PATH = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
_FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"

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
    # Render at high resolution then downscale for crisp, format-filling digits
    _RENDER_SCALE = 8

    # Style parameters measured from real meter counter photos
    # (measured from digits/reference/0/ and digits/reference/3/)
    BG_COLOR = (160, 179, 159)            # light gray-green wheel surface
    DIGIT_COLOR = (29, 41, 39)            # very dark greenish digit
    BG_NOISE_SIGMA = 5                     # subtle texture noise

    def __init__(self):
        self._font = _load_font(self.HEIGHT * self._RENDER_SCALE)

    def render(self, digit_class: str, seed: Optional[int] = None) -> Image.Image:
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}. Must be one of {DIGIT_CLASSES}")

        # Render large, then crop tight, then resize to fill target
        big_size = self.HEIGHT * self._RENDER_SCALE
        big = Image.new("RGB", (big_size, big_size), self.BG_COLOR)
        draw = ImageDraw.Draw(big)
        bbox = draw.textbbox((0, 0), digit_class, font=self._font)
        x = (big_size - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (big_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((x, y), digit_class, fill=self.DIGIT_COLOR, font=self._font)

        # Crop to tight bounding box of the digit with small margin
        arr = np.array(big)
        dark_threshold = np.array(self.BG_COLOR).mean() - 20
        dark = arr.mean(axis=2) < dark_threshold
        if dark.any():
            rows = np.where(dark.any(axis=1))[0]
            cols = np.where(dark.any(axis=0))[0]
            margin = max(2, int(0.05 * (rows[-1] - rows[0])))
            top = max(0, rows[0] - margin)
            bot = min(big_size, rows[-1] + margin)
            left = max(0, cols[0] - margin)
            right = min(big_size, cols[-1] + margin)
            big = big.crop((left, top, right, bot))

        # Resize to fill the target canvas
        result = big.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)

        # Add subtle background noise/texture (seeded per-class for reproducibility)
        arr = np.array(result).astype(np.float32)
        rng = np.random.RandomState(hash(digit_class) & 0xFFFFFFFF)
        noise = rng.normal(0, self.BG_NOISE_SIGMA, arr.shape)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        return Image.fromarray(arr)


class DigitCompositor:
    """Composites rendered digit glyphs onto real photo backgrounds.

    Loads background templates from a directory and randomly selects one
    per render call, then composites a digit glyph onto it.
    """

    WIDTH = 20
    HEIGHT = 32
    _RENDER_SCALE = 8
    DIGIT_COLOR = (29, 41, 39)  # very dark greenish, measured from reference

    _FONT_PATHS = [
        "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Black.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
    ]

    def __init__(self, background_dir: str):
        self._backgrounds = []
        bg_path = Path(background_dir)
        for ext in ("*.jpg", "*.jpeg", "*.png"):
            for f in sorted(bg_path.glob(ext)):
                img = Image.open(f).convert("RGB")
                self._backgrounds.append(img)
        if not self._backgrounds:
            raise ValueError(f"No background images found in {background_dir}")
        self._fonts = []
        font_size = self.HEIGHT * self._RENDER_SCALE
        for path in self._FONT_PATHS:
            if Path(path).exists():
                self._fonts.append(ImageFont.truetype(path, font_size))
        if not self._fonts:
            self._fonts.append(_load_font(font_size))
        logger.info(f"DigitCompositor loaded {len(self._backgrounds)} backgrounds, {len(self._fonts)} fonts")

    def render(self, digit_class: str, seed: Optional[int] = None) -> Image.Image:
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}")

        # Pick background and font randomly (seed for reproducibility if given)
        if seed is not None:
            rng = np.random.RandomState(seed & 0xFFFFFFFF)
        else:
            rng = np.random.RandomState()
        bg = self._backgrounds[rng.randint(len(self._backgrounds))].copy()
        bg = bg.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)
        font = self._fonts[rng.randint(len(self._fonts))]

        # Render digit glyph as RGBA (transparent background)
        big_size = self.HEIGHT * self._RENDER_SCALE
        glyph = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        bbox = draw.textbbox((0, 0), digit_class, font=font)
        x = (big_size - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (big_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((x, y), digit_class, fill=(*self.DIGIT_COLOR, 255), font=font)

        # Crop tight to digit bounding box
        alpha = np.array(glyph)[:, :, 3]
        if alpha.any():
            rows = np.where(alpha.any(axis=1))[0]
            cols = np.where(alpha.any(axis=0))[0]
            margin = max(2, int(0.05 * (rows[-1] - rows[0])))
            top = max(0, rows[0] - margin)
            bot = min(big_size, rows[-1] + margin)
            left = max(0, cols[0] - margin)
            right = min(big_size, cols[-1] + margin)
            glyph = glyph.crop((left, top, right, bot))

        # Resize glyph to fit within background (with small padding)
        pad = 2
        glyph = glyph.resize(
            (self.WIDTH - 2 * pad, self.HEIGHT - 2 * pad),
            Image.Resampling.LANCZOS,
        )

        # Composite glyph onto background
        bg.paste(glyph, (pad, pad), glyph)  # use alpha as mask
        return bg


class ArrowRenderer:
    SIZE = 80

    def __init__(self):
        self._num_font = _load_font(max(8, int(self.SIZE * 0.12)))

    def render(self, arrow_class: str, seed: Optional[int] = None) -> Image.Image:
        if arrow_class not in ARROW_CLASSES:
            raise ValueError(f"Invalid arrow class: {arrow_class!r}")

        value = float(arrow_class)
        angle_deg = (value / 10.0) * 360.0
        S = self.SIZE

        img = Image.new("RGB", (S, S), (220, 218, 215))
        draw = ImageDraw.Draw(img)
        cx, cy = S / 2, S / 2
        radius = S / 2 - S * 0.05  # tick outer edge

        # Major tick marks only (10 ticks for 0-9)
        tick_len = S * 0.10
        tick_w = max(2, int(S * 0.035))
        for tick in range(10):
            a = math.radians((tick / 10.0) * 360.0 - 90)
            x1 = cx + (radius - tick_len) * math.cos(a)
            y1 = cy + (radius - tick_len) * math.sin(a)
            x2 = cx + radius * math.cos(a)
            y2 = cy + radius * math.sin(a)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=tick_w)

        # Dial numbers 0-9 inside the tick marks
        num_r = radius - tick_len - S * 0.09
        for digit in range(10):
            a = math.radians((digit / 10.0) * 360.0 - 90)
            nx = cx + num_r * math.cos(a)
            ny = cy + num_r * math.sin(a)
            text = str(digit)
            bbox = draw.textbbox((0, 0), text, font=self._num_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                (nx - tw / 2 - bbox[0], ny - th / 2 - bbox[1]),
                text, fill="black", font=self._num_font,
            )

        # Red pointer: rectangular base centered on dial + triangle to tip
        # Proportions from real photo (relative to S):
        #   base: 25% wide, 16.25% along pointer axis (centered on cx,cy)
        #   triangle: 41.25% from base front edge to tip
        fwd = math.radians(angle_deg - 90)
        perp = fwd + math.pi / 2

        base_hw = S * 0.125     # half-width of rectangular base
        base_hl = S * 0.08125   # half-length of base (along pointer)
        tri_len = S * 0.4125    # triangle from base front to tip

        # Base rectangle corners
        bf_x = cx + base_hl * math.cos(fwd)  # base front center
        bf_y = cy + base_hl * math.sin(fwd)
        bb_x = cx - base_hl * math.cos(fwd)  # base back center
        bb_y = cy - base_hl * math.sin(fwd)

        bl = (bb_x + base_hw * math.cos(perp), bb_y + base_hw * math.sin(perp))
        br = (bb_x - base_hw * math.cos(perp), bb_y - base_hw * math.sin(perp))
        fl = (bf_x + base_hw * math.cos(perp), bf_y + base_hw * math.sin(perp))
        fr = (bf_x - base_hw * math.cos(perp), bf_y - base_hw * math.sin(perp))

        # Triangle tip
        tip = (bf_x + tri_len * math.cos(fwd), bf_y + tri_len * math.sin(fwd))

        draw.polygon([bl, fl, tip, fr, br], fill=(200, 30, 30))

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
            arr_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            _, encoded = cv2.imencode(".jpg", arr_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            arr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

        result = Image.fromarray(arr)
        if result.size != original_size:
            result = result.resize(original_size, Image.Resampling.BILINEAR)
        return result


class _MultiCompositor:
    """Wraps multiple ArrowCompositors, randomly selecting one per render call."""

    def __init__(self, compositors, seed=42):
        self._compositors = compositors
        self._rng = random.Random(seed)

    def render(self, arrow_class: str, seed: Optional[int] = None) -> Image.Image:
        compositor = self._rng.choice(self._compositors)
        return compositor.render(arrow_class)


class SyntheticGenerator:
    """Orchestrates synthetic data generation."""

    def __init__(self, base_dir: str = "/training"):
        self.base_dir = Path(base_dir)

    def _get_digit_renderer(self):
        """Return photo-based compositor if background templates exist, else programmatic."""
        bg_dir = self.base_dir / "digits" / "reference" / "background"
        if bg_dir.exists() and (any(bg_dir.glob("*.jpg")) or any(bg_dir.glob("*.png"))):
            logger.info(f"Using photo-based digit compositor from {bg_dir}")
            return DigitCompositor(str(bg_dir))
        logger.info("No digit background templates found, using programmatic renderer")
        return DigitRenderer()

    def _get_arrow_renderer(self):
        """Return photo-based compositor(s) if reference images exist, else programmatic renderer."""
        ref_dir = self.base_dir / "arrows" / "reference"

        if ref_dir.exists():
            from .photo_master import build_arrow_compositor

            compositors = []
            for class_dir in sorted(ref_dir.iterdir()):
                if not class_dir.is_dir():
                    continue
                arrow_class = class_dir.name
                for photo_file in sorted(class_dir.iterdir()):
                    if photo_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        try:
                            comp = build_arrow_compositor(str(photo_file), arrow_class)
                            compositors.append(comp)
                            logger.info(f"Loaded reference photo: {photo_file.name} ({arrow_class})")
                        except Exception as e:
                            logger.warning(f"Failed to load reference {photo_file}: {e}")
                        break  # one photo per class is enough

            if compositors:
                logger.info(f"Using {len(compositors)} photo-based arrow compositor(s)")
                return _MultiCompositor(compositors)

        logger.info("No arrow reference images found, using programmatic renderer")
        return ArrowRenderer()

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
                renderer = self._get_digit_renderer()
                classes = DIGIT_CLASSES
                mode = "digit"
            else:
                renderer = self._get_arrow_renderer()
                classes = ARROW_CLASSES
                mode = "arrow"

            gt_dir = self.base_dir / t / "ground_truth"

            for cls in classes:
                class_dir = gt_dir / cls
                class_dir.mkdir(parents=True, exist_ok=True)

                for i in range(count_per_class):
                    img_seed = seed + _deterministic_seed(cls, i)
                    # Fresh render per variant for maximum diversity
                    master = renderer.render(cls, seed=img_seed)
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
