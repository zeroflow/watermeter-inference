"""Photo-based master image generation for arrows.

Extracts the red pointer from a real arrow photo, inpaints the background,
and provides compositing utilities to place a pointer at any angle.
"""

import logging
from typing import Optional

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def extract_pointer_mask(img: Image.Image) -> np.ndarray:
    """Extract a binary mask of the red pointer from an arrow photo.

    Uses HSV color thresholding to isolate red regions.
    Returns a uint8 mask (0 or 255).
    """
    arr = np.array(img)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

    # Red wraps around hue=0, so use two ranges
    lower_red1 = np.array([0, 50, 50])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 50, 50])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    # Morphological cleanup: close small gaps, remove tiny noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    return mask


def inpaint_background(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Remove the pointer from the image by inpainting the masked region.

    Returns a clean dial background with the pointer region filled in.
    """
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    # Dilate mask slightly to cover edge artifacts
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    dilated = cv2.dilate(mask, kernel, iterations=1)

    inpainted = cv2.inpaint(arr, dilated, inpaintRadius=5, flags=cv2.INPAINT_TELEA)
    inpainted_rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)

    return Image.fromarray(inpainted_rgb)


def extract_pointer_template(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Extract the pointer as an RGBA image (transparent background).

    The pointer pixels are preserved; everything else becomes transparent.
    """
    arr = np.array(img)
    rgba = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    rgba[:, :, :3] = arr
    rgba[:, :, 3] = mask  # alpha = mask

    return Image.fromarray(rgba, "RGBA")


class ArrowCompositor:
    """Composites a pointer template onto a clean dial background at any angle."""

    def __init__(
        self,
        background: Image.Image,
        pointer_template: Image.Image,
        source_angle_deg: float,
    ):
        """
        Args:
            background: Clean dial background (RGB, pointer inpainted out).
            pointer_template: Pointer as RGBA (transparent background).
            source_angle_deg: The angle (in dial degrees) of the pointer in the
                original photo. Class 0.0 = 0°, 5.0 = 180°, 9.9 = 356.4°.
        """
        self._bg = background.copy()
        self._template = pointer_template.copy()
        self._source_angle = source_angle_deg

    def render(self, arrow_class: str, seed: Optional[int] = None) -> Image.Image:
        """Render the dial at a specific arrow class position.

        Interface matches ArrowRenderer.render() so it can be used as a
        drop-in replacement in SyntheticGenerator.
        """
        target_value = float(arrow_class)
        target_angle = (target_value / 10.0) * 360.0
        rotation = target_angle - self._source_angle

        # Rotate template around center
        # PIL rotates counter-clockwise, dial angles are clockwise
        rotated = self._template.rotate(
            -rotation, resample=Image.Resampling.BICUBIC, center=None
        )

        # Composite onto background
        result = self._bg.copy()
        result.paste(rotated, (0, 0), rotated)  # use alpha as mask
        return result


def build_arrow_compositor(
    photo_path: str, arrow_class: str, target_size: int = 224
) -> ArrowCompositor:
    """Build an ArrowCompositor from a real arrow photo.

    Args:
        photo_path: Path to a real arrow photo.
        arrow_class: The dial position in the photo (e.g. "9.0").
        target_size: Output image size (square).

    Returns:
        ArrowCompositor ready to render all 100 classes.
    """
    img = Image.open(photo_path).convert("RGB")

    # Resize to target size (square)
    img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

    # Extract pointer
    mask = extract_pointer_mask(img)
    bg = inpaint_background(img, mask)
    template = extract_pointer_template(img, mask)

    source_angle = (float(arrow_class) / 10.0) * 360.0
    logger.info(f"Built ArrowCompositor from {photo_path} at class {arrow_class} ({source_angle}°)")

    return ArrowCompositor(bg, template, source_angle)
