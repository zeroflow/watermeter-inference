# Water Meter Service - Implementation Progress

## Übersicht
Zusammenführung des Node-Red Flows in einen Python-Service mit Web Dashboard.

## Status Legende
- ✅ Erledigt
- 🔄 In Arbeit
- ⏳ Ausstehend
- ❌ Blockiert

---

## 1. Dokumentation

### README.md
- ✅ Architektur dokumentiert
- ✅ Komponenten beschrieben
- ✅ Web Dashboard Konzept
- ✅ Konfiguration definiert
- ✅ API Endpoints dokumentiert
- ✅ Testing Guide
- ✅ Migration Guide

### Progress.md
- ✅ Datei erstellt
- 🔄 Wird während Entwicklung aktualisiert

---

## 2. Frontend (Web Dashboard)

### templates/dashboard.html
- ✅ HTML Template erstellt
- ✅ HTMX Integration
- ✅ Auto-Refresh Toggle
- ✅ Buttons: "Jetzt Auslesen", "Reset"
- ⏳ Status-Anzeige Template (dynamisch von API geladen)

### static/style.css
- ✅ Base Styles
- ✅ Header & Navigation
- ✅ Button Styles
- ✅ Toggle Switch
- ✅ Total Value Display
- ✅ Warnings Section
- ✅ Images Grid
- ✅ Confidence Color Coding
- ✅ Responsive Design
- ✅ Loading States

**Notizen:**
- HTMX polling alle 5 Sekunden
- Responsive Grid für Bilder (7 Karten)
- Farb-Codierung: Grün (≥0.95), Gelb (0.80-0.95), Rot (<0.80)

---

## 3. Backend - Core Service

### config.yaml
- ⏳ Konfigurationsdatei erstellen
- ⏳ AI-on-the-edge Settings
- ⏳ MQTT Settings
- ⏳ Home Assistant Settings
- ⏳ Threshold Values

### watermeter_service.py (Neuer Hauptservice)

#### 3.1 Konfiguration & Setup
- ⏳ Config laden (YAML)
- ⏳ Environment Variables Support
- ⏳ Logging Setup
- ⏳ Klassendefinition `WatermeterService`

#### 3.2 MQTT Integration
- ⏳ MQTT Client Init
- ⏳ Connect zu Broker (192.168.4.11)
- ⏳ Subscribe zu "watermeter/status"
- ⏳ Callback für Trigger ("Flow finished")
- ⏳ Publish zu Home Assistant
- ⏳ MQTT Discovery für HA Sensor

#### 3.3 Bild-Loader
**Funktion:** `async def fetch_images()`
- ⏳ Sequenzielles Laden (mit 1s delay)
- ⏳ HTTP GET von AI-on-the-edge
- ⏳ URL-Building: `http://192.168.5.136/img_tmp/{id}.jpg`
- ⏳ Fehlerbehandlung (Timeout, 404, etc.)
- ⏳ Temporäre Speicherung
- ⏳ Rückgabe: Dict[id, image_data]

**IDs:**
- main_dig1, main_dig2, main_dig3 (digits)
- main_ana1, main_ana2, main_ana3, main_ana4 (arrows)

#### 3.4 Inference Engine
**Funktion:** `async def run_inference(images: Dict)`
- ⏳ Integration mit bestehenden Classifiers
- ⏳ Parallele Inference für alle 7 Bilder
- ⏳ Mapping: ID → Classifier (digits/arrows)
- ⏳ Rückgabe: Dict[id, {class, confidence, image}]

**Bestehende Classifier nutzen:**
- `digits_classifier` (aus inference.py)
- `arrows_classifier` (aus inference.py)

#### 3.5 Wert-Berechnung
**Funktion:** `def calculate_total(predictions: Dict) -> Dict`
- ⏳ Formel implementieren:
  ```
  total = (dig1 × 100) + (dig2 × 10) + (dig3 × 1) +
          (⌊ana1⌋ × 0.1) + (⌊ana2⌋ × 0.01) +
          (⌊ana3⌋ × 0.001) + (⌊ana4⌋ × 0.0001)
  ```
- ⏳ Sortierung nach Position
- ⏳ Integer-Extraktion für Arrows (⌊value⌋)
- ⏳ Rückgabe: `{total: float, raw_values: Dict}`

#### 3.6 Consistency Check
**Funktion:** `def check_consistency(predictions: Dict) -> List[str]`
- ⏳ Für jede Position i:
  - Wenn Wert[i] hat .5 (Halbposition)
  - Prüfe: Wert[i+1] sollte ≥ 5 sein
- ⏳ Warnings sammeln
- ⏳ Rückgabe: Liste von Warning-Strings

**Beispiel:**
```
dig1=2.5, dig2=3 → WARNING: "dig1 half-position but dig2=3 (expected ≥5)"
ana1=5.5, ana2=7 → OK
```

#### 3.7 Plausibilitätsprüfung
**Funktion:** `def validate_plausibility(new_value: float) -> Dict`
- ⏳ Reverse Detection:
  - Vergleich mit `self.previous_value`
  - Falls `new_value < previous_value` → reject
- ⏳ Rate Check:
  - Max. Differenz pro Zeiteinheit
  - Konfigurierbar (z.B. max +5 m³/Stunde)
- ⏳ Rückgabe: `{valid: bool, reason: str}`

**State Management:**
- ⏳ `previous_value` speichern (in-memory)
- ⏳ `last_update_time` speichern
- ⏳ Reset-Funktion für Zählerwechsel

#### 3.8 Low Confidence Handling
**Funktion:** `async def save_low_confidence(id: str, image, prediction: Dict)`
- ⏳ Threshold Check (< 0.8)
- ⏳ Rate Limiting (max 1/Stunde pro ID)
- ⏳ Timestamp generieren: `YYYYMMDDHHMMSS`
- ⏳ Speichern: `/media/import/{class}/{id}_{ts}.jpg`
- ⏳ Optional: Label Studio Sync triggern

**Label Studio Sync:**
- ⏳ HTTP POST zu Storage ID 1 (digits)
- ⏳ HTTP POST zu Storage ID 3 (arrows)
- ⏳ 60s delay zwischen Syncs
- ⏳ Fehlerbehandlung

#### 3.9 Hauptworkflow
**Funktion:** `async def process_reading()`
- ⏳ 1. Bilder laden
- ⏳ 2. Inference durchführen
- ⏳ 3. Wert berechnen
- ⏳ 4. Consistency Check
- ⏳ 5. Plausibilitätsprüfung
- ⏳ 6. Low Confidence Handling
- ⏳ 7. State aktualisieren
- ⏳ 8. MQTT Publish
- ⏳ 9. Dashboard State aktualisieren

---

## 4. Backend - FastAPI Integration

### inference.py (Erweitern)

#### 4.1 Web Dashboard Endpoints
**GET /**
- ⏳ Jinja2 Template rendern
- ⏳ dashboard.html zurückgeben

**GET /api/status**
- ⏳ Aktuellen State als JSON zurückgeben
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
- ⏳ Manuelle Messung starten
- ⏳ `process_reading()` aufrufen (async)
- ⏳ Response: Status-Message

**POST /api/reset**
- ⏳ `previous_value` zurücksetzen
- ⏳ Response: Bestätigung

**GET /api/image/{id}**
- ⏳ Letztes Bild für ID zurückgeben
- ⏳ Als JPEG mit base64 oder direkt als Image Response

#### 4.2 Shared State
- ⏳ Global State Dictionary für Dashboard
- ⏳ Lock/Mutex für Thread-Safety
- ⏳ Aktualisierung nach jeder Messung

#### 4.3 Static Files & Templates
- ⏳ `app.mount("/static", StaticFiles(directory="static"))`
- ⏳ Jinja2Templates Setup
- ⏳ Template-Rendering konfigurieren

---

## 5. Integration & Testing

### 5.1 Service Integration
- ⏳ WatermeterService in FastAPI einbinden
- ⏳ Lifecycle Management (Startup/Shutdown)
- ⏳ MQTT Client im Background starten
- ⏳ Shared State zwischen MQTT und FastAPI

### 5.2 Error Handling
- ⏳ Try-Catch für alle async Funktionen
- ⏳ Logging auf allen Ebenen
- ⏳ Graceful Degradation
- ⏳ Fehler im Dashboard anzeigen

### 5.3 Manual Testing
- ⏳ MQTT Trigger Test
- ⏳ Web Dashboard Test (Browser)
- ⏳ API Endpoint Tests (curl)
- ⏳ Consistency Check Tests
- ⏳ Plausibility Tests
- ⏳ Low Confidence Tests

### 5.4 Edge Cases
- ⏳ AI-on-the-edge nicht erreichbar
- ⏳ Bilder fehlen (404)
- ⏳ MQTT Connection Lost
- ⏳ Alle Predictions low confidence
- ⏳ NAN Klasse erkannt

---

## 6. Deployment

### 6.1 Dependencies
- ✅ requirements.txt vorbereitet
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
- ⏳ systemd Unit File erstellen
- ⏳ Service installieren
- ⏳ Auto-Start konfigurieren

### 6.3 Verzeichnisse
- ⏳ /media/import/digits/ erstellen
- ⏳ /media/import/arrows/ erstellen
- ⏳ Berechtigungen setzen

---

## 7. Migration von Node-Red

### 7.1 Node-Red Flow analysieren
- ✅ flows.json gelesen
- ✅ Logik verstanden
- ✅ Nodes identifiziert

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
- ⏳ Parallel-Test (Node-Red + Python Service)
- ⏳ Vergleich der Ergebnisse
- ⏳ Node-Red Flow deaktivieren
- ⏳ Python Service alleine laufen lassen

---

## 8. Optimierungen (Optional, später)

- ⏳ WebSocket statt Polling
- ⏳ Historie in SQLite speichern
- ⏳ Grafana Dashboard
- ⏳ Prometheus Metrics
- ⏳ Docker Container
- ⏳ Automatisches Re-Training mit neuen Bildern

---

## Nächste Schritte (Priorisiert)

1. **config.yaml erstellen** ← START HIER
2. **watermeter_service.py Grundgerüst**
3. **Bild-Loader implementieren**
4. **Inference Integration**
5. **Wert-Berechnung + Consistency Check**
6. **FastAPI Dashboard Endpoints**
7. **MQTT Integration**
8. **Testing**
9. **Deployment**

---

## Notizen & Fragen

### Offene Fragen:
- [ ] Soll History in DB gespeichert werden oder nur in-memory?
- [ ] WebSocket vs. HTMX Polling für Dashboard?
- [ ] Grafana Integration gewünscht?

### Technische Entscheidungen:
- ✅ HTMX für Frontend (lightweight, kein JS-Framework nötig)
- ✅ Jinja2 für Templates
- ✅ Paho-MQTT für MQTT Client
- ✅ AsyncIO für non-blocking Operations
- ✅ Bestehende OpenVINO Classifier wiederverwenden

### Bekannte Limitierungen:
- Rate Limit: 1 Bild/Sekunde (AI-on-the-edge Schutz)
- MQTT QoS: 2 (exactly once)
- Keine Persistenz (restart → State verloren) - akzeptabel für MVP

---

## ✅ IMPLEMENTIERUNG ABGESCHLOSSEN!

### Was wurde implementiert:

1. ✅ **config.yaml** - Vollständige Konfiguration
2. ✅ **watermeter_service.py** - Core Service mit:
   - Bild-Loader (sequenziell, rate-limited)
   - Inference Integration (OpenVINO)
   - Wert-Berechnung
   - Consistency Check
   - Plausibilitätsprüfung (Reverse/Rate)
   - Low Confidence Handling
   - MQTT Integration (Trigger + Publish)
3. ✅ **app.py** - FastAPI Web Application mit:
   - Dashboard Endpoint
   - Status API (JSON + HTML)
   - Trigger Endpoint
   - Reset Endpoint
   - Health Check
4. ✅ **Templates** - Web Dashboard:
   - dashboard.html (Haupt-Seite)
   - status_fragment.html (HTMX Status-Fragment)
5. ✅ **static/style.css** - Vollständiges Styling
6. ✅ **requirements.txt** - Dependencies
7. ✅ **QUICKSTART.md** - Start & Test Anleitung
8. ✅ **Verzeichnisse** - import/digits & import/arrows
9. ✅ **Modell-Pfade korrigiert** - digits/ov_model & arrows/ov_model

### Nächste Schritte zum Testen:

1. **Dependencies installieren**: `pip install -r requirements.txt`
2. **Service starten**: `python app.py`
3. **Dashboard öffnen**: http://localhost:8000
4. **Testen**: Siehe [QUICKSTART.md](QUICKSTART.md)

---

**Letzte Aktualisierung:** 2026-01-24 (Implementation Complete)
