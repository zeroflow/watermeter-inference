# Water Meter Service - Implementation Progress

## Overview
Consolidation of the Node-RED flow into a Python service with a web dashboard.

## Status Legend
- ✅ Completed
- 🔄 In Progress
- ⏳ Pending
- ❌ Blocked

---

## 1. Documentation

### README.md
- ✅ Architecture documented
- ✅ Components described
- ✅ Web Dashboard concept
- ✅ Configuration defined
- ✅ API endpoints documented
- ✅ Testing guide
- ✅ Migration guide

### Progress.md
- ✅ File created
- 🔄 Being updated during development

---

## 2. Frontend (Web Dashboard)

### templates/dashboard.html
- ✅ HTML template created
- ✅ HTMX integration
- ✅ Auto-refresh toggle
- ✅ Buttons: "Read Now", "Reset"
- ⏳ Status display template (dynamically loaded from API)

### static/style.css
- ✅ Base styles
- ✅ Header & navigation
- ✅ Button styles
- ✅ Toggle switch
- ✅ Total value display
- ✅ Warnings section
- ✅ Images grid
- ✅ Confidence color coding
- ✅ Responsive design
- ✅ Loading states

**Notes:**
- HTMX polling every 5 seconds
- Responsive grid for images (7 cards)
- Color coding: Green (≥0.95), Yellow (0.80-0.95), Red (<0.80)

---

## 3. Backend - Core Service

### config.yaml
- ⏳ Create configuration file
- ⏳ AI-on-the-edge settings
- ⏳ MQTT settings
- ⏳ Home Assistant settings
- ⏳ Threshold values

### watermeter_service.py (New Main Service)

#### 3.1 Configuration & Setup
- ⏳ Load config (YAML)
- ⏳ Environment variables support
- ⏳ Logging setup
- ⏳ `WatermeterService` class definition

#### 3.2 MQTT Integration
- ⏳ MQTT client initialization
- ⏳ Connect to broker (192.168.4.11)
- ⏳ Subscribe to "watermeter/status"
- ⏳ Callback for trigger ("Flow finished")
- ⏳ Publish to Home Assistant
- ⏳ MQTT discovery for HA sensor

#### 3.3 Image Loader
**Function:** `async def fetch_images()`
- ⏳ Sequential loading (with 1s delay)
- ⏳ HTTP GET from AI-on-the-edge
- ⏳ URL building: `http://192.168.5.136/img_tmp/{id}.jpg`
- ⏳ Error handling (timeout, 404, etc.)
- ⏳ Temporary storage
- ⏳ Return: Dict[id, image_data]

**IDs:**
- main_dig1, main_dig2, main_dig3 (digits)
- main_ana1, main_ana2, main_ana3, main_ana4 (arrows)

#### 3.4 Inference Engine
**Function:** `async def run_inference(images: Dict)`
- ⏳ Integration with existing classifiers
- ⏳ Parallel inference for all 7 images
- ⏳ Mapping: ID → Classifier (digits/arrows)
- ⏳ Return: Dict[id, {class, confidence, image}]

**Use existing classifiers:**
- `digits_classifier` (from inference.py)
- `arrows_classifier` (from inference.py)

#### 3.5 Value Calculation
**Function:** `def calculate_total(predictions: Dict) -> Dict`
- ⏳ Implement formula:
  ```
  total = (dig1 × 100) + (dig2 × 10) + (dig3 × 1) +
          (⌊ana1⌋ × 0.1) + (⌊ana2⌋ × 0.01) +
          (⌊ana3⌋ × 0.001) + (⌊ana4⌋ × 0.0001)
  ```
- ⏳ Sort by position
- ⏳ Integer extraction for arrows (⌊value⌋)
- ⏳ Return: `{total: float, raw_values: Dict}`

#### 3.6 Consistency Check
**Function:** `def check_consistency(predictions: Dict) -> List[str]`
- ⏳ For each position i:
  - If value[i] is at .5 (half position)
  - Check: value[i+1] should be ≥ 5
- ⏳ Collect warnings
- ⏳ Return: List of warning strings

**Example:**
```
dig1=2.5, dig2=3 → WARNING: "dig1 half-position but dig2=3 (expected ≥5)"
ana1=5.5, ana2=7 → OK
```

#### 3.7 Plausibility Check
**Function:** `def validate_plausibility(new_value: float) -> Dict`
- ⏳ Reverse detection:
  - Compare with `self.previous_value`
  - If `new_value < previous_value` → reject
- ⏳ Rate check:
  - Maximum difference per time unit
  - Configurable (e.g., max +5 m³/hour)
- ⏳ Return: `{valid: bool, reason: str}`

**State management:**
- ⏳ Store `previous_value` (in-memory)
- ⏳ Store `last_update_time`
- ⏳ Reset function for meter replacement

#### 3.8 Low Confidence Handling
**Function:** `async def save_low_confidence(id: str, image, prediction: Dict)`
- ⏳ Threshold check (< 0.8)
- ⏳ Rate limiting (max 1/hour per ID)
- ⏳ Generate timestamp: `YYYYMMDDHHMMSS`
- ⏳ Save: `/media/import/{class}/{id}_{ts}.jpg`
- ⏳ Optional: Trigger Label Studio sync

**Label Studio Sync:**
- ⏳ HTTP POST to Storage ID 1 (digits)
- ⏳ HTTP POST to Storage ID 3 (arrows)
- ⏳ 60s delay between syncs
- ⏳ Error handling

#### 3.9 Main Workflow
**Function:** `async def process_reading()`
- ⏳ 1. Load images
- ⏳ 2. Run inference
- ⏳ 3. Calculate value
- ⏳ 4. Consistency check
- ⏳ 5. Plausibility validation
- ⏳ 6. Low confidence handling
- ⏳ 7. Update state
- ⏳ 8. MQTT publish
- ⏳ 9. Update dashboard state

---

## 4. Backend - FastAPI Integration

### inference.py (Extend)

#### 4.1 Web Dashboard Endpoints
**GET /**
- ⏳ Render Jinja2 template
- ⏳ Return dashboard.html

**GET /api/status**
- ⏳ Return current state as JSON
- ⏳ Format:
  ```json
  {
    "total_value": 123.4567,
    "unit": "m³",
    "last_update": "2026-01-24T14:30:25",
    "status": "ok|warning",
    "warnings": ["..."],
    "predictions": [
      {
        "id": "main_dig1",
        "class": "7",
        "confidence": 0.953,
        "image_url": "/api/image/main_dig1"
      },
      ...
    ]
  }
  ```

**POST /api/trigger**
- ⏳ Start manual reading
- ⏳ Call `process_reading()` (async)
- ⏳ Response: Status message

**POST /api/reset**
- ⏳ Reset `previous_value`
- ⏳ Response: Confirmation

**GET /api/image/{id}**
- ⏳ Return last image for ID
- ⏳ As JPEG with base64 or directly as image response

#### 4.2 Shared State
- ⏳ Global state dictionary for dashboard
- ⏳ Lock/Mutex for thread safety
- ⏳ Update after each reading

#### 4.3 Static Files & Templates
- ⏳ `app.mount("/static", StaticFiles(directory="static"))`
- ⏳ Jinja2Templates setup
- ⏳ Configure template rendering

---

## 5. Integration & Testing

### 5.1 Service Integration
- ⏳ Integrate WatermeterService into FastAPI
- ⏳ Lifecycle management (startup/shutdown)
- ⏳ Start MQTT client in background
- ⏳ Shared state between MQTT and FastAPI

### 5.2 Error Handling
- ⏳ Try-catch for all async functions
- ⏳ Logging at all levels
- ⏳ Graceful degradation
- ⏳ Display errors in dashboard

### 5.3 Manual Testing
- ⏳ MQTT trigger test
- ⏳ Web dashboard test (browser)
- ⏳ API endpoint tests (curl)
- ⏳ Consistency check tests
- ⏳ Plausibility tests
- ⏳ Low confidence tests

### 5.4 Edge Cases
- ⏳ AI-on-the-edge unreachable
- ⏳ Images missing (404)
- ⏳ MQTT connection lost
- ⏳ All predictions low confidence
- ⏳ NAN class detected

---

## 6. Deployment

### 6.1 Dependencies
- ✅ requirements.txt prepared
  - openvino
  - opencv-python
  - numpy
  - fastapi
  - uvicorn
  - paho-mqtt
  - httpx
  - pyyaml
  - jinja2
  - python-multipart

### 6.2 Systemd Service
- ⏳ Create systemd unit file
- ⏳ Install service
- ⏳ Configure auto-start

### 6.3 Directories
- ⏳ Create /media/import/digits/
- ⏳ Create /media/import/arrows/
- ⏳ Set permissions

---

## 7. Migration from Node-RED

### 7.1 Analyze Node-RED Flow
- ✅ Read flows.json
- ✅ Understood logic
- ✅ Identified nodes

### 7.2 Feature Mapping
- ✅ MQTT Trigger → WatermeterService.on_mqtt_message()
- ✅ HTTP Request → fetch_images()
- ✅ Delay/Rate Limit → asyncio.sleep()
- ✅ Semaphore → Async Lock
- ✅ Function Nodes → Python Functions
- ✅ Image Viewer → Web Dashboard
- ✅ Debug Nodes → Logging
- ✅ HA Sensor → MQTT Publish

### 7.3 Cutover Plan
- ⏳ Parallel testing (Node-RED + Python Service)
- ⏳ Compare results
- ⏳ Deactivate Node-RED flow
- ⏳ Run Python service standalone

---

## 8. Optimizations (Optional, Later)

- ⏳ WebSocket instead of polling
- ⏳ Store history in SQLite
- ⏳ Grafana dashboard
- ⏳ Prometheus metrics
- ⏳ Docker container
- ⏳ Automatic re-training with new images

---

## Next Steps (Prioritized)

1. **Create config.yaml** ← START HERE
2. **watermeter_service.py skeleton**
3. **Implement image loader**
4. **Inference integration**
5. **Value calculation + consistency check**
6. **FastAPI dashboard endpoints**
7. **MQTT integration**
8. **Testing**
9. **Deployment**

---

## Notes & Questions

### Open Questions:
- [ ] Should history be stored in DB or only in-memory?
- [ ] WebSocket vs. HTMX polling for dashboard?
- [ ] Grafana integration desired?

### Technical Decisions:
- ✅ HTMX for frontend (lightweight, no JS framework needed)
- ✅ Jinja2 for templates
- ✅ Paho-MQTT for MQTT client
- ✅ AsyncIO for non-blocking operations
- ✅ Reuse existing OpenVINO classifiers

### Known Limitations:
- Rate limit: 1 image/second (AI-on-the-edge protection)
- MQTT QoS: 2 (exactly once)
- No persistence (restart → state lost) - acceptable for MVP

---

## ✅ IMPLEMENTATION COMPLETE!

### What Was Implemented:

1. ✅ **config.yaml** - Complete configuration
2. ✅ **watermeter_service.py** - Core service with:
   - Image loader (sequential, rate-limited)
   - Inference integration (OpenVINO)
   - Value calculation
   - Consistency check
   - Plausibility validation (reverse/rate)
   - Low confidence handling
   - MQTT integration (trigger + publish)
3. ✅ **app.py** - FastAPI web application with:
   - Dashboard endpoint
   - Status API (JSON + HTML)
   - Trigger endpoint
   - Reset endpoint
   - Health check
4. ✅ **Templates** - Web dashboard:
   - dashboard.html (main page)
   - status_fragment.html (HTMX status fragment)
5. ✅ **static/style.css** - Complete styling
6. ✅ **requirements.txt** - Dependencies
7. ✅ **QUICKSTART.md** - Start & test instructions
8. ✅ **Directories** - import/digits & import/arrows
9. ✅ **Model paths corrected** - digits/ov_model & arrows/ov_model

### Next Steps for Testing:

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Start service**: `uvicorn watermeter.app:app --host 0.0.0.0 --port 8001`
3. **Open dashboard**: http://localhost:8001
4. **Test**: See [QUICKSTART.md](QUICKSTART.md)

---

**Last Updated:** 2026-01-24 (Implementation Complete)
