# Integrated Model Training

## Übersicht

Integration von Model-Training direkt in die Watermeter-Anwendung. Ermöglicht Training und Benchmarking von custom Modellen über eine Web-UI, ohne manuell Python-Scripts ausführen zu müssen.

---

## 1. Architektur

### 1.1 Model Storage
- **Volume Mount**: Model-Ordner wird als Volume gemountet
  - `/app/models/digits/` für Digit-Modelle
  - `/app/models/arrows/` für Arrow-Modelle
- **Struktur**:
  ```
  /app/models/
  ├── digits/
  │   ├── model_digits_resnext50_32x4d_r128/
  │   │   ├── model_digits_resnext50_32x4d_r128.xml
  │   │   ├── model_digits_resnext50_32x4d_r128.bin
  │   │   ├── metadata.json
  │   │   └── training_plot.png
  │   └── model_digits_efficientnetv2_rw_t_r144_s67/
  │       ├── ...
  │       └── metadata.json
  └── arrows/
      └── model_arrows_efficientnetv2_rw_s_c10_r128_s8820/
          ├── ...
          └── metadata.json
  ```

### 1.2 Training Data Storage
- **Volume Mount**: Training-Daten als Volume gemountet
  - `/training/digits/ground_truth/{0,1,...,9,NAN}/*.jpg` für Digit-Training-Daten
  - `/training/arrows/ground_truth/{0.0,0.1,...,9.9}/*.jpg` für Arrow-Training-Daten
- Neue low-confidence Bilder werden automatisch hinzugefügt (existierendes Feature)

### 1.3 Model Naming Convention
```
model_{type}_{architecture}[_c{classes}][_r{resolution}][_s{seed}]
```
Beispiele:
- `model_digits_resnext50_32x4d_r128` (default, kein Seed)
- `model_arrows_efficientnetv2_rw_s_c10_r128_s8820` (mit Klassen und Seed)

---

## 2. Metadaten

### 2.1 Metadata Format (metadata.json)
Jedes Modell hat eine `metadata.json` Datei mit folgenden Informationen:

```json
{
  "model_type": "digits|arrows",
  "model_name": "resnext50_32x4d",
  "created_at": "2025-01-31T15:30:00Z",
  "training_info": {
    "num_samples": 1500,
    "num_classes": 11,
    "resolution": 128,
    "seed": 42,
    "epochs": 20,
    "batch_size": 16,
    "learning_rate": 0.001,
    "best_val_accuracy": 95.2,
    "best_val_loss": 0.123,
    "training_time_seconds": 450
  },
  "model_info": {
    "num_parameters": 25500000,
    "file_size_mb": 97.5
  },
  "benchmark": {
    "accuracy": 94.8,
    "mean_confidence": 96.5,
    "low_confidence_pct": 2.1,
    "inference_time_ms": 12.5,
    "num_images": 350,
    "tested_at": "2025-01-31T16:00:00Z"
  },
  "status": "active|archived",
  "notes": "Initial training with all available data"
}
```

### 2.2 Model Selection
- Aktives Modell wird in `config.yaml` referenziert
- Dropdown in UI zeigt alle verfügbaren Modelle mit Key-Metriken
- Modelle können als "active" oder "archived" markiert werden

---

## 3. Training UI

### 3.1 Layout (von oben nach unten)
**Route**: `/training`

1. **Dataset Statistics** (kompakt) - Klassenverteilung für Digits und Arrows
2. **Training Form** - Konfiguration + Start/Queue Button
3. **Training Queue** - Liste der wartenden Jobs (nur sichtbar wenn Queue nicht leer)
4. **Training Progress** - Fortschrittsanzeige des aktiven Jobs
5. **Trained Models** - Tabelle aller Modelle mit All/Digits/Arrows Tabs
6. **Benchmark Progress** - Fortschrittsanzeige des aktiven Benchmarks
7. **Benchmark Results** - Persistente Ergebnisse mit All/Digits/Arrows Tabs
8. **Log Output** - Training- und Benchmark-Logs (nur sichtbar wenn Jobs aktiv)

### 3.2 Start Training
**Form-Felder**:
- **Model Type**: Dropdown (digits/arrows)
- **Architecture**: Dropdown mit 3 Gruppen, sortiert nach Parameteranzahl:
  - **Lightweight**: MobileNetV3 Small, MobileOne S0, EfficientNet B0, MobileNetV3 Large, MobileOne S1, ResNet18
  - **Medium**: GhostNet, EdgeNeXt Small, EfficientNet B2, EfficientNetV2-T, DenseNet121, ResNet34
  - **Heavy**: DenseNet169, EfficientNetV2-S, ResNeXt50 32x4d (default), ConvNeXt Tiny
- **Resolution**: Dropdown (96, 128, 144, 160, 192)
- **Seeds**: Text (kommasepariert, z.B. "42, 67, 69")
- **Epochs**: Number (default: 20)
- **Notes**: Textarea (optional)
- **Auto-benchmark after training**: Checkbox (default: checked)

**Start Button**:
- Wenn kein Training läuft: "Start Training" - startet sofort
- Wenn Training läuft: "Add to Queue" - fügt zur Warteschlange hinzu

### 3.3 Training Queue
- Zeigt alle wartenden Trainings-Jobs
- Pro Eintrag: Architektur, Typ, Resolution, Seeds
- "Remove" Button pro Eintrag
- "Clear Queue" Button zum Leeren der gesamten Queue
- Wird automatisch per Polling aktualisiert (alle 2 Sekunden)

### 3.4 Training Progress
Während Training läuft:
- Progress Bar: Aktuelle Epoche / Gesamt-Epochen
- Live-Updates: Aktuelle Epoche, Training Loss, Validation Accuracy
- Log-Output (scrollable, Epoch-Zeilen werden gefiltert um Redundanz zu vermeiden)
- "Cancel Training" Button (stoppt auch Queue und Auto-Benchmark)

### 3.5 Model Management
- **Model List**: Tabelle mit All/Digits/Arrows Tabs
  - Columns: Name, Architecture, Resolution, Accuracy, Date, Actions
- **Actions**:
  - "Activate": Modell als aktives Modell setzen (updated config.yaml, hot-reload)
  - "Benchmark": Benchmark gegen Ground-Truth Daten laufen lassen
  - "Delete": Modell löschen (mit Bestätigung, nicht für aktive Modelle)

### 3.6 Benchmark Results (Persistent)
- **Immer sichtbar** (nicht nur während Benchmark läuft)
- **Tabs**: All / Digits / Arrows
- **Datenquelle**: Wird aus `metadata.json` aller Modelle geladen (persistiert über Neustarts)
- **Tabellen-Columns**: Model, Type, Accuracy, Confidence, Low Conf, Inference, Images, Date, Actions
- **Actions**: "Delete" Button (nur für nicht-aktive Modelle), "Active" Badge für aktive Modelle
- **Sortierung**: Nach Accuracy absteigend, bester Eintrag hervorgehoben

---

## 4. Training Backend

### 4.1 Training Pipeline
1. Daten-Validierung (genug Bilder pro Klasse?)
2. Model erstellen (PyTorch + timm)
3. Training Loop mit Progress-Callbacks
4. ONNX Export
5. OpenVINO Conversion
6. Metadaten + Training-Plot speichern
7. Nächsten Job aus Queue starten (wenn vorhanden)
8. Auto-Benchmark ausführen (wenn aktiviert und Queue leer)

### 4.2 Training Manager

```python
class TrainingManager:
    def __init__(self):
        self.active_training_job = None
        self.active_benchmark_job = None
        self.training_queue: List[Dict] = []
        self._auto_benchmark_pending: List[tuple] = []
        self._queue_lock = threading.Lock()

    def start_training(self, config) -> Dict:
        """Start training or queue if busy.
        Returns: {'job_id': str|None, 'queued': bool, 'queue_position': int}"""

    def cancel_training(self, clear_queue=True):
        """Cancel running training. clear_queue=True also clears queue + auto-benchmark."""

    def get_queue(self) -> List[Dict]:
        """Get current training queue."""

    def remove_from_queue(self, index: int) -> bool:
        """Remove specific item from queue."""

    def clear_queue(self):
        """Clear entire training queue and auto-benchmark pending."""
```

### 4.3 Training Queue
- Wenn Training läuft und neuer Job submitted wird → Job wird in Queue eingefügt
- Nach Abschluss eines Trainings → nächster Job wird automatisch gestartet
- Queue kann über UI verwaltet werden (einzelne Items entfernen, komplett leeren)
- Cancel bricht aktuelles Training ab UND leert die Queue

### 4.4 Auto-Benchmark
- Checkbox "Auto-benchmark after training" im Form (default: an)
- Model-IDs werden während Training gesammelt
- Wenn alle Queue-Jobs abgearbeitet sind → Benchmarks werden sequentiell ausgeführt
- Läuft in separatem Daemon-Thread
- Nutzt existierende `_run_benchmark()` Methode

### 4.5 Parallelität
- **Ein Training** gleichzeitig (weitere werden gequeued)
- **Ein Benchmark** gleichzeitig
- Training und Benchmark können parallel laufen
- Auto-Benchmark wartet bis Queue komplett abgearbeitet

---

## 5. Benchmark Integration

### 5.1 Benchmark Process
- Verwendet OpenVINO für Inferenz
- Läuft gegen alle Ground-Truth Daten
- Output: Overall Accuracy, Per-Class Accuracy, Mean Confidence, Low-Confidence %, Inference Time

### 5.2 Benchmark Trigger
- **Manuell**: "Benchmark" Button in der Model-Tabelle
- **Automatisch**: Nach Training wenn "Auto-benchmark" aktiviert (nach Queue-Abarbeitung)

### 5.3 Persistente Ergebnisse
- Benchmark-Ergebnisse werden in `metadata.json` des jeweiligen Modells gespeichert
- Beim Laden der Seite werden alle Ergebnisse aus den Model-Metadaten extrahiert
- Ergebnisse bleiben über Container-Neustarts erhalten (Volume Mount)

---

## 6. REST API Endpoints

### Training
- `GET /api/training/status` - Training/Benchmark Status + Queue + Auto-Benchmark Pending
- `POST /api/training/start` - Training starten oder in Queue einreihen
- `POST /api/training/cancel?clear_queue=true` - Training abbrechen (optional Queue leeren)
- `GET /api/training/logs/{job_id}` - Training Logs

### Training Queue
- `DELETE /api/training/queue/{index}` - Einzelnen Queue-Eintrag entfernen
- `DELETE /api/training/queue` - Gesamte Queue leeren

### Models
- `GET /api/models?type={digits|arrows}` - Alle Modelle auflisten
- `POST /api/models/{type}/{id}/activate` - Modell aktivieren
- `DELETE /api/models/{type}/{id}` - Modell löschen
- `POST /api/models/{type}/{id}/benchmark` - Benchmark starten

### Benchmark
- `POST /api/benchmark/cancel` - Benchmark abbrechen

### Data
- `GET /api/training-data/stats` - Training-Daten Statistiken

---

## 7. Implementation Roadmap

### ✅ Phase 1: Backend Foundation (Completed)
- `training_manager.py` - Training orchestration mit Queue + Auto-Benchmark
- `model_manager.py` - Model metadata & file management
- API Endpoints in `app.py`
- Updated `docker-entrypoint.sh` - Default model copy mit metadata.json Erzeugung
- Updated `Dockerfile` & `docker-compose.yml` - Volume mounts

### ✅ Phase 2: Training UI (Completed)
- `templates/training.html` - Vollständige Training-UI (~1700 Zeilen)
- Route `/training` in `app.py`
- JavaScript für Polling, Form-Handling, Model-Verwaltung

### ✅ Phase 3: Benchmark Integration (Completed)
- `_execute_benchmark()` - Echte Benchmark-Logik mit OpenVINO
- Benchmark Progress Section in UI
- Persistente Benchmark Results Section mit Tabs

### ✅ Phase 4: Queue & Auto-Benchmark (Completed)
- Training Queue Backend (start → queue wenn busy, auto-chain)
- Training Queue UI (Anzeige, Remove, Clear)
- Auto-Benchmark nach Queue-Abarbeitung
- Benchmark Results Rework (persistent, Tabs, Delete-Button)
- Erweiterte Architektur-Auswahl (18 Modelle in 3 Gruppen)
- UI Layout Rework (kompakte Stats, Queue-Section, Log-Section am Ende)

---

## 8. Technische Entscheidungen

### Storage & Architektur
- **Docker**: Training läuft im Container (CPU)
- **Model Storage**: Modelle werden persistiert über Volume Mount (`/app/models/`)
  - Startup: wenn leer → Default-Modelle aus `digits/selected/` und `arrows/selected/` kopieren
- **Training Data**: Volume Mount für `/training/` (digits/arrows jeweils mit `ground_truth/`)
- **Model Files**: XML/BIN + metadata.json + training_plot.png
- **Cleanup**: Manuelles Aufräumen über UI (Delete-Button in Model-Tabelle und Benchmark-Tabelle)

### Training
- **Parallel Execution**: Training und Inference gleichzeitig OK
  - Training auf CPU (torch device='cpu')
  - Inference auf GPU (OpenVINO device='AUTO')
- **Training Queue**: Mehrere Trainings können eingereicht werden, werden sequentiell abgearbeitet
- **Auto-Benchmark**: Optional nach Training (wenn alle Queue-Jobs fertig)
- **Seed-based Training**: Seeds als kommaseparierte Liste (z.B. `42, 67, 69`)
- **Arrows Step-Size**: Dropdown mit 1.0, 0.5, 0.2, 0.1
- **Batch-Training**: Parameter Ranges (cross-product aller Kombinationen)
- **Error Handling**: Error log anzeigen, Auto-Cleanup von failed trainings
- **Abbruch**: Cancel stoppt Training + leert Queue + cancelt Auto-Benchmark

### Verfügbare Architekturen
| Gruppe | Architektur | timm Name | ~Parameter |
|--------|------------|-----------|------------|
| Lightweight | MobileNetV3 Small | mobilenetv3_small_100 | 2.5M |
| Lightweight | MobileOne S0 | mobileone_s0 | 2.1M |
| Lightweight | EfficientNet B0 | efficientnet_b0 | 5.3M |
| Lightweight | MobileNetV3 Large | mobilenetv3_large_100 | 5.4M |
| Lightweight | MobileOne S1 | mobileone_s1 | 4.8M |
| Lightweight | ResNet18 | resnet18 | 11.7M |
| Medium | GhostNet | ghostnet_100 | 5.2M |
| Medium | EdgeNeXt Small | edgenext_small | 5.6M |
| Medium | EfficientNet B2 | efficientnet_b2 | 9.1M |
| Medium | EfficientNetV2-T | efficientnetv2_rw_t | 13.7M |
| Medium | DenseNet121 | densenet121 | 8.0M |
| Medium | ResNet34 | resnet34 | 21.8M |
| Heavy | DenseNet169 | densenet169 | 14.1M |
| Heavy | EfficientNetV2-S | efficientnetv2_rw_s | 24.0M |
| Heavy | ResNeXt50 32x4d | resnext50_32x4d | 25.0M |
| Heavy | ConvNeXt Tiny | convnext_tiny | 28.6M |

### Model Management
- **Hot-Reload**: Modellwechsel ohne App-Restart (Model-Lock während Reload)
- **Benchmark Persistenz**: Ergebnisse in metadata.json gespeichert, über Neustarts verfügbar

### UI/UX
- **Framework**: FastAPI + Jinja2 Templates (kein React/SPA)
- **Progress Updates**: REST API Polling (alle 2 Sekunden)
- **Log-Filterung**: Epoch/Loss/Accuracy Zeilen werden aus Logs gefiltert (redundant mit Progress-Bars)
- **Layout**: Kompakte Stats oben, Form + Queue Mitte, Models + Benchmark unten, Logs ganz unten

### Nicht implementiert
- ❌ Rollback zu vorherigem Modell
- ❌ A/B Testing
- ❌ CPU/GPU Auswahl in UI
- ❌ Multi-GPU Support
- ❌ Auto-Retry bei Fehlern
- ❌ Model Comparison Side-by-Side
