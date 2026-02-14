"""
Water Meter AI Service
Combines image fetching, OpenVINO inference, consistency checks, and MQTT publishing
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import base64
import io

import cv2
import numpy as np
import httpx
import yaml
import paho.mqtt.client as mqtt
from .inference import get_inference_service
from .persistence import StateStore
from .image_hash import compute_dhash, HashCache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WatermeterService:
    """Main service for watermeter reading and inference."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the watermeter service."""
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # Update logging level from config
        log_level = getattr(logging, self.config['logging']['level'])
        logging.getLogger().setLevel(log_level)

        # State management
        self.previous_value: Optional[float] = None
        self.last_update_time: Optional[datetime] = None
        self.ha_publish_enabled: bool = self.config['homeassistant']['enabled']
        self.current_state: Dict = {
            'total_value': None,
            'unit': 'm³',
            'last_update': None,
            'status': 'idle',
            'warnings': [],
            'predictions': [],
            'processing': False,
            'ha_publish_enabled': self.ha_publish_enabled
        }

        # Persistence
        persistence_config = self.config.get('persistence', {})
        if persistence_config.get('enabled', False):
            self.state_store = StateStore(persistence_config['state_file'])
            # Load previous state
            self.previous_value, self.last_update_time = self.state_store.load()
        else:
            self.state_store = None
            logger.info("Persistence disabled")

        # Low confidence rate limiting
        self.last_save_times: Dict[str, float] = {}

        # Rate history for plausibility checks (list of (value, timestamp) tuples)
        self.rate_history: List[Tuple[float, datetime]] = []
        self.rate_history_size = self.config['plausibility'].get('rate_history_size', 5)

        # Consecutive rejection tracking for stuck state detection
        self.consecutive_rejections = 0
        self.max_consecutive_rejections = 5  # Warn user after this many rejections

        # Trigger mode
        trigger_config = self.config.get('trigger', {})
        self.trigger_mode = trigger_config.get('mode', 'mqtt')
        self.cyclic_interval = trigger_config.get('cyclic_interval', 300)

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

        logger.info(f"WatermeterService initialized (trigger_mode={self.trigger_mode})")

    async def fetch_images(self) -> Dict[str, Tuple[bytes, str]]:
        """
        Fetch all images from AI-on-the-edge device.

        Returns:
            Dict mapping ID to (image_bytes, image_class)
        """
        images = {}
        aiote_config = self.config['aiote']
        base_url = f"http://{aiote_config['host']}{aiote_config['image_path']}"

        # Collect all IDs with their class
        all_ids = []
        for id_name in self.config['images']['digits']:
            all_ids.append((id_name, 'digits'))
        for id_name in self.config['images']['arrows']:
            all_ids.append((id_name, 'arrows'))

        logger.info(f"Fetching {len(all_ids)} images from AI-on-the-edge")

        async with httpx.AsyncClient(timeout=aiote_config['timeout']) as client:
            for idx, (image_id, image_class) in enumerate(all_ids):
                # Rate limiting - delay between fetches
                if idx > 0:
                    await asyncio.sleep(aiote_config['fetch_delay'])

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
        aiote_config = self.config['aiote']
        src_url = self.config['images']['src']

        logger.info(f"Fetching whole image from {src_url}")

        async with httpx.AsyncClient(timeout=aiote_config['timeout']) as client:
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

        detection = self.config.get('detection', {})

        # 1. Apply rotation if configured
        rotation = detection.get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))
            logger.debug(f"Applied rotation: {rotation}°")

        # 2. Marker-based alignment if markers are configured
        markers = detection.get('markers', [])
        if len(markers) >= 2:
            img = self._align_with_markers(img, markers)
            height, width = img.shape[:2]  # Update dimensions after alignment

        # 3. Extract ROIs
        images = {}

        # Extract digit ROIs - use detection config count, generate IDs
        digits_config = detection.get('digits', {})
        digit_rois = digits_config.get('rois', [])
        digit_count = digits_config.get('count', len(digit_rois))

        for i, roi in enumerate(digit_rois[:digit_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode('.jpg', roi_img)
            digit_id = f"digit_{i + 1}"
            images[digit_id] = (encoded.tobytes(), 'digits')
            logger.debug(f"Extracted digit ROI: {digit_id}")

        # Extract analog ROIs - use detection config count, generate IDs
        analogs_config = detection.get('analogs', {})
        analog_rois = analogs_config.get('rois', [])
        analog_count = analogs_config.get('count', len(analog_rois))

        for i, roi in enumerate(analog_rois[:analog_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode('.jpg', roi_img)
            analog_id = f"analog_{i + 1}"
            images[analog_id] = (encoded.tobytes(), 'arrows')
            logger.debug(f"Extracted analog ROI: {analog_id}")

        logger.info(f"Extracted {len(images)} ROIs from whole image")
        return images

    # Alignment constants (internal tuning, not user-facing)
    SEARCH_MARGIN = 0.15      # ±15% of image dimensions for search window
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
            path = Path(f'/data/marker_{i}.jpg')
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
            ref_cx = (marker['x'] + marker['width'] / 2) * width
            ref_cy = (marker['y'] + marker['height'] / 2) * height
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
                logger.warning(
                    f"Marker {i+1} match confidence too low: {max_val:.3f} < {self.CONFIDENCE_THRESHOLD}"
                )
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
        x = int(roi['x'] * width)
        y = int(roi['y'] * height)
        w = int(roi['width'] * width)
        h = int(roi['height'] * height)

        # Clamp to image bounds
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)

        # Extract ROI
        roi_img = img[y:y+h, x:x+w]

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
                    result = get_inference_service().predict(image_class, str(temp_path))
                    predictions[image_id] = {
                        'id': image_id,
                        'class': result['class'],
                        'confidence': result['confidence'],
                        'model': image_class,
                        'image_bytes': image_bytes
                    }
                    logger.debug(f"{image_id}: {result['class']} ({result['confidence']:.3f})")
                except Exception as e:
                    logger.error(f"Inference failed for {image_id}: {e}")
                    predictions[image_id] = {
                        'id': image_id,
                        'class': 'ERROR',
                        'confidence': 0.0,
                        'model': image_class,
                        'image_bytes': image_bytes,
                        'error': str(e)
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
        process_separate = self.config['images'].get('process_separate', False)

        if process_separate:
            # Use IDs from config arrays
            digit_ids = self.config['images']['digits']
            arrow_ids = self.config['images']['arrows']
        else:
            # Use generated IDs from detection config
            detection = self.config.get('detection', {})
            digit_count = detection.get('digits', {}).get('count', 0)
            analog_count = detection.get('analogs', {}).get('count', 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

        # Collect predictions in order
        digits = []
        arrows = []

        for image_id in digit_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred['class'] != 'NAN' and pred['class'] != 'ERROR':
                    digits.append(int(pred['class']))
                else:
                    logger.warning(f"{image_id} has invalid class: {pred['class']}")
                    digits.append(0)  # Default to 0

        for image_id in arrow_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred['class'] != 'ERROR':
                    arrows.append(float(pred['class']))
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

        raw_values = {
            'digits': digits,
            'arrows': arrows
        }

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
        if not self.config['plausibility'].get('enable_consistency_check', True):
            return warnings

        # Determine IDs based on processing mode
        process_separate = self.config['images'].get('process_separate', False)

        if process_separate:
            all_ids = self.config['images']['digits'] + self.config['images']['arrows']
        else:
            detection = self.config.get('detection', {})
            digit_count = detection.get('digits', {}).get('count', 0)
            analog_count = detection.get('analogs', {}).get('count', 0)
            digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
            analog_ids = [f"analog_{i + 1}" for i in range(analog_count)]
            all_ids = digit_ids + analog_ids

        # Collect all values in order
        all_values = []

        for image_id in all_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred['class'] not in ['NAN', 'ERROR']:
                    if pred['model'] == 'digits':
                        all_values.append((image_id, int(pred['class'])))
                    else:
                        all_values.append((image_id, float(pred['class'])))

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
        config = self.config['plausibility']

        # Check if we have a previous value
        if self.previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._add_to_rate_history(new_value)
            return True, warnings

        # Reverse detection
        if config['enable_reverse_detection']:
            if new_value < self.previous_value:
                msg = f"Reverse detected: {self.previous_value:.4f} → {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config['enable_rate_limit']:
            value_diff = new_value - self.previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config['max_rate_per_reading']:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

            # Rate per hour - check against history if available
            if self.last_update_time:
                time_diff = (datetime.now() - self.last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config['max_rate_per_hour']:
                        # If we have history, verify the rate is consistently high
                        if len(self.rate_history) >= 2:
                            avg_rate = self._calculate_average_rate_per_hour()
                            if avg_rate is not None and avg_rate > config['max_rate_per_hour']:
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
            self.rate_history = self.rate_history[-self.rate_history_size:]

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

    async def save_low_confidence(self, image_id: str, image_bytes: bytes,
                                  prediction: Dict,
                                  next_image_bytes: bytes = None) -> None:
        """
        Save low confidence images for later training.

        Args:
            image_id: Image identifier
            image_bytes: Image data
            prediction: Prediction result
            next_image_bytes: Optional image of the next smaller dial (for annotation help)
        """
        config = self.config['low_confidence']

        if not config['save_enabled']:
            return

        # Check rate limiting
        now = time.time()
        last_save = self.last_save_times.get(image_id, 0)
        if now - last_save < config['save_rate_limit']:
            logger.debug(f"Rate limit: skipping save for {image_id}")
            return

        # Generate timestamp
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

        # Determine save path - save to input folder for manual review
        image_class = prediction['model']
        base_path = Path(config['save_path'])
        save_dir = base_path / image_class / 'input'
        save_dir.mkdir(parents=True, exist_ok=True)

        # Deduplication check
        new_hash = None
        input_cache = None
        dedup_enabled = config.get('dedup_enabled', True)
        if dedup_enabled:
            new_hash = compute_dhash(image_bytes)
            if new_hash is not None:
                threshold = config.get('dedup_threshold', 10)

                # Check against input folder
                input_cache = HashCache(save_dir)
                dup = input_cache.find_near_duplicate(new_hash, threshold)
                if dup:
                    logger.debug(f"Dedup: skipping {image_id}, near-duplicate of {dup} in input")
                    return

                # Check against ground truth if configured
                scope = config.get('dedup_scope', 'input+ground_truth')
                if scope == 'input+ground_truth':
                    gt_base = base_path / image_class / 'ground_truth'
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
            try:
                self.current_state['processing'] = True
                self.current_state['warnings'] = []
                self.current_state['status'] = 'processing'

                logger.info("=" * 60)
                logger.info("Starting new water meter reading")
                logger.info("=" * 60)

                # 1. Fetch images (separate or whole image mode)
                process_separate = self.config['images'].get('process_separate', False)

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

                # 4. Consistency check
                consistency_warnings = self.check_consistency(predictions)

                # 5. Plausibility check
                is_valid, plausibility_warnings = self.validate_plausibility(total_value)

                all_warnings = consistency_warnings + plausibility_warnings

                # 6. Handle low confidence images
                threshold = self.config['inference']['confidence_threshold']
                low_conf_config = self.config['low_confidence']

                # Get arrow IDs list for finding "next" arrow
                process_separate = self.config['images'].get('process_separate', False)
                if process_separate:
                    arrow_ids = self.config['images']['arrows']
                else:
                    detection = self.config.get('detection', {})
                    analog_count = detection.get('analogs', {}).get('count', 0)
                    arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

                for pred in predictions.values():
                    if pred['confidence'] < threshold:
                        logger.warning(f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']:.3f})")

                        # For arrows, try to get the next smaller dial's image
                        next_image_bytes = None
                        if pred['model'] == 'arrows' and pred['id'] in arrow_ids:
                            idx = arrow_ids.index(pred['id'])
                            if idx + 1 < len(arrow_ids):
                                next_arrow_id = arrow_ids[idx + 1]
                                if next_arrow_id in predictions:
                                    next_image_bytes = predictions[next_arrow_id]['image_bytes']
                                    logger.debug(f"Including next dial {next_arrow_id} for annotation help")

                        # Save low confidence images if enabled
                        await self.save_low_confidence(
                            pred['id'],
                            pred['image_bytes'],
                            pred,
                            next_image_bytes
                        )
                        # Add warning if enabled
                        if low_conf_config.get('warn_enabled', True):
                            all_warnings.append(
                                f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']*100:.1f}%)"
                            )

                # 7. Update state
                # Always store predictions (even on error) so images are displayed
                self.current_state['predictions'] = [
                    {
                        'id': pred['id'],
                        'class': pred['class'],
                        'confidence': pred['confidence'],
                        'model': pred['model'],
                        'image_base64': base64.b64encode(pred['image_bytes']).decode('utf-8')
                    }
                    for pred in predictions.values()
                ]

                if is_valid:
                    self.previous_value = total_value
                    self.last_update_time = datetime.now()
                    self.consecutive_rejections = 0  # Reset rejection counter

                    # Add to rate history for plausibility checks
                    self._add_to_rate_history(total_value)

                    # Persist state to disk
                    if self.state_store:
                        self.state_store.save(self.previous_value, self.last_update_time)

                    self.current_state['total_value'] = total_value
                    self.current_state['last_update'] = self.last_update_time.isoformat()
                    self.current_state['status'] = 'warning' if all_warnings else 'ok'
                    self.current_state['warnings'] = all_warnings

                    logger.info(f"✓ Reading accepted: {total_value:.4f} m³")

                    # Publish to MQTT
                    await self.publish_to_mqtt(total_value, all_warnings, predictions)
                else:
                    self.consecutive_rejections += 1
                    self.current_state['status'] = 'error'
                    self.current_state['warnings'] = all_warnings
                    self.current_state['total_value'] = total_value
                    logger.error(f"✗ Reading rejected: {total_value:.4f} m³ (consecutive: {self.consecutive_rejections})")

                    # Check for stuck state
                    if self.consecutive_rejections >= self.max_consecutive_rejections:
                        stuck_msg = (
                            f"STUCK: {self.consecutive_rejections} consecutive rejections. "
                            f"Previous value: {self.previous_value:.4f}, Current: {total_value:.4f}. "
                            f"Consider using /reset if previous value is incorrect."
                        )
                        all_warnings.append(stuck_msg)
                        self.current_state['warnings'] = all_warnings
                        logger.error(stuck_msg)

                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error during processing: {e}", exc_info=True)
                self.current_state['status'] = 'error'
                self.current_state['warnings'] = [str(e)]
                # Try to save predictions if we got that far
                try:
                    if 'predictions' in locals() and predictions:
                        self.current_state['predictions'] = [
                            {
                                'id': pred['id'],
                                'class': pred['class'],
                                'confidence': pred['confidence'],
                                'model': pred['model'],
                                'image_base64': base64.b64encode(pred['image_bytes']).decode('utf-8')
                            }
                            for pred in predictions.values()
                        ]
                except Exception as pred_err:
                    logger.error(f"Could not save predictions after error: {pred_err}")
            finally:
                self.current_state['processing'] = False

        return self.current_state

    async def publish_to_mqtt(self, value: float, warnings: List[str],
                             predictions: Dict) -> None:
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

        ha_config = self.config['homeassistant']

        # Build payload
        payload = {
            'state': round(value, 4),
            'attributes': {
                'last_update': datetime.now().isoformat(),
                'warnings': warnings,
                'confidences': {
                    pred['id']: round(pred['confidence'] * 100, 1)
                    for pred in predictions.values()
                }
            }
        }

        # Publish
        topic = ha_config['publish_topic']
        self.mqtt_client.publish(
            topic,
            json.dumps(payload),
            qos=2,
            retain=True
        )
        logger.info(f"Published to MQTT: {topic}")

    def reset_previous_value(self) -> None:
        """Reset the previous value (for meter replacement or stuck state)."""
        logger.info("Resetting previous value, rate history, and rejection counter")
        self.previous_value = None
        self.last_update_time = None
        self.rate_history = []
        self.consecutive_rejections = 0

        # Clear persisted state
        if self.state_store:
            self.state_store.clear()

    def toggle_ha_publish(self, enabled: bool) -> None:
        """Toggle Home Assistant MQTT publishing."""
        self.ha_publish_enabled = enabled
        self.current_state['ha_publish_enabled'] = enabled
        logger.info(f"Home Assistant publishing {'enabled' if enabled else 'disabled'}")

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT Discovery message."""
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping discovery")
            return
        
        if not self.ha_publish_enabled:
            logger.warning("Home Assistant publishing not enabled - skipping discovery")
            return

        ha_config = self.config['homeassistant']

        # Discovery topic: <discovery_prefix>/<component>/<node_id>/<object_id>/config
        discovery_topic = f"{ha_config['discovery_prefix']}/sensor/watermeter_ai/watermeter_usage/config"

        # Discovery payload
        discovery_payload = {
            "name": ha_config['sensor']['name'],
            "state_topic": ha_config['publish_topic'],
            "unit_of_measurement": ha_config['sensor']['unit'],
            "device_class": ha_config['sensor']['device_class'],
            "state_class": ha_config['sensor']['state_class'],
            "icon": ha_config['sensor']['icon'],
            "unique_id": "watermeter_ai_usage",
            "value_template": "{{ value_json.state }}",
            "json_attributes_topic": ha_config['publish_topic'],
            "device": {
                "identifiers": ["watermeter_ai"],
                "name": ha_config['device']['name'],
                "manufacturer": ha_config['device']['manufacturer'],
                "model": ha_config['device']['model']
            }
        }

        # Publish with retain=True so HA finds it after restart
        self.mqtt_client.publish(
            discovery_topic,
            json.dumps(discovery_payload),
            qos=1,
            retain=True
        )
        logger.info(f"Published MQTT Discovery to {discovery_topic}")

    # MQTT Callbacks
    def on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connect callback."""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            mqtt_config = self.config['mqtt']

            # Subscribe to trigger topic only in mqtt/both mode
            if self.trigger_mode in ('mqtt', 'both'):
                client.subscribe(mqtt_config['trigger_topic'], qos=2)
                logger.info(f"Subscribed to {mqtt_config['trigger_topic']}")
            else:
                logger.info("Cyclic-only mode — skipping trigger topic subscription")

            # Always subscribe to reset and HA status
            client.subscribe(mqtt_config['reset_topic'], qos=2)
            client.subscribe("homeassistant/status", qos=1)
            logger.info(f"Subscribed to {mqtt_config['reset_topic']}")
            logger.info("Subscribed to homeassistant/status")

            # Publish discovery on connect
            self.publish_discovery()
        else:
            logger.error(f"MQTT connection failed with code {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback."""
        mqtt_config = self.config['mqtt']
        topic = msg.topic
        payload = msg.payload.decode('utf-8')

        logger.info(f"MQTT message: {topic} = {payload}")

        if topic == mqtt_config['trigger_topic']:
            if payload == mqtt_config['trigger_payload']:
                logger.info("Trigger received - starting processing")
                # Start processing in background from MQTT thread
                if self.loop:
                    asyncio.run_coroutine_threadsafe(self.process_reading(), self.loop)
                else:
                    logger.error("Event loop not available - cannot process reading")

        elif topic == mqtt_config['reset_topic']:
            # Route through event loop to avoid mutating shared state from MQTT thread
            if self.loop:
                self.loop.call_soon_threadsafe(self.reset_previous_value)
            else:
                self.reset_previous_value()

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

        mqtt_config = self.config['mqtt']

        self.mqtt_client = mqtt.Client(client_id=mqtt_config['client_id'])
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

        logger.info(f"Connecting to MQTT broker {mqtt_config['broker']}:{mqtt_config['port']}")
        try:
            self.mqtt_client.connect(
                mqtt_config['broker'],
                mqtt_config['port'],
                mqtt_config['keepalive']
            )
        except (OSError, ConnectionRefusedError) as exc:
            logger.warning(
                f"MQTT broker not reachable ({exc}). "
                f"App continues without MQTT — will reconnect automatically."
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
