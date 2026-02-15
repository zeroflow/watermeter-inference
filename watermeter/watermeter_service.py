"""
Water Meter AI Service
Combines image fetching, OpenVINO inference, consistency checks, and MQTT publishing
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import base64

import cv2
import numpy as np
import httpx
import yaml
import paho.mqtt.client as mqtt
from .inference import get_inference_service
from .persistence import StateStore
from .image_hash import compute_dhash, HashCache

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class WatermeterService:
    """Main service for watermeter reading and inference."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the watermeter service."""
        # Load configuration
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # Update logging level from config
        log_level = getattr(logging, self.config["logging"]["level"])
        logging.getLogger().setLevel(log_level)

        # State management
        self.previous_value: Optional[float] = None
        self.last_update_time: Optional[datetime] = None
        self.ha_publish_enabled: bool = self.config["homeassistant"]["enabled"]
        self.current_state: Dict = {
            "total_value": None,
            "unit": "m³",
            "last_update": None,
            "status": "idle",
            "warnings": [],
            "predictions": [],
            "processing": False,
            "ha_publish_enabled": self.ha_publish_enabled,
            "leak_warning": False,
            "last_published_value": None,
            "last_published_timestamp": None,
            "last_rejected_value": None,
            "last_rejected_timestamp": None,
            "last_rejected_reasons": [],
        }

        # Persistence
        persistence_config = self.config.get("persistence", {})
        if persistence_config.get("enabled", False):
            self.state_store = StateStore(persistence_config["state_file"])
            # Load previous state
            self.previous_value, self.last_update_time = self.state_store.load()
            # Populate last_published from persisted state (BL-14)
            if self.previous_value is not None:
                self.current_state["last_published_value"] = self.previous_value
                self.current_state["last_published_timestamp"] = (
                    self.last_update_time.strftime("%H:%M") if self.last_update_time else None
                )
        else:
            self.state_store = None
            logger.info("Persistence disabled")

        # Low confidence rate limiting
        self.last_save_times: Dict[str, float] = {}

        # Rate history for plausibility checks (list of (value, timestamp) tuples)
        self.rate_history: List[Tuple[float, datetime]] = []
        self.rate_history_size = self.config["plausibility"].get("rate_history_size", 5)

        # Consecutive rejection tracking for stuck state detection
        self.consecutive_rejections = 0
        self.max_consecutive_rejections = 5  # Warn user after this many rejections

        # Leak detection state
        self.leak_warning: bool = False

        # Trigger mode
        trigger_config = self.config.get("trigger", {})
        self.trigger_mode = trigger_config.get("mode", "mqtt")
        self.cyclic_interval = trigger_config.get("cyclic_interval", 300)

        # Async lock for processing
        self.processing_lock = asyncio.Lock()

        # Event loop reference for MQTT callbacks
        self.loop = None

        # MQTT client
        self.mqtt_client = None

        # Cyclic loop task
        self._cyclic_task: Optional[asyncio.Task] = None

        # Cached marker templates for alignment (loaded lazily)
        self._marker_templates: Optional[List[np.ndarray]] = None

        # Confirmation state (BL-07)
        self._pending_confirmation: Optional[Dict] = None
        self._confirmation_timer: Optional[threading.Timer] = None

        logger.info(f"WatermeterService initialized (trigger_mode={self.trigger_mode})")

    async def fetch_images(self) -> Dict[str, Tuple[bytes, str]]:
        """
        Fetch all images from AI-on-the-edge device.

        Returns:
            Dict mapping ID to (image_bytes, image_class)
        """
        images = {}
        aiote_config = self.config["aiote"]
        base_url = f"http://{aiote_config['host']}{aiote_config['image_path']}"

        # Collect all IDs with their class
        all_ids = []
        for id_name in self.config["images"]["digits"]:
            all_ids.append((id_name, "digits"))
        for id_name in self.config["images"]["arrows"]:
            all_ids.append((id_name, "arrows"))

        logger.info(f"Fetching {len(all_ids)} images from AI-on-the-edge")

        async with httpx.AsyncClient(timeout=aiote_config["timeout"]) as client:
            for idx, (image_id, image_class) in enumerate(all_ids):
                # Rate limiting - delay between fetches
                if idx > 0:
                    await asyncio.sleep(aiote_config["fetch_delay"])

                url = f"{base_url}/{image_id}.jpg"
                try:
                    logger.debug(f"Fetching {url}")
                    response = await client.get(url)
                    response.raise_for_status()
                    images[image_id] = (response.content, image_class)
                    logger.debug(f"✓ Fetched {image_id} ({len(response.content)} bytes)")
                except httpx.HTTPError as e:
                    logger.error(f"Failed to fetch {image_id}: {e}")
                    # Continue with other images

        logger.info(f"Successfully fetched {len(images)}/{len(all_ids)} images")
        return images

    async def fetch_whole_image(self) -> Optional[bytes]:
        """
        Fetch the whole source image from AI-on-the-edge device.

        Returns:
            Image bytes or None on failure
        """
        aiote_config = self.config["aiote"]
        src_url = self.config["images"]["src"]

        logger.info(f"Fetching whole image from {src_url}")

        async with httpx.AsyncClient(timeout=aiote_config["timeout"]) as client:
            try:
                response = await client.get(src_url)
                response.raise_for_status()
                logger.info(f"Successfully fetched whole image ({len(response.content)} bytes)")
                return response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch whole image: {e}")
                return None

    def process_whole_image(self, image_bytes: bytes) -> Dict[str, Tuple[bytes, str]]:
        """
        Process whole image: apply rotation, marker alignment, and extract ROIs.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Dict mapping ID to (image_bytes, image_class) - same format as fetch_images
        """
        # Decode image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        height, width = img.shape[:2]

        detection = self.config.get("detection", {})

        # 1. Apply rotation if configured
        rotation = detection.get("rotation", 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))
            logger.debug(f"Applied rotation: {rotation}°")

        # 2. Marker-based alignment if markers are configured
        markers = detection.get("markers", [])
        if len(markers) >= 2:
            img = self._align_with_markers(img, markers)
            height, width = img.shape[:2]  # Update dimensions after alignment

        # 3. Extract ROIs
        images = {}

        # Extract digit ROIs - use detection config count, generate IDs
        digits_config = detection.get("digits", {})
        digit_rois = digits_config.get("rois", [])
        digit_count = digits_config.get("count", len(digit_rois))

        for i, roi in enumerate(digit_rois[:digit_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode(".jpg", roi_img)
            digit_id = f"digit_{i + 1}"
            images[digit_id] = (encoded.tobytes(), "digits")
            logger.debug(f"Extracted digit ROI: {digit_id}")

        # Extract analog ROIs - use detection config count, generate IDs
        analogs_config = detection.get("analogs", {})
        analog_rois = analogs_config.get("rois", [])
        analog_count = analogs_config.get("count", len(analog_rois))

        for i, roi in enumerate(analog_rois[:analog_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode(".jpg", roi_img)
            analog_id = f"analog_{i + 1}"
            images[analog_id] = (encoded.tobytes(), "arrows")
            logger.debug(f"Extracted analog ROI: {analog_id}")

        logger.info(f"Extracted {len(images)} ROIs from whole image")
        return images

    # Alignment constants (internal tuning, not user-facing)
    SEARCH_MARGIN = 0.15  # ±15% of image dimensions for search window
    CONFIDENCE_THRESHOLD = 0.5  # Minimum template match quality

    def _load_marker_templates(self, marker_count: int) -> Optional[List[np.ndarray]]:
        """
        Load marker template images from disk, with caching.

        Args:
            marker_count: Number of marker templates to load

        Returns:
            List of grayscale template images, or None if any are missing/unreadable
        """
        if self._marker_templates is not None:
            return self._marker_templates

        templates = []
        for i in range(1, marker_count + 1):
            path = Path(f"/data/marker_{i}.jpg")
            if not path.exists():
                logger.warning(f"Marker template not found: {path}")
                return None
            template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                logger.warning(f"Failed to read marker template: {path}")
                return None
            templates.append(template)

        self._marker_templates = templates
        logger.debug(f"Loaded {len(templates)} marker templates")
        return self._marker_templates

    def invalidate_marker_cache(self):
        """Clear cached marker templates so they are reloaded on next alignment."""
        self._marker_templates = None

    def _align_with_markers(self, img: np.ndarray, markers: List[Dict]) -> np.ndarray:
        """
        Align image using saved marker positions via template matching.

        Uses cv2.matchTemplate to locate each marker in the current image,
        then applies a similarity transform (rotation + uniform scale + translation)
        to correct for camera drift.

        Fail-open: returns original image unchanged on any failure.

        Args:
            img: Input image (BGR)
            markers: List of marker dicts with x, y, width, height (normalized 0-1)

        Returns:
            Aligned image, or original if alignment fails
        """
        height, width = img.shape[:2]

        if len(markers) < 2:
            logger.warning("Need at least 2 markers for alignment")
            return img

        # Load templates (cached after first call)
        templates = self._load_marker_templates(len(markers))
        if templates is None:
            return img

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        ref_centers = []
        found_centers = []

        for i, marker in enumerate(markers[:2]):
            template = templates[i]
            th, tw = template.shape[:2]

            # Reference center: where the marker should be
            ref_cx = (marker["x"] + marker["width"] / 2) * width
            ref_cy = (marker["y"] + marker["height"] / 2) * height
            ref_centers.append([ref_cx, ref_cy])

            # Search region: ±SEARCH_MARGIN around expected position
            margin_x = int(self.SEARCH_MARGIN * width)
            margin_y = int(self.SEARCH_MARGIN * height)

            sx1 = max(0, int(ref_cx - margin_x))
            sy1 = max(0, int(ref_cy - margin_y))
            sx2 = min(width, int(ref_cx + margin_x))
            sy2 = min(height, int(ref_cy + margin_y))

            # Search region must be larger than template
            if (sx2 - sx1) < tw or (sy2 - sy1) < th:
                logger.warning(f"Search region too small for marker {i+1}")
                return img

            search_region = gray[sy1:sy2, sx1:sx2]

            result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val < self.CONFIDENCE_THRESHOLD:
                logger.warning(f"Marker {i+1} match confidence too low: {max_val:.3f} < {self.CONFIDENCE_THRESHOLD}")
                return img

            # Convert match position back to full-image coordinates (center of matched region)
            found_cx = sx1 + max_loc[0] + tw / 2
            found_cy = sy1 + max_loc[1] + th / 2
            found_centers.append([found_cx, found_cy])

        ref_pts = np.float32(ref_centers).reshape(-1, 1, 2)
        found_pts = np.float32(found_centers).reshape(-1, 1, 2)

        transform, inliers = cv2.estimateAffinePartial2D(found_pts, ref_pts)
        if transform is None:
            logger.warning("Failed to estimate alignment transform")
            return img

        aligned = cv2.warpAffine(img, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
        logger.debug(
            f"Applied marker alignment (confidence: "
            f"{', '.join(f'{c:.3f}' for c in [ref_centers[0][0], ref_centers[1][0]])})"
        )
        return aligned

    def _extract_roi(self, img: np.ndarray, roi: Dict, width: int, height: int) -> np.ndarray:
        """
        Extract a region of interest from the image.

        Args:
            img: Source image
            roi: ROI dict with x, y, width, height (normalized 0-1)
            width: Image width
            height: Image height

        Returns:
            Cropped ROI image
        """
        # Convert normalized coordinates to pixels
        x = int(roi["x"] * width)
        y = int(roi["y"] * height)
        w = int(roi["width"] * width)
        h = int(roi["height"] * height)

        # Clamp to image bounds
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)

        # Extract ROI
        roi_img = img[y : y + h, x : x + w]

        return roi_img

    async def run_inference(self, images: Dict[str, Tuple[bytes, str]]) -> Dict[str, Dict]:
        """
        Run inference on all images.

        Args:
            images: Dict mapping ID to (image_bytes, image_class)

        Returns:
            Dict mapping ID to prediction result
        """
        predictions = {}

        logger.info(f"Running inference on {len(images)} images")

        # Create temporary directory for images
        import tempfile

        temp_dir = Path(tempfile.mkdtemp())

        try:
            for image_id, (image_bytes, image_class) in images.items():
                # Save image temporarily
                temp_path = temp_dir / f"{image_id}.jpg"
                temp_path.write_bytes(image_bytes)

                # Run prediction via inference service (supports hot-reload)
                try:
                    correction_config = self.config.get("correction", {})
                    if correction_config.get("enabled", False):
                        top_k_count = correction_config.get("top_k", 3)
                        top_k_results = get_inference_service().predict_detailed(
                            image_class, str(temp_path), top_k=top_k_count
                        )
                        result = top_k_results[0]
                        predictions[image_id] = {
                            "id": image_id,
                            "class": result["class"],
                            "confidence": result["confidence"],
                            "model": image_class,
                            "image_bytes": image_bytes,
                            "top_k": top_k_results,
                        }
                    else:
                        result = get_inference_service().predict(image_class, str(temp_path))
                        predictions[image_id] = {
                            "id": image_id,
                            "class": result["class"],
                            "confidence": result["confidence"],
                            "model": image_class,
                            "image_bytes": image_bytes,
                        }
                    logger.debug(f"{image_id}: {result['class']} ({result['confidence']:.3f})")
                except Exception as e:
                    logger.error(f"Inference failed for {image_id}: {e}")
                    predictions[image_id] = {
                        "id": image_id,
                        "class": "ERROR",
                        "confidence": 0.0,
                        "model": image_class,
                        "image_bytes": image_bytes,
                        "error": str(e),
                    }
        finally:
            # Cleanup temp files
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info(f"Inference completed for {len(predictions)} images")
        return predictions

    def calculate_total(self, predictions: Dict[str, Dict]) -> Tuple[float, Dict]:
        """
        Calculate total water meter reading from predictions.

        Args:
            predictions: Dict of prediction results

        Returns:
            (total_value, raw_values_dict)
        """
        # Determine digit and arrow IDs based on processing mode
        process_separate = self.config["images"].get("process_separate", False)

        if process_separate:
            # Use IDs from config arrays
            digit_ids = self.config["images"]["digits"]
            arrow_ids = self.config["images"]["arrows"]
        else:
            # Use generated IDs from detection config
            detection = self.config.get("detection", {})
            digit_count = detection.get("digits", {}).get("count", 0)
            analog_count = detection.get("analogs", {}).get("count", 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

        # Collect predictions in order
        digits = []
        arrows = []

        for image_id in digit_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] != "NAN" and pred["class"] != "ERROR":
                    digits.append(int(pred["class"]))
                else:
                    logger.warning(f"{image_id} has invalid class: {pred['class']}")
                    digits.append(0)  # Default to 0

        for image_id in arrow_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] != "ERROR":
                    arrows.append(float(pred["class"]))
                else:
                    logger.warning(f"{image_id} has error")
                    arrows.append(0.0)

        # Calculate total with dynamic multipliers based on count
        total = 0.0

        # Digits contribution: first digit has highest place value
        for i, digit in enumerate(digits):
            multiplier = 10 ** (len(digits) - 1 - i)
            total += digit * multiplier

        # Arrows contribution (use floor of value)
        for i, arrow in enumerate(arrows):
            multiplier = 10 ** (-(i + 1))  # 0.1, 0.01, 0.001, ...
            total += int(arrow) * multiplier

        raw_values = {"digits": digits, "arrows": arrows}

        logger.info(f"Calculated total: {total:.4f} m³")
        return total, raw_values

    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """
        Check consistency between adjacent positions.
        A position with .5 (half) should have next position >= 5.

        Args:
            predictions: Dict of prediction results

        Returns:
            List of warning messages
        """
        warnings = []

        # Check if consistency check is enabled
        if not self.config["plausibility"].get("enable_consistency_check", True):
            return warnings

        # Determine IDs based on processing mode
        process_separate = self.config["images"].get("process_separate", False)

        if process_separate:
            all_ids = self.config["images"]["digits"] + self.config["images"]["arrows"]
        else:
            detection = self.config.get("detection", {})
            digit_count = detection.get("digits", {}).get("count", 0)
            analog_count = detection.get("analogs", {}).get("count", 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            analog_ids = [f"analog_{i + 1}" for i in range(analog_count)]
            all_ids = digit_ids + analog_ids

        # Collect all values in order
        all_values = []

        for image_id in all_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] not in ["NAN", "ERROR"]:
                    if pred["model"] == "digits":
                        all_values.append((image_id, int(pred["class"])))
                    else:
                        all_values.append((image_id, float(pred["class"])))

        # Check consistency
        for i in range(len(all_values) - 1):
            current_id, current_val = all_values[i]
            next_id, next_val = all_values[i + 1]

            # Check if current has fractional part >= 0.4 (represents .5)
            current_frac = current_val % 1
            current_has_half = current_frac >= 0.4

            # Check if next is in upper half (>= 5)
            next_int_part = int(next_val)
            next_is_upper_half = next_int_part >= 5

            if current_has_half != next_is_upper_half:
                msg = f"{current_id}={current_val} (half={current_has_half}) vs {next_id}={next_val} (upper={next_is_upper_half})"
                warnings.append(msg)
                logger.warning(f"Consistency check: {msg}")

        return warnings

    def validate_plausibility(self, new_value: float) -> Tuple[bool, List[str]]:
        """
        Validate plausibility of new reading.

        Args:
            new_value: New meter reading

        Returns:
            (is_valid, warnings)
        """
        warnings = []
        config = self.config["plausibility"]

        # Check if we have a previous value
        if self.previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._add_to_rate_history(new_value)
            return True, warnings

        # Reverse detection
        if config["enable_reverse_detection"]:
            if new_value < self.previous_value:
                msg = f"Reverse detected: {self.previous_value:.4f} → {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - self.previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

            # Rate per hour - check against history if available
            if self.last_update_time:
                time_diff = (datetime.now() - self.last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config["max_rate_per_hour"]:
                        # If we have history, verify the rate is consistently high
                        if len(self.rate_history) >= 2:
                            avg_rate = self._calculate_average_rate_per_hour()
                            if avg_rate is not None and avg_rate > config["max_rate_per_hour"]:
                                msg = f"Rate per hour too high: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.error(msg)
                                return False, warnings
                            else:
                                # Single spike, warn but accept
                                msg = f"Rate spike: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            # No history yet, warn but accept
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        return True, warnings

    def _add_to_rate_history(self, value: float) -> None:
        """Add a reading to rate history."""
        self.rate_history.append((value, datetime.now()))
        # Keep only the last N readings
        if len(self.rate_history) > self.rate_history_size:
            self.rate_history = self.rate_history[-self.rate_history_size :]

    def _calculate_average_rate_per_hour(self) -> Optional[float]:
        """Calculate average rate per hour from history."""
        if len(self.rate_history) < 2:
            return None

        # Calculate rate from oldest to newest in history
        oldest_value, oldest_time = self.rate_history[0]
        newest_value, newest_time = self.rate_history[-1]

        time_diff = (newest_time - oldest_time).total_seconds()
        if time_diff <= 0:
            return None

        value_diff = newest_value - oldest_value
        rate_per_hour = (value_diff / time_diff) * 3600
        return rate_per_hour

    def _check_sustained_consumption(self) -> Optional[str]:
        """
        Check if the last N consecutive readings all show rate above threshold.

        Returns:
            Warning message string if leak detected, None otherwise.
        """
        config = self.config["plausibility"]

        if not config.get("enable_leak_detection", True):
            return None

        threshold = config.get("sustained_rate_threshold", 0.05)
        min_readings = config.get("sustained_rate_readings", 3)

        if len(self.rate_history) < min_readings + 1:
            return None

        tail = self.rate_history[-(min_readings + 1) :]

        for i in range(len(tail) - 1):
            val_prev, ts_prev = tail[i]
            val_curr, ts_curr = tail[i + 1]

            time_diff_s = (ts_curr - ts_prev).total_seconds()
            if time_diff_s <= 0:
                return None

            rate_per_hour = ((val_curr - val_prev) / time_diff_s) * 3600

            if rate_per_hour < threshold:
                return None

        total_time_s = (tail[-1][1] - tail[0][1]).total_seconds()
        total_time_min = total_time_s / 60
        avg_rate = ((tail[-1][0] - tail[0][0]) / total_time_s) * 3600

        return (
            f"Sustained consumption: {avg_rate:.3f} m\u00b3/h over {total_time_min:.0f} min "
            f"({min_readings} consecutive readings above {threshold} m\u00b3/h)"
        )

    # ── User Confirmation (BL-07) ───────────────────────────────────────

    def _get_confirmation_config(self) -> Dict:
        """Return the confirmation config with defaults applied."""
        defaults = {
            "enabled": False,
            "request_topic": "watermeter/confirmation_request",
            "response_topic": "watermeter/confirmation_response",
            "timeout_minutes": 5,
            "min_warnings": 1,
            "min_low_confidence_positions": 2,
            "max_rate_jump_factor": 3.0,
        }
        user = self.config.get("confirmation", {})
        return {**defaults, **user}

    def _should_request_confirmation(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict]
    ) -> Optional[str]:
        """
        Decide whether a reading needs user confirmation.

        Returns a reason string if confirmation is needed, None otherwise.
        """
        conf = self._get_confirmation_config()
        if not conf["enabled"]:
            return None

        # Don't stack confirmations -- if one is already pending, skip
        if self._pending_confirmation is not None:
            return None

        # Condition 1: reading has enough warnings
        if len(warnings) >= conf["min_warnings"]:
            return f"{len(warnings)} warning(s) on this reading"

        # Condition 2: enough positions below confidence threshold
        threshold = self.config["inference"]["confidence_threshold"]
        low_conf_count = sum(1 for pred in predictions.values() if pred["confidence"] < threshold)
        if low_conf_count >= conf["min_low_confidence_positions"]:
            return f"{low_conf_count} position(s) below confidence threshold"

        # Condition 3: rate jump relative to average
        if self.previous_value is not None and self.last_update_time is not None:
            avg_rate = self._calculate_average_rate_per_hour()
            if avg_rate is not None and avg_rate > 0:
                time_diff = (datetime.now() - self.last_update_time).total_seconds()
                if time_diff > 0:
                    current_rate = ((total_value - self.previous_value) / time_diff) * 3600
                    if current_rate > avg_rate * conf["max_rate_jump_factor"]:
                        return (
                            f"Rate jump: {current_rate:.3f} m\u00b3/h "
                            f"exceeds {conf['max_rate_jump_factor']}x average ({avg_rate:.3f})"
                        )

        return None

    def _publish_confirmation_request(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict], reason: str
    ) -> None:
        """
        Publish a confirmation request to MQTT and start the timeout timer.

        Stores the reading details in _pending_confirmation so they can be
        committed or discarded when the user responds.
        """
        conf = self._get_confirmation_config()

        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT not connected -- cannot request confirmation, auto-accepting reading")
            # Clear pending state since we can't actually request confirmation
            self._pending_confirmation = None
            self.current_state["status"] = "warning" if warnings else "ok"
            return

        # Build compact per-position confidence map
        confidences = {
            pred["id"]: {
                "class": pred["class"],
                "confidence": round(pred["confidence"], 3),
            }
            for pred in predictions.values()
        }

        payload = {
            "value": round(total_value, 4),
            "reason": reason,
            "warnings": warnings,
            "positions": confidences,
            "timestamp": datetime.now().isoformat(),
        }

        # NOTE: _pending_confirmation is set by the caller (process_reading)
        # with the correct pre-reading snapshots. Do NOT overwrite it here.

        self.mqtt_client.publish(
            conf["request_topic"],
            json.dumps(payload),
            qos=1,
        )
        logger.info(f"Published confirmation request: {reason}")

        # Start timeout timer
        self._cancel_confirmation_timer()
        timeout_seconds = conf["timeout_minutes"] * 60
        self._confirmation_timer = threading.Timer(timeout_seconds, self._confirmation_timeout)
        self._confirmation_timer.daemon = True
        self._confirmation_timer.start()
        logger.info(f"Confirmation timeout set: {conf['timeout_minutes']} min")

    def _cancel_confirmation_timer(self) -> None:
        """Cancel the running confirmation timeout timer, if any."""
        if self._confirmation_timer is not None:
            self._confirmation_timer.cancel()
            self._confirmation_timer = None

    def _confirmation_timeout(self) -> None:
        """Called by Timer thread -- routes to event loop for thread safety."""
        if self.loop:
            self.loop.call_soon_threadsafe(self._do_confirmation_timeout)
        else:
            self._do_confirmation_timeout()

    def _do_confirmation_timeout(self) -> None:
        """Actually process the timeout. Runs on the event loop thread."""
        pending = self._pending_confirmation
        if pending is None:
            return

        logger.warning("Confirmation timeout -- auto-rejecting reading")

        # Revert state to before the pending reading was applied
        self.previous_value = pending["previous_value_before"]
        self.last_update_time = pending["last_update_time_before"]

        # Persist the reverted state
        if self.state_store:
            if self.previous_value is not None and self.last_update_time is not None:
                self.state_store.save(self.previous_value, self.last_update_time)
            else:
                self.state_store.clear()

        # Remove the last entry from rate_history (it was added optimistically)
        if self.rate_history:
            self.rate_history.pop()

        self._pending_confirmation = None
        self._confirmation_timer = None
        self.current_state["status"] = "timeout"
        self.current_state["warnings"] = ["Confirmation timed out -- reading discarded"]
        logger.info("Pending confirmation cleared after timeout")

    def _handle_confirmation_response(self, payload: str) -> None:
        """
        Process a user response from the confirmation response MQTT topic.

        Payloads:
            - "confirm"        -- accept the pending reading as-is
            - "reject"         -- discard the pending reading
            - "correct:{value}" -- accept with an overridden value
        """
        pending = self._pending_confirmation
        if pending is None:
            logger.warning("Received confirmation response but nothing is pending -- ignoring")
            return

        self._cancel_confirmation_timer()
        payload = payload.strip()

        if payload == "confirm":
            logger.info("User confirmed the reading")
            # Reading was already optimistically applied -- just publish to HA
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.publish_to_mqtt(
                        pending["value"],
                        pending["warnings"],
                        pending["predictions"],
                        leak_warning=self.leak_warning,
                    ),
                    self.loop,
                )
            self.current_state["status"] = "ok"
            self.current_state["warnings"] = pending["warnings"]

        elif payload == "reject":
            logger.info("User rejected the reading")
            # Revert to pre-reading state
            self.previous_value = pending["previous_value_before"]
            self.last_update_time = pending["last_update_time_before"]
            if self.state_store:
                if self.previous_value is not None and self.last_update_time is not None:
                    self.state_store.save(self.previous_value, self.last_update_time)
                else:
                    self.state_store.clear()
            # Remove the optimistic rate_history entry
            if self.rate_history:
                self.rate_history.pop()
            self.current_state["status"] = "rejected"
            self.current_state["warnings"] = ["Reading rejected by user"]

        elif payload.startswith("correct:"):
            corrected_str = payload[len("correct:") :]
            try:
                corrected_value = float(corrected_str)
            except ValueError:
                logger.error(f"Invalid corrected value: {corrected_str!r}")
                return

            logger.info(f"User corrected reading: {pending['value']:.4f} -> {corrected_value:.4f}")

            # Apply the corrected value
            self.previous_value = corrected_value
            self.last_update_time = datetime.now()
            if self.state_store:
                self.state_store.save(self.previous_value, self.last_update_time)

            # Fix up rate_history: replace the last (optimistic) entry
            if self.rate_history:
                self.rate_history[-1] = (corrected_value, self.last_update_time)

            self.current_state["total_value"] = corrected_value
            self.current_state["last_update"] = self.last_update_time.isoformat()
            self.current_state["status"] = "ok"
            self.current_state["warnings"] = [f"User corrected: {pending['value']:.4f} -> {corrected_value:.4f}"]

            # Publish the corrected value to HA
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.publish_to_mqtt(
                        corrected_value,
                        self.current_state["warnings"],
                        pending["predictions"],
                        leak_warning=self.leak_warning,
                    ),
                    self.loop,
                )
        else:
            logger.warning(f"Unknown confirmation response: {payload!r}")
            return

        self._pending_confirmation = None

    def get_confirmation_status(self) -> Optional[Dict]:
        """
        Return details of the pending confirmation, or None if nothing is pending.

        Used by the /api/confirmation/status endpoint.
        """
        pending = self._pending_confirmation
        if pending is None:
            return None

        conf = self._get_confirmation_config()
        elapsed = (datetime.now() - pending["timestamp"]).total_seconds()
        timeout_seconds = conf["timeout_minutes"] * 60

        return {
            "value": round(pending["value"], 4),
            "reason": pending["reason"],
            "warnings": pending["warnings"],
            "timestamp": pending["timestamp"].isoformat(),
            "timeout_minutes": conf["timeout_minutes"],
            "seconds_remaining": max(0, int(timeout_seconds - elapsed)),
        }

    # ── Value Correction Engine (BL-04) ─────────────────────────────────

    def _get_ordered_position_ids(self) -> List[str]:
        """Return position IDs in order: digit_1, ..., analog_1, ... (most to least significant)."""
        process_separate = self.config["images"].get("process_separate", False)
        if process_separate:
            digit_ids = self.config["images"]["digits"]
            arrow_ids = self.config["images"]["arrows"]
        else:
            detection = self.config.get("detection", {})
            digit_count = detection.get("digits", {}).get("count", 0)
            analog_count = detection.get("analogs", {}).get("count", 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]
        return digit_ids + arrow_ids

    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
        """Estimate plausible range for next reading based on rate history."""
        if self.previous_value is None or len(self.rate_history) < 3:
            return None
        avg_rate = self._calculate_average_rate_per_hour()
        if avg_rate is None or avg_rate <= 0:
            return None
        hours_elapsed = 0.0
        if self.last_update_time:
            hours_elapsed = (datetime.now() - self.last_update_time).total_seconds() / 3600
        if hours_elapsed <= 0:
            return None
        expected_delta = avg_rate * hours_elapsed
        config = self.config.get("correction", {})
        tolerance = config.get("rate_tolerance_factor", 3.0)
        min_expected = self.previous_value
        max_expected = self.previous_value + expected_delta * tolerance
        return (min_expected, max_expected)

    def _recalculate_with_replacement(
        self, predictions: Dict[str, Dict], replace_id: str, replace_class: str, raw_values: Dict
    ) -> float:
        """Calculate hypothetical total with one position replaced."""
        process_separate = self.config["images"].get("process_separate", False)
        if process_separate:
            digit_ids = self.config["images"]["digits"]
            arrow_ids = self.config["images"]["arrows"]
        else:
            detection = self.config.get("detection", {})
            digit_count = detection.get("digits", {}).get("count", 0)
            analog_count = detection.get("analogs", {}).get("count", 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

        digits = []
        for image_id in digit_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls not in ("NAN", "ERROR"):
                    digits.append(int(cls))
                else:
                    digits.append(0)

        arrows = []
        for image_id in arrow_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls != "ERROR":
                    arrows.append(float(cls))
                else:
                    arrows.append(0.0)

        total = 0.0
        for i, digit in enumerate(digits):
            total += digit * 10 ** (len(digits) - 1 - i)
        for i, arrow in enumerate(arrows):
            total += int(arrow) * 10 ** (-(i + 1))
        return total

    def _check_consistency_improvement(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """Check if replacing a position fixes a consistency violation with adjacent positions."""
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False
        idx = position_ids.index(replace_id)

        def get_value(pid, override_id=None, override_class=None):
            if pid not in predictions:
                return None
            cls = override_class if pid == override_id else predictions[pid]["class"]
            if cls in ("NAN", "ERROR"):
                return None
            return float(cls) if predictions[pid]["model"] == "arrows" else int(cls)

        def has_violation(val_a, val_b):
            """Check half/upper consistency between adjacent positions."""
            frac = val_a % 1
            has_half = frac >= 0.4
            upper = int(val_b) >= 5
            return has_half != upper

        current_violations = 0
        replacement_violations = 0

        # Check pair with previous position (idx-1, idx)
        if idx > 0:
            prev_id = position_ids[idx - 1]
            prev_val = get_value(prev_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if prev_val is not None and curr_val is not None:
                if has_violation(prev_val, curr_val):
                    current_violations += 1
                if alt_val is not None and has_violation(prev_val, alt_val):
                    replacement_violations += 1

        # Check pair with next position (idx, idx+1)
        if idx < len(position_ids) - 1:
            next_id = position_ids[idx + 1]
            next_val = get_value(next_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if next_val is not None and curr_val is not None:
                if has_violation(curr_val, next_val):
                    current_violations += 1
                if alt_val is not None and has_violation(alt_val, next_val):
                    replacement_violations += 1

        return current_violations > 0 and replacement_violations < current_violations

    def _check_cross_arrow_consistency(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """
        Check if replacing an arrow position improves cross-arrow consistency
        with the adjacent more-significant arrow.

        Only applies to arrow-arrow pairs. Uses the constraining arrow's
        confidence as a gate: only fires when the constraining arrow is confident.
        """
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False

        idx = position_ids.index(replace_id)
        if idx == 0:
            return False

        prev_id = position_ids[idx - 1]
        if prev_id not in predictions:
            return False

        # Both must be arrows
        if predictions[prev_id]["model"] != "arrows" or predictions[replace_id]["model"] != "arrows":
            return False

        # Confidence gate
        config = self.config.get("correction", {})
        confidence_gate = config.get("cross_arrow_confidence_gate", 0.8)
        if predictions[prev_id]["confidence"] < confidence_gate:
            return False

        prev_class = predictions[prev_id]["class"]
        if prev_class in ("NAN", "ERROR"):
            return False

        curr_class = predictions[replace_id]["class"]
        if curr_class in ("NAN", "ERROR"):
            return False

        # Determine expected half for the less-significant arrow based on
        # the constraining arrow's fractional position.
        # frac < 0.5 → needle in lower part of dial → next dial in lower half (0-4)
        # frac >= 0.5 → needle in upper part → next dial in upper half (5-9)
        prev_value = float(prev_class)
        prev_frac = prev_value - int(prev_value)
        expected_lower_half = prev_frac < 0.5

        curr_int = int(float(curr_class))
        alt_int = int(float(replace_class))

        curr_in_expected = (curr_int < 5) == expected_lower_half
        alt_in_expected = (alt_int < 5) == expected_lower_half

        return (not curr_in_expected) and alt_in_expected

    def correct_predictions(self, predictions: Dict[str, Dict], raw_total: float, raw_values: Dict) -> List[str]:
        """
        Correct low-confidence predictions using contextual signals.
        Modifies predictions dict in-place. Returns correction warning strings.
        """
        config = self.config.get("correction", {})
        if not config.get("enabled", False):
            return []

        correction_threshold = config.get("confidence_threshold", 0.7)
        min_signal_agreement = config.get("min_signal_agreement", 2)
        min_alternative_confidence = config.get("min_alternative_confidence", 0.05)
        max_corrections = config.get("max_corrections_per_reading", 2)
        corrections = []

        position_ids = self._get_ordered_position_ids()

        # Safety: if all positions are high-confidence, don't touch anything
        all_confident = all(
            predictions[pid]["confidence"] >= correction_threshold for pid in position_ids if pid in predictions
        )
        if all_confident:
            return []

        expected_range = self._estimate_expected_range()

        # Meter rollover guard
        skip_previous_value_signal = False
        if self.previous_value is not None:
            digit_ids = [pid for pid in position_ids if pid.startswith("digit_")]
            if digit_ids:
                digit_count = len(digit_ids)
                max_meter = 10**digit_count
                all_near_zero = all(
                    int(predictions[pid]["class"]) <= 1
                    for pid in digit_ids
                    if pid in predictions and predictions[pid]["class"] not in ("NAN", "ERROR")
                )
                if all_near_zero and self.previous_value > 0.9 * max_meter:
                    skip_previous_value_signal = True

        for pid in position_ids:
            if len(corrections) >= max_corrections:
                break
            if pid not in predictions:
                continue

            pred = predictions[pid]
            if pred["confidence"] >= correction_threshold:
                continue

            top_k = pred.get("top_k", [])
            if not top_k or len(top_k) < 2:
                continue

            alternatives = [
                alt for alt in top_k[1:] if alt["confidence"] >= min_alternative_confidence and alt["class"] != "NAN"
            ]
            if not alternatives:
                continue

            best_alt = None
            best_score = 0

            for alt in alternatives:
                score = 0
                total_with_alt = self._recalculate_with_replacement(predictions, pid, alt["class"], raw_values)

                # Signal 1: Previous value constraint
                if not skip_previous_value_signal and self.previous_value is not None:
                    if raw_total < self.previous_value and total_with_alt >= self.previous_value:
                        score += 1

                # Signal 2: Expected rate
                if expected_range is not None:
                    min_exp, max_exp = expected_range
                    if (raw_total < min_exp or raw_total > max_exp) and min_exp <= total_with_alt <= max_exp:
                        score += 1

                # Signal 3: Adjacent position consistency
                if self._check_consistency_improvement(predictions, pid, alt["class"]):
                    score += 1

                # Signal 4: Cross-arrow consistency (BL-05)
                if self._check_cross_arrow_consistency(predictions, pid, alt["class"]):
                    score += 1

                if score > best_score:
                    best_score = score
                    best_alt = alt

            if best_alt is not None and best_score >= min_signal_agreement:
                old_class = pred["class"]
                old_conf = pred["confidence"]
                pred["class"] = best_alt["class"]
                pred["confidence"] = best_alt["confidence"]
                pred["corrected_from"] = old_class
                pred["corrected_confidence"] = old_conf
                pred["correction_signals"] = best_score

                msg = (
                    f"Corrected {pid}: {old_class}\u2192{best_alt['class']} "
                    f"(conf={old_conf:.2f}\u2192{best_alt['confidence']:.2f}, "
                    f"signals={best_score}/{min_signal_agreement})"
                )
                corrections.append(msg)
                logger.info(msg)

        return corrections

    async def save_low_confidence(
        self, image_id: str, image_bytes: bytes, prediction: Dict, next_image_bytes: bytes = None
    ) -> None:
        """
        Save low confidence images for later training.

        Args:
            image_id: Image identifier
            image_bytes: Image data
            prediction: Prediction result
            next_image_bytes: Optional image of the next smaller dial (for annotation help)
        """
        config = self.config["low_confidence"]

        if not config["save_enabled"]:
            return

        # Check rate limiting
        now = time.time()
        last_save = self.last_save_times.get(image_id, 0)
        if now - last_save < config["save_rate_limit"]:
            logger.debug(f"Rate limit: skipping save for {image_id}")
            return

        # Generate timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        # Determine save path - save to input folder for manual review
        image_class = prediction["model"]
        base_path = Path(config["save_path"])
        save_dir = base_path / image_class / "input"
        save_dir.mkdir(parents=True, exist_ok=True)

        # Deduplication check
        new_hash = None
        input_cache = None
        dedup_enabled = config.get("dedup_enabled", True)
        if dedup_enabled:
            new_hash = compute_dhash(image_bytes)
            if new_hash is not None:
                threshold = config.get("dedup_threshold", 10)

                # Check against input folder
                input_cache = HashCache(save_dir)
                dup = input_cache.find_near_duplicate(new_hash, threshold)
                if dup:
                    logger.debug(f"Dedup: skipping {image_id}, near-duplicate of {dup} in input")
                    return

                # Check against ground truth if configured
                scope = config.get("dedup_scope", "input+ground_truth")
                if scope == "input+ground_truth":
                    gt_base = base_path / image_class / "ground_truth"
                    if gt_base.is_dir():
                        for class_dir in gt_base.iterdir():
                            if class_dir.is_dir():
                                gt_cache = HashCache(class_dir)
                                dup = gt_cache.find_near_duplicate(new_hash, threshold)
                                if dup:
                                    logger.debug(
                                        f"Dedup: skipping {image_id}, near-duplicate of "
                                        f"{dup} in ground_truth/{class_dir.name}"
                                    )
                                    return

        save_path = save_dir / f"{image_id}_{timestamp}.jpg"

        # Save image
        save_path.write_bytes(image_bytes)
        logger.info(f"Saved low confidence image: {save_path}")

        # Update input hash cache
        if dedup_enabled and new_hash is not None:
            try:
                if input_cache is None:
                    input_cache = HashCache(save_dir)
                input_cache.add(save_path.name, new_hash)
            except Exception as e:
                logger.warning(f"Failed to update hash cache: {e}")

        # Save next image if provided (helps with annotation)
        if next_image_bytes:
            next_save_path = save_dir / f"{image_id}_{timestamp}_next.jpg"
            next_save_path.write_bytes(next_image_bytes)
            logger.info(f"Saved next dial reference image: {next_save_path}")

        # Update rate limit
        self.last_save_times[image_id] = now

    async def process_reading(self) -> Dict:
        """
        Main processing workflow:
        1. Fetch images
        2. Run inference
        3. Calculate value
        4. Consistency check
        5. Plausibility check
        6. Handle low confidence
        7. Update state

        Returns:
            Current state dict
        """
        async with self.processing_lock:
            # If a confirmation is pending, skip processing new readings
            if self._pending_confirmation is not None:
                logger.info("Skipping reading -- confirmation pending for previous reading")
                return self.current_state

            try:
                self.current_state["processing"] = True
                self.current_state["warnings"] = []
                self.current_state["status"] = "processing"

                logger.info("=" * 60)
                logger.info("Starting new water meter reading")
                logger.info("=" * 60)

                # 1. Fetch images (separate or whole image mode)
                process_separate = self.config["images"].get("process_separate", False)

                if process_separate:
                    # Fetch individual images from AI-on-the-edge
                    images = await self.fetch_images()
                    if not images:
                        raise Exception("No images fetched")
                else:
                    # Fetch whole image and extract ROIs
                    whole_image = await self.fetch_whole_image()
                    if not whole_image:
                        raise Exception("Failed to fetch whole image")
                    images = self.process_whole_image(whole_image)
                    if not images:
                        raise Exception("No ROIs extracted from whole image")

                # 2. Run inference
                predictions = await self.run_inference(images)

                # 3. Calculate total
                total_value, raw_values = self.calculate_total(predictions)

                # 3b. Value correction (BL-04)
                correction_warnings = self.correct_predictions(predictions, total_value, raw_values)
                if correction_warnings:
                    total_value, raw_values = self.calculate_total(predictions)
                    logger.info(f"Recalculated total after {len(correction_warnings)} correction(s): {total_value:.4f}")

                # 4. Consistency check
                consistency_warnings = self.check_consistency(predictions)

                # 5. Plausibility check
                is_valid, plausibility_warnings = self.validate_plausibility(total_value)

                all_warnings = correction_warnings + consistency_warnings + plausibility_warnings

                # Leak detection (sustained consumption check)
                leak_msg = self._check_sustained_consumption()
                self.leak_warning = leak_msg is not None
                self.current_state["leak_warning"] = self.leak_warning
                if leak_msg:
                    all_warnings.append(leak_msg)
                    logger.warning(leak_msg)

                # 6. Handle low confidence images
                threshold = self.config["inference"]["confidence_threshold"]
                low_conf_config = self.config["low_confidence"]

                # Get arrow IDs list for finding "next" arrow
                process_separate = self.config["images"].get("process_separate", False)
                if process_separate:
                    arrow_ids = self.config["images"]["arrows"]
                else:
                    detection = self.config.get("detection", {})
                    analog_count = detection.get("analogs", {}).get("count", 0)
                    arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

                for pred in predictions.values():
                    if pred["confidence"] < threshold:
                        logger.warning(f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']:.3f})")

                        # For arrows, try to get the next smaller dial's image
                        next_image_bytes = None
                        if pred["model"] == "arrows" and pred["id"] in arrow_ids:
                            idx = arrow_ids.index(pred["id"])
                            if idx + 1 < len(arrow_ids):
                                next_arrow_id = arrow_ids[idx + 1]
                                if next_arrow_id in predictions:
                                    next_image_bytes = predictions[next_arrow_id]["image_bytes"]
                                    logger.debug(f"Including next dial {next_arrow_id} for annotation help")

                        # Save low confidence images if enabled
                        await self.save_low_confidence(pred["id"], pred["image_bytes"], pred, next_image_bytes)
                        # Add warning if enabled
                        if low_conf_config.get("warn_enabled", True):
                            all_warnings.append(
                                f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']*100:.1f}%)"
                            )

                # 7. Update state
                # Always store predictions (even on error) so images are displayed
                self.current_state["predictions"] = [
                    {
                        "id": pred["id"],
                        "class": pred["class"],
                        "confidence": pred["confidence"],
                        "model": pred["model"],
                        "image_base64": base64.b64encode(pred["image_bytes"]).decode("utf-8"),
                        # Include correction metadata if present
                        **(
                            {
                                "corrected_from": pred["corrected_from"],
                                "corrected_confidence": pred["corrected_confidence"],
                                "correction_signals": pred["correction_signals"],
                            }
                            if "corrected_from" in pred
                            else {}
                        ),
                    }
                    for pred in predictions.values()
                ]

                if is_valid:
                    # Snapshot state before applying (needed for confirmation revert)
                    prev_value_before = self.previous_value
                    prev_time_before = self.last_update_time

                    self.previous_value = total_value
                    self.last_update_time = datetime.now()
                    self.consecutive_rejections = 0  # Reset rejection counter

                    # Add to rate history for plausibility checks
                    self._add_to_rate_history(total_value)

                    # Persist state to disk
                    if self.state_store:
                        self.state_store.save(self.previous_value, self.last_update_time)

                    self.current_state["total_value"] = total_value
                    self.current_state["last_update"] = self.last_update_time.isoformat()
                    self.current_state["status"] = "warning" if all_warnings else "ok"
                    self.current_state["warnings"] = all_warnings

                    # Track last-published / clear last-rejected (BL-14)
                    self.current_state["last_published_value"] = total_value
                    self.current_state["last_published_timestamp"] = self.last_update_time.strftime("%H:%M")
                    self.current_state["last_rejected_value"] = None
                    self.current_state["last_rejected_timestamp"] = None
                    self.current_state["last_rejected_reasons"] = []

                    logger.info(f"✓ Reading accepted: {total_value:.4f} m³")

                    # Check if user confirmation is needed (BL-07)
                    confirmation_reason = self._should_request_confirmation(
                        total_value,
                        all_warnings,
                        predictions,
                    )
                    if confirmation_reason:
                        # Hold HA publish -- store snapshots for revert on reject/timeout
                        self._pending_confirmation = {
                            "value": total_value,
                            "warnings": all_warnings,
                            "predictions": predictions,
                            "reason": confirmation_reason,
                            "timestamp": datetime.now(),
                            "previous_value_before": prev_value_before,
                            "last_update_time_before": prev_time_before,
                        }
                        self._publish_confirmation_request(
                            total_value,
                            all_warnings,
                            predictions,
                            confirmation_reason,
                        )
                        self.current_state["status"] = "pending_confirmation"
                        logger.info(f"Reading held for confirmation: {confirmation_reason}")
                    else:
                        # Normal path -- publish immediately
                        await self.publish_to_mqtt(
                            total_value, all_warnings, predictions, leak_warning=self.leak_warning
                        )
                else:
                    self.consecutive_rejections += 1
                    self.current_state["status"] = "error"
                    self.current_state["warnings"] = all_warnings
                    self.current_state["total_value"] = total_value

                    # Track last-rejected state (BL-14)
                    self.current_state["last_rejected_value"] = total_value
                    self.current_state["last_rejected_timestamp"] = datetime.now().strftime("%H:%M")
                    self.current_state["last_rejected_reasons"] = plausibility_warnings

                    logger.error(
                        f"✗ Reading rejected: {total_value:.4f} m³ (consecutive: {self.consecutive_rejections})"
                    )

                    # Check for stuck state
                    if self.consecutive_rejections >= self.max_consecutive_rejections:
                        stuck_msg = (
                            f"STUCK: {self.consecutive_rejections} consecutive rejections. "
                            f"Previous value: {self.previous_value:.4f}, Current: {total_value:.4f}. "
                            f"Consider using /reset if previous value is incorrect."
                        )
                        all_warnings.append(stuck_msg)
                        self.current_state["warnings"] = all_warnings
                        logger.error(stuck_msg)

                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error during processing: {e}", exc_info=True)
                self.current_state["status"] = "error"
                self.current_state["warnings"] = [str(e)]
                # Try to save predictions if we got that far
                try:
                    if "predictions" in locals() and predictions:
                        self.current_state["predictions"] = [
                            {
                                "id": pred["id"],
                                "class": pred["class"],
                                "confidence": pred["confidence"],
                                "model": pred["model"],
                                "image_base64": base64.b64encode(pred["image_bytes"]).decode("utf-8"),
                            }
                            for pred in predictions.values()
                        ]
                except Exception as pred_err:
                    logger.error(f"Could not save predictions after error: {pred_err}")
            finally:
                self.current_state["processing"] = False

        return self.current_state

    async def publish_to_mqtt(
        self, value: float, warnings: List[str], predictions: Dict, *, leak_warning: bool = False
    ) -> None:
        """
        Publish reading to Home Assistant via MQTT.

        Args:
            value: Meter reading value
            warnings: List of warnings
            predictions: Prediction results
        """
        if not self.ha_publish_enabled:
            logger.info("HA publishing disabled - skipping MQTT publish")
            return

        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping publish")
            return

        ha_config = self.config["homeassistant"]

        # Build payload
        payload = {
            "state": round(value, 4),
            "attributes": {
                "last_update": datetime.now().isoformat(),
                "warnings": warnings,
                "leak_warning": leak_warning,
                "confidences": {pred["id"]: round(pred["confidence"] * 100, 1) for pred in predictions.values()},
            },
        }

        # Publish
        topic = ha_config["publish_topic"]
        self.mqtt_client.publish(topic, json.dumps(payload), qos=2, retain=True)
        logger.info(f"Published to MQTT: {topic}")

    def reset_previous_value(self) -> None:
        """Reset the previous value (for meter replacement or stuck state)."""
        logger.info("Resetting previous value, rate history, and rejection counter")
        self.previous_value = None
        self.last_update_time = None
        self.rate_history = []
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False

        # Clear last-published and last-rejected tracking (BL-14)
        self.current_state["last_published_value"] = None
        self.current_state["last_published_timestamp"] = None
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []

        # Clear any pending confirmation (BL-07)
        self._cancel_confirmation_timer()
        self._pending_confirmation = None

        # Clear persisted state
        if self.state_store:
            self.state_store.clear()

    def set_manual_value(self, value: float) -> bool:
        """Manually set the meter value (BL-15).

        Updates all internal state as if a reading was accepted, seeds rate
        history so the next automated reading doesn't trigger a false spike,
        and publishes the value to MQTT.

        Returns:
            True if value was published to MQTT, False otherwise
        """
        logger.info(f"Manual meter set: {value:.4f} m³")

        now = datetime.now()

        # Update core state
        self.previous_value = value
        self.last_update_time = now

        # Clear and re-seed rate history
        self.rate_history = [(value, now)]

        # Reset plausibility tracking
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False

        # Cancel any pending confirmation (BL-07)
        self._cancel_confirmation_timer()
        self._pending_confirmation = None

        # Persist state
        if self.state_store:
            self.state_store.save(self.previous_value, self.last_update_time)

        # Update current_state for UI
        self.current_state["total_value"] = value
        self.current_state["last_update"] = now.isoformat()
        self.current_state["status"] = "ok"
        self.current_state["warnings"] = []

        # Update last-published / clear last-rejected (BL-14)
        self.current_state["last_published_value"] = value
        self.current_state["last_published_timestamp"] = now.strftime("%H:%M")
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []

        # Publish to MQTT
        mqtt_published = False
        if self.ha_publish_enabled:
            if self.mqtt_client and self.mqtt_client.is_connected():
                try:
                    ha_config = self.config["homeassistant"]
                    topic = ha_config.get("publish_topic", "homeassistant/sensor/watermeter/state")
                    payload = json.dumps(
                        {
                            "state": round(value, 4),
                            "attributes": {
                                "last_update": now.isoformat(),
                                "warnings": ["Manual input"],
                                "leak_warning": False,
                                "confidences": {},
                                "source": "manual",
                            },
                        }
                    )
                    self.mqtt_client.publish(topic, payload, qos=2, retain=True)
                    mqtt_published = True
                    logger.info(f"Published manual value {value:.4f} to MQTT: {topic}")
                except Exception as e:
                    logger.error(f"Failed to publish manual value to MQTT: {e}")
            else:
                logger.warning("MQTT not connected, manual value not published to Home Assistant")

        return mqtt_published

    def toggle_ha_publish(self, enabled: bool) -> None:
        """Toggle Home Assistant MQTT publishing."""
        self.ha_publish_enabled = enabled
        self.current_state["ha_publish_enabled"] = enabled
        logger.info(f"Home Assistant publishing {'enabled' if enabled else 'disabled'}")

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT Discovery message."""
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping discovery")
            return

        if not self.ha_publish_enabled:
            logger.warning("Home Assistant publishing not enabled - skipping discovery")
            return

        ha_config = self.config["homeassistant"]

        # Discovery topic: <discovery_prefix>/<component>/<node_id>/<object_id>/config
        discovery_topic = f"{ha_config['discovery_prefix']}/sensor/watermeter_ai/watermeter_usage/config"

        # Discovery payload
        discovery_payload = {
            "name": ha_config["sensor"]["name"],
            "state_topic": ha_config["publish_topic"],
            "unit_of_measurement": ha_config["sensor"]["unit"],
            "device_class": ha_config["sensor"]["device_class"],
            "state_class": ha_config["sensor"]["state_class"],
            "icon": ha_config["sensor"]["icon"],
            "unique_id": "watermeter_ai_usage",
            "value_template": "{{ value_json.state }}",
            "json_attributes_topic": ha_config["publish_topic"],
            "device": {
                "identifiers": ["watermeter_ai"],
                "name": ha_config["device"]["name"],
                "manufacturer": ha_config["device"]["manufacturer"],
                "model": ha_config["device"]["model"],
            },
        }

        # Publish with retain=True so HA finds it after restart
        self.mqtt_client.publish(discovery_topic, json.dumps(discovery_payload), qos=1, retain=True)
        logger.info(f"Published MQTT Discovery to {discovery_topic}")

    # MQTT Callbacks
    def on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connect callback."""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            mqtt_config = self.config["mqtt"]

            # Subscribe to trigger topic only in mqtt/both mode
            if self.trigger_mode in ("mqtt", "both"):
                client.subscribe(mqtt_config["trigger_topic"], qos=2)
                logger.info(f"Subscribed to {mqtt_config['trigger_topic']}")
            else:
                logger.info("Cyclic-only mode — skipping trigger topic subscription")

            # Always subscribe to reset and HA status
            client.subscribe(mqtt_config["reset_topic"], qos=2)
            client.subscribe("homeassistant/status", qos=1)
            logger.info(f"Subscribed to {mqtt_config['reset_topic']}")
            logger.info("Subscribed to homeassistant/status")

            # Subscribe to confirmation response topic if enabled (BL-07)
            conf_config = self._get_confirmation_config()
            if conf_config["enabled"]:
                client.subscribe(conf_config["response_topic"], qos=2)
                logger.info(f"Subscribed to {conf_config['response_topic']}")

            # Publish discovery on connect
            self.publish_discovery()
        else:
            logger.error(f"MQTT connection failed with code {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback."""
        mqtt_config = self.config["mqtt"]
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        logger.info(f"MQTT message: {topic} = {payload}")

        if topic == mqtt_config["trigger_topic"]:
            if payload == mqtt_config["trigger_payload"]:
                logger.info("Trigger received - starting processing")
                # Start processing in background from MQTT thread
                if self.loop:
                    asyncio.run_coroutine_threadsafe(self.process_reading(), self.loop)
                else:
                    logger.error("Event loop not available - cannot process reading")

        elif topic == mqtt_config["reset_topic"]:
            # Route through event loop to avoid mutating shared state from MQTT thread
            if self.loop:
                self.loop.call_soon_threadsafe(self.reset_previous_value)
            else:
                self.reset_previous_value()

        elif topic == self._get_confirmation_config()["response_topic"]:
            # Route through event loop to keep state mutations on the main thread
            if self.loop:
                self.loop.call_soon_threadsafe(self._handle_confirmation_response, payload)
            else:
                self._handle_confirmation_response(payload)

        elif topic == "homeassistant/status":
            if payload == "online":
                logger.info("Home Assistant came online - republishing discovery")
                self.publish_discovery()

    def start_mqtt(self):
        """Initialize and start MQTT client.

        Connects to the MQTT broker. If the broker is unreachable the app
        continues without MQTT — paho's background loop will keep retrying.
        """
        # Capture the event loop for MQTT callbacks
        try:
            self.loop = asyncio.get_running_loop()
            logger.info("Event loop captured for MQTT callbacks")
        except RuntimeError:
            logger.warning("No running event loop - MQTT triggers may not work")

        mqtt_config = self.config["mqtt"]

        self.mqtt_client = mqtt.Client(client_id=mqtt_config["client_id"])
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

        logger.info(f"Connecting to MQTT broker {mqtt_config['broker']}:{mqtt_config['port']}")
        try:
            self.mqtt_client.connect(mqtt_config["broker"], mqtt_config["port"], mqtt_config["keepalive"])
        except (OSError, ConnectionRefusedError) as exc:
            logger.warning(
                f"MQTT broker not reachable ({exc}). " f"App continues without MQTT — will reconnect automatically."
            )

        # Start loop in background thread (handles reconnect automatically)
        self.mqtt_client.loop_start()
        logger.info("MQTT client started")

    def stop_mqtt(self):
        """Stop MQTT client."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("MQTT client stopped")

    async def _cyclic_loop(self):
        """Periodically trigger process_reading at the configured interval."""
        logger.info(f"Cyclic trigger loop started (interval={self.cyclic_interval}s)")
        try:
            while True:
                await asyncio.sleep(self.cyclic_interval)
                logger.info("Cyclic trigger — starting reading")
                await self.process_reading()
        except asyncio.CancelledError:
            logger.info("Cyclic trigger loop cancelled")

    def start_cyclic_loop(self):
        """Start the cyclic trigger background task."""
        if self._cyclic_task is not None:
            logger.warning("Cyclic loop already running")
            return
        self._cyclic_task = asyncio.create_task(self._cyclic_loop())
        logger.info("Cyclic trigger loop task created")

    def stop_cyclic_loop(self):
        """Cancel the cyclic trigger background task."""
        if self._cyclic_task is not None:
            self._cyclic_task.cancel()
            self._cyclic_task = None
            logger.info("Cyclic trigger loop stopped")


# Global service instance
service: Optional[WatermeterService] = None


def get_service() -> WatermeterService:
    """Get or create the global service instance."""
    global service
    if service is None:
        service = WatermeterService()
    return service
