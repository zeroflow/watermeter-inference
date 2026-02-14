# Water Meter Service - Quick Start Guide

## 🚀 Installation

### 1. Dependencies installieren

```bash
cd /var/ml/openvino-notebooks/watermeter
pip install -r requirements.txt
```

### 2. Konfiguration prüfen

Die Datei [config.yaml](config.yaml) enthält alle Einstellungen. Wichtige Settings:

```yaml
aiote:
  host: "192.168.5.136"  # AI-on-the-edge Device IP

mqtt:
  broker: "192.168.4.11"  # MQTT Broker IP
  trigger_topic: "watermeter/status"
  trigger_payload: "Flow finished"

dashboard:
  host: "0.0.0.0"
  port: 8001
```

**Falls nötig, passe diese Werte an!**

## 🎯 Service starten

### Variante 1: Direkt mit Python

```bash
cd /var/ml/openvino-notebooks/watermeter
python -m uvicorn watermeter.app:app --host 0.0.0.0 --port 8001
```

### Variante 2: Mit uvicorn (empfohlen für Produktion)

```bash
cd /var/ml/openvino-notebooks/watermeter
uvicorn watermeter.app:app --host 0.0.0.0 --port 8001 --reload
```

Der Service startet auf **http://localhost:8001**

## 🌐 Web Dashboard öffnen

Öffne im Browser: **http://localhost:8001**

Du siehst:
- ✅ Header mit Buttons "Jetzt Auslesen" und "Reset"
- ✅ Auto-Refresh Toggle
- ✅ Status-Anzeige (initial leer)

## 🧪 Testing

### Test 1: Manueller Trigger via Web UI

1. Öffne http://localhost:8001
2. Klicke auf **"Jetzt Auslesen"**
3. Warte ~10 Sekunden (7 Bilder @ 1s delay + Inference)
4. Status sollte aktualisiert werden mit:
   - Berechneter Wert (z.B. "123.4567 m³")
   - 7 Bilder mit erkannten Werten
   - Confidence Scores (grün/gelb/rot)

### Test 2: Manueller Trigger via API

```bash
curl -X POST http://localhost:8001/api/trigger
```

Response:
```json
{"message": "Reading triggered successfully"}
```

### Test 3: Status abfragen

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

**Voraussetzung:** MQTT Broker läuft auf 192.168.4.11

```bash
mosquitto_pub -h 192.168.4.11 -t "watermeter/status" -m "Flow finished"
```

Der Service sollte automatisch eine Messung starten.

### Test 5: Reset Previous Value

```bash
curl -X POST http://localhost:8001/api/reset
```

Setzt die Reverse-Detection zurück (nützlich bei Zählerwechsel).

## 📊 Logs beobachten

Der Service loggt alle wichtigen Events:

```
2026-01-24 14:30:15 - __main__ - INFO - Starting Water Meter Dashboard...
2026-01-24 14:30:15 - watermeter_service - INFO - WatermeterService initialized
2026-01-24 14:30:15 - watermeter_service - INFO - Connecting to MQTT broker 192.168.4.11:1883
2026-01-24 14:30:15 - watermeter_service - INFO - Connected to MQTT broker
2026-01-24 14:30:20 - watermeter_service - INFO - Trigger received - starting processing
2026-01-24 14:30:20 - watermeter_service - INFO - Fetching 7 images from AI-on-the-edge
2026-01-24 14:30:27 - watermeter_service - INFO - Successfully fetched 7/7 images
2026-01-24 14:30:27 - watermeter_service - INFO - Running inference on 7 images
2026-01-24 14:30:28 - watermeter_service - INFO - Inference completed for 7 images
2026-01-24 14:30:28 - watermeter_service - INFO - Calculated total: 123.4567 m³
2026-01-24 14:30:28 - watermeter_service - INFO - ✓ Reading accepted: 123.4567 m³
```

## 🔧 Troubleshooting

### Problem: "No images fetched"

**Ursache:** AI-on-the-edge Device nicht erreichbar

**Lösung:**
```bash
# Teste Verbindung
curl http://192.168.5.136/img_tmp/main_dig1.jpg --output test.jpg

# Falls nicht erreichbar:
# 1. Prüfe IP in config.yaml
# 2. Prüfe, ob Device läuft
# 3. Prüfe Netzwerk
```

### Problem: "MQTT connection failed"

**Ursache:** MQTT Broker nicht erreichbar

**Lösung:**
```bash
# Teste MQTT Broker
mosquitto_pub -h 192.168.4.11 -t test -m hello

# Falls nicht erreichbar:
# 1. Prüfe Broker IP in config.yaml
# 2. Starte MQTT Broker (mosquitto)
# 3. Prüfe Firewall
```

### Problem: "ModuleNotFoundError: No module named 'openvino'"

**Ursache:** Dependencies nicht installiert

**Lösung:**
```bash
pip install -r requirements.txt
```

### Problem: Low Confidence Warnings

**Normal bei:**
- Schlechter Beleuchtung
- Verschmutztem Zähler
- Unklaren Zifferstellungen

**Bilder werden gespeichert in:**
```
watermeter/import/digits/
watermeter/import/arrows/
```

Diese können für Re-Training verwendet werden.

### Problem: Consistency Warnings

**Beispiel:** `dig1=2.5 vs dig2=2 - inkonsistent!`

**Bedeutung:** Der erste Zeiger steht zwischen 2 und 3, aber der zweite zeigt auf 2 (sollte ≥5 sein für Konsistenz)

**Ursachen:**
- Fehlklassifikation
- Zeiger nicht eindeutig

**Aktion:**
- Prüfe Bilder im Dashboard
- Eventuell Modell neu trainieren mit mehr Daten

### Problem: Reverse Detection

**Log:** `Reverse detected: 123.4 → 122.1`

**Ursache:** Wert ist kleiner als vorheriger Wert (unmöglich)

**Lösungen:**
1. Falls Zählerwechsel: Reset mit `curl -X POST http://localhost:8001/api/reset`
2. Falls Fehlklassifikation: Warte auf nächste Messung
3. Prüfe Bilder und Confidence Scores

## 📁 Dateistruktur

```
watermeter/
├── app.py                          # FastAPI Web Application
├── watermeter_service.py          # Core Service (MQTT, Inference, Calculation)
├── inference.py                    # Classifier Definitions (Label Studio)
├── config.yaml                     # Konfiguration
├── requirements.txt                # Python Dependencies
│
├── templates/
│   ├── dashboard.html             # Haupt-Dashboard
│   └── status_fragment.html       # Status-Fragment (HTMX)
│
├── static/
│   └── style.css                  # Dashboard Styling
│
├── import/                        # Low Confidence Images
│   ├── digits/
│   └── arrows/
│
├── digits/
│   └── ov_model/
│       └── model_mobilenetv3_small_100_c11_r144.xml
│
└── arrows/
    └── ov_model/
        └── model_mobilenetv3_small_100_c20_r144.xml
```

## 🔄 Integration mit Node-Red (Migration)

### Schritt 1: Parallel-Betrieb

Beide Systeme parallel laufen lassen und Ergebnisse vergleichen.

### Schritt 2: MQTT Topic ändern

In AI-on-the-edge:
- Ändere MQTT Topic von "watermeter/status" zu "watermeter/status-python"

In config.yaml:
```yaml
mqtt:
  trigger_topic: "watermeter/status-python"
```

### Schritt 3: Node-Red Flow deaktivieren

Wenn Python Service stabil läuft:
1. Node-Red Flow exportieren (Backup)
2. Flow deaktivieren
3. Python Service auf "watermeter/status" umstellen

### Schritt 4: Home Assistant

Der Python Service published direkt via MQTT Discovery. Kein manuelles Setup nötig!

## 🎉 Erfolg!

Wenn alles funktioniert, solltest du sehen:

✅ Dashboard lädt Bilder und zeigt Werte
✅ MQTT Trigger funktioniert
✅ Home Assistant empfängt Updates
✅ Low Confidence Bilder werden gespeichert
✅ Consistency Checks funktionieren

**Bei Fragen oder Problemen:** Prüfe die Logs!

```bash
# Logs in Echtzeit
tail -f /var/log/watermeter.log  # Falls systemd
# oder direkt im Terminal beobachten
```
