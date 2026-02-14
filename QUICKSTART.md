# Water Meter Service - Quick Start Guide

## Installation

### 1. Install dependencies

```bash
cd /path/to/watermeter
pip install -r requirements.txt
```

### 2. Check configuration

The file [config.yaml](config.yaml) contains all settings. Key settings:

```yaml
aiote:
  host: "192.168.x.x"  # AI-on-the-edge device IP

mqtt:
  broker: "192.168.x.x"  # MQTT broker IP
  trigger_topic: "watermeter/status"
  trigger_payload: "Flow finished"

dashboard:
  host: "0.0.0.0"
  port: 8001
```

**Adjust these values to match your network!**

## Starting the Service

### Option 1: Direct with Python

```bash
cd /path/to/watermeter
python -m uvicorn watermeter.app:app --host 0.0.0.0 --port 8001
```

### Option 2: With uvicorn (recommended for production)

```bash
cd /path/to/watermeter
uvicorn watermeter.app:app --host 0.0.0.0 --port 8001 --reload
```

The service starts at **http://localhost:8001**

## Open the Web Dashboard

Open in your browser: **http://localhost:8001**

You should see:
- Header with "Read Now" and "Reset" buttons
- Auto-refresh toggle
- Status display (initially empty)

## Testing

### Test 1: Manual Trigger via Web UI

1. Open http://localhost:8001
2. Click **"Read Now"**
3. Wait ~10 seconds (7 images @ 1s delay + inference)
4. Status should update with:
   - Calculated value (e.g. "123.4567 m³")
   - 7 images with recognized values
   - Confidence scores (green/yellow/red)

### Test 2: Manual Trigger via API

```bash
curl -X POST http://localhost:8001/api/trigger
```

Response:
```json
{"message": "Reading triggered successfully"}
```

### Test 3: Query Status

```bash
curl http://localhost:8001/api/status | jq
```

Response:
```json
{
  "total_value": 123.4567,
  "unit": "m³",
  "last_update": "2026-01-24T14:30:25",
  "status": "ok",
  "warnings": [],
  "predictions": [...]
}
```

### Test 4: MQTT Trigger (AI-on-the-edge Integration)

**Prerequisite:** MQTT broker running at your configured IP

```bash
mosquitto_pub -h <MQTT_BROKER_IP> -t "watermeter/status" -m "Flow finished"
```

The service should automatically start a measurement.

### Test 5: Reset Previous Value

```bash
curl -X POST http://localhost:8001/api/reset
```

Resets reverse detection (useful after meter replacement).

## Viewing Logs

The service logs all important events:

```
2026-01-24 14:30:15 - __main__ - INFO - Starting Water Meter Dashboard...
2026-01-24 14:30:15 - watermeter_service - INFO - WatermeterService initialized
2026-01-24 14:30:15 - watermeter_service - INFO - Connecting to MQTT broker 192.168.x.x:1883
2026-01-24 14:30:15 - watermeter_service - INFO - Connected to MQTT broker
2026-01-24 14:30:20 - watermeter_service - INFO - Trigger received - starting processing
2026-01-24 14:30:20 - watermeter_service - INFO - Fetching 7 images from AI-on-the-edge
2026-01-24 14:30:27 - watermeter_service - INFO - Successfully fetched 7/7 images
2026-01-24 14:30:27 - watermeter_service - INFO - Running inference on 7 images
2026-01-24 14:30:28 - watermeter_service - INFO - Inference completed for 7 images
2026-01-24 14:30:28 - watermeter_service - INFO - Calculated total: 123.4567 m³
2026-01-24 14:30:28 - watermeter_service - INFO - Reading accepted: 123.4567 m³
```

## Troubleshooting

### Problem: "No images fetched"

**Cause:** AI-on-the-edge device not reachable

**Solution:**
```bash
# Test connection
curl http://<AIOTE_HOST>/img_tmp/main_dig1.jpg --output test.jpg

# If not reachable:
# 1. Check IP in config.yaml
# 2. Check if device is running
# 3. Check network connectivity
```

### Problem: "MQTT connection failed"

**Cause:** MQTT broker not reachable

**Solution:**
```bash
# Test MQTT broker
mosquitto_pub -h <MQTT_BROKER_IP> -t test -m hello

# If not reachable:
# 1. Check broker IP in config.yaml
# 2. Start MQTT broker (mosquitto)
# 3. Check firewall
```

### Problem: "ModuleNotFoundError: No module named 'openvino'"

**Cause:** Dependencies not installed

**Solution:**
```bash
pip install -r requirements.txt
```

### Problem: Low Confidence Warnings

**Normal with:**
- Poor lighting
- Dirty meter
- Ambiguous digit positions

**Images are saved in:**
```
/training/digits/input/
/training/arrows/input/
```

These can be used for re-training.

### Problem: Consistency Warnings

**Example:** `dig1=2.5 vs dig2=2 - inconsistent!`

**Meaning:** The first pointer is between 2 and 3, but the second shows 2 (should be >=5 for consistency)

**Causes:**
- Misclassification
- Ambiguous pointer position

**Action:**
- Check images in the dashboard
- Consider retraining the model with more data

### Problem: Reverse Detection

**Log:** `Reverse detected: 123.4 -> 122.1`

**Cause:** Value is less than previous value (impossible for a water meter)

**Solutions:**
1. If meter replaced: Reset with `curl -X POST http://localhost:8001/api/reset`
2. If misclassification: Wait for next measurement
3. Check images and confidence scores
