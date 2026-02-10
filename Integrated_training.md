# Integrated Model Training

## Übersicht

Integration von Model-Training direkt in die Watermeter-Anwendung. Ermöglicht Training und Benchmarking von custom Modellen über eine Web-UI, ohne manuell Python-Scripts ausführen zu müssen.

---

## 1. Architektur

### 1.1 Model Storage
- **Volume Mount**: Model-Ordner wird als Volume gemountet
  - `/models/digits/` für Digit-Modelle
  - `/models/arrows/` für Arrow-Modelle
- **Struktur**:
  ```
  /models/
  ├── digits/
  │   ├── model_1/
  │   │   ├── model.xml
  │   │   ├── model.bin
  │   │   └── metadata.json
  │   └── model_2/
  │       ├── model.xml
  │       ├── model.bin
  │       └── metadata.json
  └── arrows/
      └── model_1/
          ├── model.xml
          ├── model.bin
          └── metadata.json
  ```

### 1.2 Training Data Storage
- **Volume Mount**: Training-Daten als Volume gemountet
  - `/training/digits/ground_truth/` für Digit-Training-Daten
  - `/training/arrows/ground_truth/` für Arrow-Training-Daten
- Neue low-confidence Bilder werden automatisch hinzugefügt (existierendes Feature)

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
  "benchmark_info": {
    "accuracy": 94.8,
    "mean_confidence": 96.5,
    "low_confidence_pct": 2.1,
    "inference_time_ms": 12.5,
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

### 3.1 Training Dashboard
**Route**: `/training`

**Features**:
- Übersicht über verfügbare Training-Daten
  - Anzahl Bilder pro Klasse (digits/arrows)
  - Letzte hinzugefügte Bilder (timestamp)
  - Klassenverteilung (Balkendiagramm)
- Liste aller trainierten Modelle
  - Name, Accuracy, Confidence, Training-Datum
  - "Active" Badge für aktuell verwendetes Modell
- Training-Status (idle/running)
- Benchmark-Status (idle/running)

### 3.2 Start Training
**Form-Felder**:
- **Model Type**: Dropdown (digits/arrows)
- **Architecture**: Dropdown (resnext50_32x4d, efficientnetv2_rw_s, etc.)
- **Resolution**: Dropdown (96, 128, 144, 160, 192)
- **Seed**: Number (default: 42)
- **Epochs**: Number (default: 20)
- **Model Name**: Text (optional, auto-generated if empty)
- **Notes**: Textarea (optional)

**Button**: "Start Training"

### 3.3 Training Progress
Während Training läuft:
- Progress Bar: Aktuelle Epoche / Gesamt-Epochen
- Live-Updates:
  - Aktuelle Epoche
  - Training Loss
  - Validation Accuracy
  - Geschätzte verbleibende Zeit
- Log-Output (scrollable)
- "Cancel Training" Button

### 3.4 Model Management
- **Model List**: Tabelle mit allen Modellen
  - Columns: Name, Type, Accuracy, Confidence, Date, Status, Actions
- **Actions**:
  - "Set Active": Modell als aktives Modell setzen (updated config.yaml)
  - "Benchmark": Benchmark gegen Test-Daten laufen lassen
  - "Archive": Modell als archiviert markieren
  - "Delete": Modell löschen (mit Bestätigung)
  - "View Details": Detailansicht mit allen Metadaten

---

## 4. Training Backend

### 4.1 Training Process
- **Async Execution**: Training läuft als Background-Task (Threading oder multiprocessing)
- **Progress Updates**:
  - WebSocket oder Server-Sent Events (SSE) für Live-Updates
  - Oder Polling via REST API
- **Pipeline**:
  1. Daten-Validierung (genug Bilder pro Klasse?)
  2. Model erstellen (timm)
  3. Training Loop (wie in train_*.py)
  4. ONNX Export
  5. OpenVINO Conversion
  6. Metadaten speichern
  7. Optional: Auto-Benchmark

### 4.2 Training Manager
Python-Klasse zur Verwaltung von Training-Prozessen:

```python
class TrainingManager:
    def __init__(self):
        self.active_training = None  # Only one training at a time
        self.active_benchmark = None  # Only one benchmark at a time

    def start_training(self, config: TrainingConfig) -> str:
        """Start training job, returns job_id"""

    def get_training_status(self, job_id: str) -> TrainingStatus:
        """Get current training status"""

    def cancel_training(self, job_id: str):
        """Cancel running training"""

    def get_training_log(self, job_id: str) -> List[str]:
        """Get training log output"""
```

### 4.3 Parallelität
- **Ein Training** gleichzeitig erlaubt
- **Ein Benchmark** gleichzeitig erlaubt
- Training und Benchmark können parallel laufen
- Wenn Training/Benchmark läuft, werden entsprechende Buttons disabled

---

## 5. Benchmark Integration

### 5.1 Benchmark Process
- Verwendet existierenden Code aus `benchmark_*.py`
- Läuft gegen alle Ground-Truth Daten
- Output:
  - Overall Accuracy
  - Per-Class Accuracy
  - Mean Confidence
  - Low-Confidence Percentage
  - Inference Time

### 5.2 Benchmark UI
- **Manual Trigger**: Button "Run Benchmark" bei jedem Modell
- **Auto-Benchmark**: Optional nach Training
- **Progress Display**:
  - Current Class / Total Classes
  - Processed Images / Total Images
  - Estimated Time Remaining

### 5.3 Model Comparison
- Optional: Benchmark neues Modell vs. aktuelles Modell
- Side-by-side Vergleich der Metriken
- Empfehlung: "Use new model" / "Keep current model"

---

## 6. REST API Endpoints

### Training
- `GET /api/training/status` - Get training/benchmark status
- `POST /api/training/start` - Start new training
- `POST /api/training/cancel` - Cancel running training
- `GET /api/training/logs/:job_id` - Get training logs
- `GET /api/training/progress/:job_id` - Get training progress

### Models
- `GET /api/models` - List all models
- `GET /api/models/:id` - Get model details
- `POST /api/models/:id/activate` - Set model as active
- `POST /api/models/:id/archive` - Archive model
- `DELETE /api/models/:id` - Delete model
- `POST /api/models/:id/benchmark` - Start benchmark

### Data
- `GET /api/training-data/stats` - Get training data statistics

---

## 7. Implementation Roadmap

### ✅ Phase 1: Backend Foundation (Completed)
**Ziele:**
1. ✅ Model metadata structure & management (`ModelManager` class)
2. ⚠️ Training execution backend (`TrainingManager` class) - **Struktur fertig, aber `_execute_training()` ist noch Placeholder!**
3. ✅ REST API endpoints für Training & Models
4. ✅ Model hot-reload functionality
5. ✅ Docker volume mount setup & default model initialization

**Deliverables:**
- `training_manager.py` - Training orchestration
- `model_manager.py` - Model metadata & file management
- API Endpoints in `app.py`:
  - `GET /api/training/status`
  - `POST /api/training/start`
  - `POST /api/training/cancel`
  - `GET /api/training/progress/:job_id`
  - `GET /api/models`
  - `POST /api/models/:id/activate`
  - `DELETE /api/models/:id`
- Updated `docker-entrypoint.sh` - Default model copy logic
- Updated `Dockerfile` & `docker-compose.yml` - Volume mounts

**✅ Training-Execution - IMPLEMENTIERT**
Die `_execute_training()` Methode in `training_manager.py` ist vollständig implementiert:
- PyTorch + timm für Model-Training
- Progress-Callbacks für Live-Updates (Epoch, Loss, Val Accuracy)
- ONNX + OpenVINO Export
- Model-Speicherung in `/app/models/{type}/{model_id}/` mit metadata.json
- Training-Plot als PNG
- Für Arrows: automatische Dataset-Subsampling basierend auf step_size

### ✅ Phase 2: Training UI (Completed)
1. ✅ Training dashboard page (`/training`)
2. ✅ Start training form (with batch parameter ranges)
3. ✅ Progress display (polling-based)
4. ✅ Model list with activate/delete actions
5. ✅ Training data statistics

**Deliverables:**
- `templates/training.html` - Vollständige Training-UI (~1300 Zeilen)
- Route `/training` in `app.py`
- JavaScript für Polling, Form-Handling, Model-Verwaltung

### ✅ Phase 3: Benchmark Integration (Completed)
1. ✅ Benchmark execution backend (`_execute_benchmark()` in training_manager.py)
2. ✅ Benchmark API endpoints (`POST /api/models/{type}/{id}/benchmark`, `POST /api/benchmark/cancel`)
3. ✅ Benchmark UI & progress display (in training.html)
4. ❌ Model comparison view - nicht implementiert (als "nice-to-have" eingestuft)

**Deliverables:**
- `_execute_benchmark()` - Echte Benchmark-Logik mit OpenVINO
- `_generate_arrow_classes()`, `_round_to_arrow_class()`, `_collect_benchmark_images()` - Helper-Funktionen
- Benchmark Progress Section in UI
- Benchmark Results Section mit Per-Class Accuracy Details
- Benchmark Button in Model-Tabelle

### Phase 4: Polish & Optimization (TODO)
1. Training logs view (bereits teilweise in Progress-Section)
2. Advanced visualizations (training curves)
3. Model search/filter
4. Performance optimizations

### ⚠️ Wichtig: Training-Execution fehlt noch!
Bevor Training funktioniert, muss `_execute_training()` implementiert werden. Dies erfordert:
1. Refactoring von `train_digits.py` und `train_arrows.py` für programmatische Nutzung
2. Integration in `training_manager.py`
3. Progress-Callbacks für Epoch/Loss/Accuracy Updates
4. Model-Export nach `/app/models/{type}/{model_id}/`

---

## 8. Technische Entscheidungen

### Storage & Architektur
- **Docker**: Training läuft im Container
- **Model Storage**: Modelle werden persistiert über Volume Mount
  - `/app/models/digits/` und `/app/models/arrows/`
  - Startup: wenn leer → Default-Modelle aus `digits/selected/` und `arrows/selected/` kopieren
- **Training Data**: Volume Mount für `/training/` (digits/arrows jeweils mit `input/` und `ground_truth/`)
- **Model Files**: Nur XML/BIN + metadata.json speichern (keine ONNX)
- **Training Plots**: Im Model-Ordner `/app/models/<name>/`
- **Cleanup**: Manuelles Aufräumen über UI (kein Auto-Cleanup)

### Training
- **Parallel Execution**: Training und Inference gleichzeitig OK
  - Training auf CPU (torch device='cpu')
  - Inference auf GPU (OpenVINO device='AUTO')
  - Keine weitere Isolation nötig
- **Seed-based Training**: Ja, Seeds als Liste eingeben (z.B. `[42, 67, 69]`)
- **Arrows Step-Size**: Ja, Dropdown mit 1.0, 0.5, 0.2, 0.1
- **Batch-Training**: Parameter Ranges (cross-product aller Kombinationen)
  - Seeds: `[42, 67]` × Resolutions: `[128, 144]` × Models: `[resnext50]` = 4 Trainings
- **Error Handling**: Error log anzeigen, Auto-Cleanup von failed trainings
- **Abbruch**: Best epoch wird automatisch gespeichert (wie aktuell)

### Model Management
- **Hot-Reload**: Ja, Modellwechsel ohne App-Restart
  - Model-Lock während Reload
  - Inferenz-Requests warten
  - Maintenance Mode Message im UI: "Switching model..."
- **Rollback**: ❌ Nicht implementieren (verworfen)

### Benchmark
- **Execution**: Optional manuell nach Training
- **Results**: Nur aktuelle Ergebnisse anzeigen (keine Persistenz)
- **Test Data**: Komplette ground_truth (kein Test-Set Split)

### UI/UX
- **Framework**: Flask Templates beibehalten (kein React)
- **Progress Updates**: REST API Polling (alle 1-2 Sekunden)
- **Labeling**: Existierendes `/label` Endpoint nutzen
- **Notifications**: Nur im UI (keine Email)

### Nicht implementiert
- ❌ Rollback zu vorherigem Modell
- ❌ A/B Testing
- ❌ Backup/Restore
- ❌ CPU/GPU Auswahl in UI
- ❌ Multi-GPU Support
- ❌ Auto-Retry bei Fehlern
