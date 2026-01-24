# Water Meter AI Inference Service

## Übersicht

Dieser Service ersetzt den bisherigen Node-Red Flow und kombiniert alle Funktionen in einem Python-Service:
- MQTT-basierter Trigger
- Automatisches Laden von Wasserzähler-Bildern
- OpenVINO Inference für Ziffern und Analogzeiger
- Konsistenzprüfung und Plausibilitätschecks
- MQTT-Integration mit Home Assistant

## Architektur

```
MQTT Trigger (watermeter/status)  ←─────────┐
         ↓                                   │
    Bild-Loader (7 Bilder von AI-on-the-edge)│
         ↓                                   │
    OpenVINO Inference (digits & arrows)    │
         ↓                                   │
    Wert-Berechnung + Consistency Check     │
         ↓                                   │
    Plausibilitätsprüfung (Reverse/Rate)    │
         ↓                                   │
    MQTT zu Home Assistant                  │
         ↓                                   │
    Status Update → Web Dashboard ──────────┘
                    (mit "Jetzt Auslesen" Button)
```

## Komponenten

### 1. Bilder-Quellen
Der Service lädt 7 Bilder vom AI-on-the-edge Device:

**Ziffern (digits):**
- `main_dig1` - Hunderter (×100 m³)
- `main_dig2` - Zehner (×10 m³)
- `main_dig3` - Einer (×1 m³)

**Analogzeiger (arrows):**
- `main_ana1` - Zehntel (×0.1 m³)
- `main_ana2` - Hundertstel (×0.01 m³)
- `main_ana3` - Tausendstel (×0.001 m³)
- `main_ana4` - Zehntausendstel (×0.0001 m³)

URL-Pattern: `http://192.168.5.136/img_tmp/{id}.jpg`

### 2. Inference-Modelle

**Digits Classifier:**
- Modell: `ov_model_digits/model_mobilenetv3_small_100_c11_r144.xml`
- Klassen: ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'NAN']
- Input: 144×144 RGB

**Arrows Classifier:**
- Modell: `ov_model_arrows/model_mobilenetv3_small_100_c20_r144.xml`
- Klassen: ['0.0', '0.5', '1.0', '1.5', ..., '9.0', '9.5']
- Input: 144×144 RGB

### 3. Wert-Berechnung

**Formel:**
```
total = (dig1 × 100) + (dig2 × 10) + (dig3 × 1) +
        (⌊ana1⌋ × 0.1) + (⌊ana2⌋ × 0.01) + (⌊ana3⌋ × 0.001) + (⌊ana4⌋ × 0.0001)
```

**Consistency Check:**
Ein Zeiger/Ziffer an Position `i` mit Wert `X.5` (Halbposition) sollte bedeuten, dass die nächste Position `i+1` im Bereich 5-9 liegt.

Beispiel:
- `dig1 = 2.0`, `dig2 = 3` → OK (dig1 zeigt genau auf 2, dig2 ist im unteren Bereich)
- `dig1 = 2.5`, `dig2 = 7` → OK (dig1 zwischen 2 und 3, dig2 im oberen Bereich)
- `dig1 = 2.5`, `dig2 = 2` → WARNUNG (Inkonsistenz!)

### 4. Plausibilitätschecks

**Reverse Prevention:**
- Der Zählerwert kann nicht rückwärts laufen
- Wenn `new_value < previous_value` → Wert wird verworfen
- Reset-Funktion verfügbar (bei Zählerwechsel)

**Rate Limiting:**
- Bilder werden mit max. 1/Sekunde geladen (Schutz für AI-on-the-edge)
- Inference wird sequenziell durchgeführt
- Low-confidence Bilder werden max. 1 pro Stunde gespeichert

### 5. Low Confidence Handling

Bei `confidence < 0.8`:
- Bild wird gespeichert: `/media/import/{class}/{id}_{timestamp}.jpg`
- Format: `main_dig1_20260124143025.jpg`
- Optional: Label Studio Sync triggern (für Re-Training)

### 6. Web Dashboard

**Status-Seite:** `http://localhost:8000/`

**Anzeige:**
- **Bilder in Reihenfolge** (dig1, dig2, dig3, ana1-4)
  - Thumbnail des Bildes
  - Erkannter Wert (z.B. "7" oder "3.5")
  - Confidence Score (z.B. "95.3%")
  - Farb-Codierung:
    - Grün: confidence ≥ 0.95
    - Gelb: 0.80 ≤ confidence < 0.95
    - Rot: confidence < 0.80

- **Berechneter Gesamtwert**
  - Große Anzeige: "123.4567 m³"
  - Letzte Aktualisierung: Timestamp
  - Status: "OK" / "Warnung"

- **Warnungen & Status**
  - Consistency Warnings (z.B. "dig1=2.5 vs dig2=2 - inkonsistent!")
  - Rate too high (z.B. "Sprung um +10 m³ in 5 Minuten")
  - Reverse detection (z.B. "Rückwärtslauf erkannt: 123.4 → 122.1")
  - Low confidence alerts

- **Aktionen**
  - Button "Jetzt Auslesen" → Triggert neue Messung
  - Button "Reset Previous Value" → Setzt Reverse-Protection zurück
  - Auto-Refresh Toggle (Live-Updates)

**Technologie:**
- FastAPI + Jinja2 Templates (HTML)
- HTMX für dynamische Updates (ohne Page Reload)
- Minimal CSS (responsive, mobile-friendly)
- Optional: WebSocket für Live-Updates

## Konfiguration

### Environment Variables / Config File

```yaml
# AI-on-the-edge Device
AIOTE_HOST: "192.168.5.136"
AIOTE_IMAGE_PATH: "/img_tmp"

# Image IDs
DIGIT_IDS: ["main_dig1", "main_dig2", "main_dig3"]
ARROW_IDS: ["main_ana1", "main_ana2", "main_ana3", "main_ana4"]

# MQTT Settings
MQTT_BROKER: "192.168.4.11"
MQTT_PORT: 1883
MQTT_TRIGGER_TOPIC: "watermeter/status"
MQTT_TRIGGER_PAYLOAD: "Flow finished"
MQTT_PUBLISH_TOPIC: "homeassistant/sensor/watermeter/state"

# Home Assistant MQTT Discovery
HA_DISCOVERY_PREFIX: "homeassistant"
HA_DEVICE_NAME: "AI Water Meter"
HA_SENSOR_NAME: "Water Usage"
HA_UNIT: "m³"
HA_DEVICE_CLASS: "water"
HA_STATE_CLASS: "total_increasing"

# Inference Settings
CONFIDENCE_THRESHOLD: 0.8
LOW_CONFIDENCE_SAVE_PATH: "/media/import"

# Label Studio (optional)
LABEL_STUDIO_ENABLED: false
LABEL_STUDIO_URL: "http://192.168.4.35:8080"
LABEL_STUDIO_TOKEN: "***REMOVED***"
LABEL_STUDIO_DIGITS_STORAGE_ID: 1
LABEL_STUDIO_ARROWS_STORAGE_ID: 3

# Rate Limiting
IMAGE_FETCH_DELAY: 1.0  # seconds between image fetches
LOW_CONFIDENCE_SAVE_RATE: 3600  # max 1 per hour
```

## Workflow Details

### 1. MQTT Trigger
```
Empfang: watermeter/status = "Flow finished"
         ↓
    Starte Verarbeitung
```

### 2. Bild-Laden (sequenziell)
```
Für jede ID in [main_dig1, main_dig2, main_dig3,
                main_ana1, main_ana2, main_ana3, main_ana4]:
    1. Warte 1 Sekunde (Rate Limit)
    2. Lade http://192.168.5.136/img_tmp/{id}.jpg
    3. Speichere temporär
```

### 3. Inference
```
Für jedes Bild:
    1. Bestimme Classifier (digits/arrows basierend auf ID)
    2. Führe Inference durch
    3. Erhalte {class: "X", confidence: 0.XX}
    4. Falls confidence < 0.8:
       → Speichere Bild für späteres Training
```

### 4. Wert-Berechnung
```
1. Sammle alle 7 Ergebnisse
2. Sortiere nach Position (dig1, dig2, dig3, ana1-ana4)
3. Berechne Gesamtwert
4. Führe Consistency Check durch
5. Generiere Warnings bei Inkonsistenzen
```

### 5. Plausibilitätsprüfung
```
IF new_value < previous_value:
    → Verwerfe Wert (Log Warning)
ELSE:
    → Akzeptiere Wert
    → Speichere als previous_value
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

### 1. Dependencies
```bash
pip install openvino opencv-python numpy fastapi uvicorn paho-mqtt httpx pyyaml jinja2 python-multipart
```

### 2. Verzeichnisstruktur
```
watermeter/
├── inference.py                    # FastAPI Service (bestehend)
├── watermeter_service.py          # Neuer MQTT-Service
├── config.yaml                     # Konfiguration
├── templates/
│   └── dashboard.html             # Web Dashboard Template
├── static/
│   └── style.css                  # Dashboard Styling
├── ov_model_digits/
│   └── model_mobilenetv3_small_100_c11_r144.xml
├── ov_model_arrows/
│   └── model_mobilenetv3_small_100_c20_r144.xml
└── /media/import/
    ├── digits/
    └── arrows/
```

### 3. Systemd Service (optional)
```ini
[Unit]
Description=Water Meter AI Service
After=network.target

[Service]
Type=simple
User=watermeter
WorkingDirectory=/var/ml/openvino-notebooks/watermeter
ExecStart=/usr/bin/python3 watermeter_service.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Migration von Node-Red

### Was ersetzt wird:
- ✅ MQTT Trigger Node
- ✅ HTTP Request Nodes (Bild-Laden)
- ✅ Rate Limiting (Delay Nodes)
- ✅ Semaphore (nur 1 Request gleichzeitig)
- ✅ Function Nodes (Berechnung, Consistency Check)
- ✅ Low Confidence Handling
- ✅ Label Studio Sync
- ✅ Home Assistant MQTT Sensor

### Vorteile:
- Einfachere Wartung (alles in Python)
- Bessere Performance (kein HTTP Overhead für Inference)
- Leichtere Testbarkeit
- Versionskontrolle
- Weniger Abhängigkeiten (kein Node-Red nötig)

## API Endpoints

### Web Dashboard
- `GET /` - Status Dashboard (HTML)
- `GET /api/status` - Aktueller Status als JSON
- `POST /api/trigger` - Manuelle Messung auslösen
- `POST /api/reset` - Previous Value zurücksetzen

### Legacy Endpoints (Label Studio)
- `POST /predict/{model_type}/setup` - Model Setup
- `POST /predict/{model_type}/predict` - Batch Prediction
- `GET /predict/{model_type}/health` - Health Check
- `POST /predict/{model_type}` - Direct Prediction

## Testing

### Manual Trigger via MQTT
```bash
mosquitto_pub -h 192.168.4.11 -t "watermeter/status" -m "Flow finished"
```

### Manual Trigger via Web API
```bash
curl -X POST http://localhost:8000/api/trigger
```

### Status abfragen
```bash
curl http://localhost:8000/api/status | jq
```

### Reset Previous Value
```bash
curl -X POST http://localhost:8000/api/reset
```

### Check Logs
```bash
journalctl -u watermeter -f
```

### Web Dashboard öffnen
```bash
xdg-open http://localhost:8000/
```

## Troubleshooting

### Häufige Probleme:

**1. Bilder nicht erreichbar**
- Prüfe: `curl http://192.168.5.136/img_tmp/main_dig1.jpg`
- Stelle sicher, dass AI-on-the-edge läuft

**2. MQTT Connection Failed**
- Prüfe MQTT Broker: `mosquitto_pub -h 192.168.4.11 -t test -m hello`
- Prüfe Firewall-Regeln

**3. Low Confidence Warnings**
- Normal bei schlechter Beleuchtung
- Gespeicherte Bilder für Re-Training nutzen
- Label Studio Sync prüfen

**4. Reverse Detection**
- Normal bei Fehlklassifikation
- Check logs für tatsächlichen vs. erkannten Wert
- Eventuell Modell neu trainieren

**5. Consistency Warnings**
- Deutet auf Klassifikationsfehler hin
- Prüfe betroffene Bilder in `/media/import/`
- Feedback-Loop für Training
