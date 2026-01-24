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

import httpx
import yaml
import paho.mqtt.client as mqtt
from inference import digits_classifier, arrows_classifier
from persistence import StateStore

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

        # Async lock for processing
        self.processing_lock = asyncio.Lock()

        # Event loop reference for MQTT callbacks
        self.loop = None

        # MQTT client
        self.mqtt_client = None

        logger.info("WatermeterService initialized")

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

                # Select classifier
                classifier = digits_classifier if image_class == 'digits' else arrows_classifier

                # Run prediction
                try:
                    result = classifier.predict(str(temp_path))
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
        # Sort predictions by ID to ensure correct order
        digits = []
        arrows = []

        for image_id in self.config['images']['digits']:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred['class'] != 'NAN' and pred['class'] != 'ERROR':
                    digits.append(int(pred['class']))
                else:
                    logger.warning(f"{image_id} has invalid class: {pred['class']}")
                    digits.append(0)  # Default to 0

        for image_id in self.config['images']['arrows']:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred['class'] != 'ERROR':
                    arrows.append(float(pred['class']))
                else:
                    logger.warning(f"{image_id} has error")
                    arrows.append(0.0)

        # Calculate total: dig1*100 + dig2*10 + dig3*1 + floor(ana1)*0.1 + ...
        total = 0.0

        # Digits contribution
        multipliers = [100, 10, 1]
        for i, digit in enumerate(digits[:3]):  # Max 3 digits
            total += digit * multipliers[i]

        # Arrows contribution (use floor of value)
        arrow_multipliers = [0.1, 0.01, 0.001, 0.0001]
        for i, arrow in enumerate(arrows[:4]):  # Max 4 arrows
            total += int(arrow) * arrow_multipliers[i]

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

        # Collect all values in order
        all_ids = self.config['images']['digits'] + self.config['images']['arrows']
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
            return True, warnings

        # Reverse detection
        if config['enable_reverse_detection']:
            if new_value < self.previous_value:
                msg = f"Reverse detected: {self.previous_value:.4f} → {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config['enable_rate_limit'] and self.last_update_time:
            time_diff = (datetime.now() - self.last_update_time).total_seconds()
            value_diff = new_value - self.previous_value

            if time_diff > 0:
                # Rate per hour
                rate_per_hour = (value_diff / time_diff) * 3600
                if rate_per_hour > config['max_rate_per_hour']:
                    msg = f"Rate too high: {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                    warnings.append(msg)
                    logger.warning(msg)

                # Rate per minute
                rate_per_minute = (value_diff / time_diff) * 60
                if rate_per_minute > config['max_rate_per_minute']:
                    msg = f"Rate too high: {rate_per_minute:.4f} m³/min (max: {config['max_rate_per_minute']})"
                    warnings.append(msg)
                    logger.warning(msg)

        return True, warnings

    async def save_low_confidence(self, image_id: str, image_bytes: bytes,
                                  prediction: Dict) -> None:
        """
        Save low confidence images for later training.

        Args:
            image_id: Image identifier
            image_bytes: Image data
            prediction: Prediction result
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

        # Determine save path
        image_class = prediction['model']
        save_dir = Path(config['save_path']) / image_class
        save_dir.mkdir(parents=True, exist_ok=True)

        save_path = save_dir / f"{image_id}_{timestamp}.jpg"

        # Save image
        save_path.write_bytes(image_bytes)
        logger.info(f"Saved low confidence image: {save_path}")

        # Update rate limit
        self.last_save_times[image_id] = now

        # TODO: Label Studio sync if enabled
        if config['label_studio_enabled']:
            logger.info("Label Studio sync not yet implemented")

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

                # 1. Fetch images
                images = await self.fetch_images()
                if not images:
                    raise Exception("No images fetched")

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
                for pred in predictions.values():
                    if pred['confidence'] < threshold:
                        logger.warning(f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']:.3f})")
                        await self.save_low_confidence(
                            pred['id'],
                            pred['image_bytes'],
                            pred
                        )
                        all_warnings.append(
                            f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']*100:.1f}%)"
                        )

                # 7. Update state
                if is_valid:
                    self.previous_value = total_value
                    self.last_update_time = datetime.now()

                    # Persist state to disk
                    if self.state_store:
                        self.state_store.save(self.previous_value, self.last_update_time)

                    self.current_state['total_value'] = total_value
                    self.current_state['last_update'] = self.last_update_time.isoformat()
                    self.current_state['status'] = 'warning' if all_warnings else 'ok'
                    self.current_state['warnings'] = all_warnings
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

                    logger.info(f"✓ Reading accepted: {total_value:.4f} m³")

                    # Publish to MQTT
                    await self.publish_to_mqtt(total_value, all_warnings, predictions)
                else:
                    self.current_state['status'] = 'error'
                    self.current_state['warnings'] = all_warnings
                    logger.error(f"✗ Reading rejected: {total_value:.4f} m³")

                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error during processing: {e}", exc_info=True)
                self.current_state['status'] = 'error'
                self.current_state['warnings'] = [str(e)]
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
        """Reset the previous value (for meter replacement)."""
        logger.info("Resetting previous value")
        self.previous_value = None
        self.last_update_time = None

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
            # Subscribe to topics
            mqtt_config = self.config['mqtt']
            client.subscribe(mqtt_config['trigger_topic'], qos=2)
            client.subscribe(mqtt_config['reset_topic'], qos=2)
            client.subscribe("homeassistant/status", qos=1)
            logger.info(f"Subscribed to {mqtt_config['trigger_topic']}")
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
            self.reset_previous_value()

        elif topic == "homeassistant/status":
            if payload == "online":
                logger.info("Home Assistant came online - republishing discovery")
                self.publish_discovery()

    def start_mqtt(self):
        """Initialize and start MQTT client."""
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
        self.mqtt_client.connect(
            mqtt_config['broker'],
            mqtt_config['port'],
            mqtt_config['keepalive']
        )

        # Start loop in background thread
        self.mqtt_client.loop_start()
        logger.info("MQTT client started")

    def stop_mqtt(self):
        """Stop MQTT client."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("MQTT client stopped")


# Global service instance
service: Optional[WatermeterService] = None


def get_service() -> WatermeterService:
    """Get or create the global service instance."""
    global service
    if service is None:
        service = WatermeterService()
    return service
