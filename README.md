# Water Meter AI Inference Service

## Overview

This service replaces the previous Node-Red flow, combining all functions in a single Python service:
- MQTT-based trigger
- Automatic water meter image loading
- OpenVINO inference for digits and analog dials
- Consistency checks and plausibility validation
- MQTT integration with Home Assistant

## Architecture

```
MQTT Trigger (watermeter/status)  <------------+
         |                                     |
    Image Loader (7 images from AI-on-the-edge)|
         |                                     |
    OpenVINO Inference (digits & arrows)       |
         |                                     |
    Value Calculation + Consistency Check       |
         |                                     |
    Plausibility Check (Reverse/Rate)          |
         |                                     |
    MQTT to Home Assistant                     |
         |                                     |
    Status Update -> Web Dashboard ------------+
                     (with "Read Now" button)
```

## Components

### 1. Image Sources
The service loads 7 images from the AI-on-the-edge device:

**Digits:**
- `main_dig1` - Hundreds (x100 m³)
- `main_dig2` - Tens (x10 m³)
- `main_dig3` - Ones (x1 m³)

**Analog dials (arrows):**
- `main_ana1` - Tenths (x0.1 m³)
- `main_ana2` - Hundredths (x0.01 m³)
- `main_ana3` - Thousandths (x0.001 m³)
- `main_ana4` - Ten-thousandths (x0.0001 m³)

URL pattern: `http://<AIOTE_HOST>/img_tmp/{id}.jpg`

### 2. Inference Models

**Digits Classifier:**
- Model: `ov_model_digits/model_mobilenetv3_small_100_c11_r144.xml`
- Classes: ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'NAN']
- Input: 144x144 RGB

**Arrows Classifier:**
- Model: `ov_model_arrows/model_mobilenetv3_small_100_c20_r144.xml`
- Classes: ['0.0', '0.5', '1.0', '1.5', ..., '9.0', '9.5']
- Input: 144x144 RGB

### 3. Value Calculation

**Formula:**
```
total = (dig1 x 100) + (dig2 x 10) + (dig3 x 1) +
        (floor(ana1) x 0.1) + (floor(ana2) x 0.01) + (floor(ana3) x 0.001) + (floor(ana4) x 0.0001)
```

**Consistency Check:**
A pointer/digit at position `i` with value `X.5` (half-position) should mean that the next position `i+1` is in the range 5-9.

Example:
- `dig1 = 2.0`, `dig2 = 3` -> OK (dig1 points exactly at 2, dig2 is in the lower range)
- `dig1 = 2.5`, `dig2 = 7` -> OK (dig1 between 2 and 3, dig2 in the upper range)
- `dig1 = 2.5`, `dig2 = 2` -> WARNING (inconsistency!)

### 4. Plausibility Checks

**Reverse Prevention:**
- The meter value cannot run backwards
- If `new_value < previous_value` -> value is rejected
- Reset function available (for meter replacement)

**Rate Limiting:**
- Images are loaded at max 1/second (protection for AI-on-the-edge)
- Inference is performed sequentially
- Low-confidence images are saved at most once per hour

### 5. Low Confidence Handling

When `confidence < 0.8`:
- Image is saved: `/training/{type}/input/{class}/{id}_{timestamp}.jpg`
- Format: `main_dig1_20260124143025.jpg`

### 6. Web Dashboard

**Status page:** `http://localhost:8001/`

**Display:**
- **Images in order** (dig1, dig2, dig3, ana1-4)
  - Image thumbnail
  - Recognized value (e.g. "7" or "3.5")
  - Confidence score (e.g. "95.3%")
  - Color coding:
    - Green: confidence >= 0.95
    - Yellow: 0.80 <= confidence < 0.95
    - Red: confidence < 0.80

- **Calculated total value**
  - Large display: "123.4567 m³"
  - Last update: timestamp
  - Status: "OK" / "Warning"

- **Warnings & Status**
  - Consistency warnings (e.g. "dig1=2.5 vs dig2=2 - inconsistent!")
  - Rate too high (e.g. "Jump of +10 m³ in 5 minutes")
  - Reverse detection (e.g. "Reverse detected: 123.4 -> 122.1")
  - Low confidence alerts

- **Actions**
  - "Read Now" button -> triggers new measurement
  - "Reset Previous Value" button -> resets reverse protection
  - Auto-refresh toggle (live updates)

**Technology:**
- FastAPI + Jinja2 Templates (HTML)
- HTMX for dynamic updates (no page reload)
- Minimal CSS (responsive, mobile-friendly)

## Configuration

### Config File

```yaml
# AI-on-the-edge Device
aiote:
  host: "192.168.x.x"  # Your AI-on-the-edge device IP

# MQTT Settings
mqtt:
  broker: "192.168.x.x"  # Your MQTT broker IP
  port: 1883
  trigger_topic: "watermeter/status"
  trigger_payload: "Flow finished"

# Home Assistant Integration
homeassistant:
  enabled: true
  publish_topic: "homeassistant/sensor/watermeter/state"

# Inference Settings
inference:
  confidence_threshold: 0.8
  device: "AUTO"  # CPU, GPU, or AUTO
```

See [config.yaml](config.yaml) for the full configuration reference.

## Workflow Details

### 1. MQTT Trigger
```
Receive: watermeter/status = "Flow finished"
         |
    Start processing
```

### 2. Image Loading (sequential)
```
For each ID in [main_dig1, main_dig2, main_dig3,
                main_ana1, main_ana2, main_ana3, main_ana4]:
    1. Wait (rate limit)
    2. Load http://<AIOTE_HOST>/img_tmp/{id}.jpg
    3. Store temporarily
```

### 3. Inference
```
For each image:
    1. Determine classifier (digits/arrows based on ID)
    2. Run inference
    3. Get {class: "X", confidence: 0.XX}
    4. If confidence < 0.8:
       -> Save image for later training
```

### 4. Value Calculation
```
1. Collect all 7 results
2. Sort by position (dig1, dig2, dig3, ana1-ana4)
3. Calculate total value
4. Run consistency check
5. Generate warnings for inconsistencies
```

### 5. Plausibility Check
```
IF new_value < previous_value:
    -> Reject value (log warning)
ELSE:
    -> Accept value
    -> Store as previous_value
```

### 6. MQTT Publish
```
Topic: homeassistant/sensor/watermeter/state
Payload: {
    "state": 123.4567,
    "attributes": {
        "unit_of_measurement": "m³",
        "device_class": "water",
        "state_class": "total_increasing",
        "warnings": ["..."],  // optional
        "confidences": {
            "main_dig1": 0.99,
            "main_dig2": 0.95,
            ...
        }
    }
}
```

## Installation

### Option 1: Docker (recommended)

See [DOCKER.md](DOCKER.md) for full instructions.

```bash
docker compose up -d
```

### Option 2: Local Python

```bash
pip install -r requirements.txt
uvicorn watermeter.app:app --host 0.0.0.0 --port 8001
```

### Option 3: Systemd Service

```ini
[Unit]
Description=Water Meter AI Service
After=network.target

[Service]
Type=simple
User=watermeter
WorkingDirectory=/opt/watermeter
ExecStart=/usr/bin/python3 -m uvicorn watermeter.app:app --host 0.0.0.0 --port 8001
Restart=always

[Install]
WantedBy=multi-user.target
```

## API Endpoints

### Web Dashboard
- `GET /` - Status dashboard (HTML)
- `GET /api/status` - Current status as JSON
- `POST /api/trigger` - Trigger manual measurement
- `POST /api/reset` - Reset previous value

### Legacy Endpoints (Label Studio)
- `POST /predict/{model_type}/setup` - Model setup
- `POST /predict/{model_type}/predict` - Batch prediction
- `GET /predict/{model_type}/health` - Health check
- `POST /predict/{model_type}` - Direct prediction

## Testing

### Manual Trigger via MQTT
```bash
mosquitto_pub -h <MQTT_BROKER_IP> -t "watermeter/status" -m "Flow finished"
```

### Manual Trigger via Web API
```bash
curl -X POST http://localhost:8001/api/trigger
```

### Query Status
```bash
curl http://localhost:8001/api/status | jq
```

### Reset Previous Value
```bash
curl -X POST http://localhost:8001/api/reset
```

### Open Web Dashboard
```bash
xdg-open http://localhost:8001/
```

## Troubleshooting

### Common Problems

**1. Images not reachable**
- Check: `curl http://<AIOTE_HOST>/img_tmp/main_dig1.jpg`
- Ensure AI-on-the-edge device is running

**2. MQTT Connection Failed**
- Check MQTT broker: `mosquitto_pub -h <MQTT_BROKER_IP> -t test -m hello`
- Check firewall rules

**3. Low Confidence Warnings**
- Normal with poor lighting
- Use saved images for re-training

**4. Reverse Detection**
- Normal with misclassification
- Check logs for actual vs. recognized value
- Consider retraining the model

**5. Consistency Warnings**
- Indicates classification errors
- Check affected images in the dashboard
- Feedback loop for training
