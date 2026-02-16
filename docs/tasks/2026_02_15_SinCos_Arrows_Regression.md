# Sin/Cos Regression fuer Arrows-Modell

**Datum**: 2026-02-15
**Status**: Konzept fertig — bereit zur Implementierung

---

## 1. Problemstellung

### Der Bug im aktuellen System

Das Arrows-Modell sagt den Zeigerstand eines Wasserzaehler-Zifferblatts vorher: **0.0 bis 9.9** (100 diskrete 0.1er-Schritte, eine volle Umdrehung).

Der aktuelle Sigmoid-Regressor (`training_mode="continuous"`) mappt Zielwerte auf `value/10 → [0, 1)`. Damit gilt:

| Wert | Sigmoid-Target | Physische Distanz zu 0.0 | Target-Distanz zu 0.0 |
|------|---------------|--------------------------|----------------------|
| 0.1  | 0.01          | 0.1 (nah)                | 0.01 (nah) ✓        |
| 5.0  | 0.50          | 5.0 (weit)               | 0.50 (weit) ✓       |
| 9.9  | 0.99          | **0.1 (nah!)**           | **0.99 (maximal!)** ✗ |

**Das Modell sieht 9.9 und 0.0 als die am weitesten entfernten Werte**, obwohl sie physisch direkt nebeneinander liegen. Das fuehrt zu:
- Schlechter Genauigkeit nahe 0.0 und 9.9
- Instabiles Training an den "Raendern"
- Verzerrte Vorhersagen, die den Randbereich meiden

### Visualisierung

```
Sigmoid-Sicht:     0.0 ────────── 5.0 ────────── 9.9
                    |                               |
                    └── Distanz = MAXIMAL ───────────┘  ← FALSCH!

Realitaet (Zifferblatt ist ein Kreis):

                         0.0
                     9.5     0.5
                   9.0         1.0
                  8.5           1.5
                   8.0         2.0
                     7.5     2.5
                         5.0

                  9.9 → 0.0 = nur 0.1 Schritt   ← RICHTIG!
```

---

## 2. Loesung: Sin/Cos-Encoding

### Grundidee

Den zirkulaeren Wert `v ∈ [0, 10)` auf einen **Einheitskreis** abbilden:

```
θ = 2π · v / 10

Target 1: sin(θ)
Target 2: cos(θ)
```

### Warum das den Bug loest

| Wert | sin(θ)  | cos(θ)  | Eukl. Distanz zu (sin(0), cos(0)) |
|------|---------|---------|-----------------------------------|
| 0.0  |  0.000  |  1.000  | 0.000 (identisch) ✓              |
| 0.1  |  0.063  |  0.998  | 0.063 (nah) ✓                    |
| 5.0  |  0.000  | -1.000  | 2.000 (maximal) ✓                |
| 9.9  | -0.063  |  0.998  | **0.063 (nah!)** ✓               |

**Der Uebergang 9.9 → 0.0 ist jetzt genauso klein wie 0.0 → 0.1.** Problem geloest.

### Rueckabbildung

```python
angle = atan2(sin_pred, cos_pred)    # → [-π, π]
if angle < 0: angle += 2π            # → [0, 2π)
dial_value = angle * 10 / (2π)       # → [0.0, 10.0)
dial_value = round(dial_value, 1)    # → 0.0, 0.1, ..., 9.9
```

---

## 3. Architektur-Entscheidungen

### 3.1 Model Head: `num_classes=2` (timm Default)

```
Input (Bild 128×128)
  → EfficientNetV2-S Backbone (pretrained)
    → GlobalAvgPool
      → Dropout(0.2)
        → Linear(1280, 2)   ← [sin_raw, cos_raw]
```

**Kein Custom Head.** timm's Default-Head (Pool → Dropout → Linear) reicht fuer unser kleines Dataset (1794 Bilder). Ein zusaetzliches Hidden Layer erhoeht Overfitting-Gefahr.

**Keine Aktivierungsfunktion** am Output — die Rohwerte werden direkt als sin/cos interpretiert.

**ACHTUNG:** Der bestehende `"continuous"`-Pfad wendet `torch.sigmoid(outputs)` an (training_manager.py L606). Fuer sincos darf **kein Sigmoid** verwendet werden! Copy-Paste-Fehler hier fuehrt zu einem schwer erkennbaren Bug: Training konvergiert, aber Outputs liegen in (0,1) statt (-1,1) → atan2 produziert nur Werte im 1. Quadranten.

### 3.2 Loss: MSE auf (sin, cos)

```python
criterion = nn.MSELoss()
loss = criterion(model_output, sincos_target)  # Shapes: (batch, 2)
```

**Warum nur MSE, nichts Kombiniertes:**
- Sin/cos-Encoding loest den Wraparound bereits. Der Loss ist zweitrangig (Zhou et al. CVPR 2019: "Representation matters more than loss function").
- Kein zusaetzlicher Hyperparameter (λ fuer Combined Loss).
- Bei 1794 Bildern ist jede Komplexitaet ein Overfitting-Risiko.

### 3.3 Keine Normalisierung im Netz

Die Model-Outputs werden **nicht** auf den Einheitskreis normalisiert.

**Begruendung:**
- `atan2` ist **scale-invariant**: `atan2(k·sin, k·cos) = atan2(sin, cos)` fuer jedes k>0
- Einfacherer ONNX-Export (keine Division, keine Quantisierungsartefakte)
- Ein trainiertes Netz konvergiert nie zu (0,0)-Outputs — das waere maximal weit von allen Targets

**Fallback im Post-Processing:**
```python
if sqrt(sin² + cos²) < 1e-6:
    confidence = 0.0  # Signal: Vorhersage unzuverlaessig
```

### 3.4 Confidence-Schaetzung

Die **Norm des Output-Vektors** dient als Confidence-Proxy:
- Norm > 0.8 → Modell ist ueberzeugt
- Norm < 0.3 → Modell ist unsicher

```python
confidence = min(1.0, norm / 1.0)
```

Dies ersetzt die bisherige Sigmoid-Heuristik (`|sigmoid - 0.5| * 2`).

---

## 4. Training-Modus als UI-Parameter

### Konzept

Der `training_mode` wird **nicht** in der config.yaml gespeichert, sondern beim Start eines Trainings als Parameter uebergeben. So kann der User bei jedem Training frei waehlen, ohne die Config zu aendern.

### Terminologie (Code-konsistent!)

Der bestehende Code verwendet `"discrete"` und `"continuous"` — NICHT `"classification"` und `"regression"`. Wir fuegen `"sincos"` als dritten Wert hinzu:

| Code-Wert | UI-Label | Beschreibung |
|-----------|----------|-------------|
| `"discrete"` | Klassifikation | 100 Klassen, CrossEntropy, argmax |
| `"continuous"` | Regression (Legacy) | 1 Output, Sigmoid, MSE — **Wraparound-Bug** |
| `"sincos"` | **Sin/Cos** | 2 Outputs, MSE, atan2 — **empfohlen** |

### UI-Aenderungen

**Training-Formular** (`training.html`):
```html
<label>Training-Modus (Arrows)</label>
<select name="training_mode" id="training-mode">
  <option value="sincos" selected>Sin/Cos (empfohlen)</option>
  <option value="discrete">Klassifikation</option>
  <option value="continuous">Regression (Legacy)</option>
</select>
```

- Dropdown wird **nur fuer `model_type=arrows`** angezeigt (digits hat kein Wraparound-Problem)
- Default: `"sincos"`
- Bei `model_type=digits` wird `training_mode` nicht gesendet (bleibt `"discrete"`)

### API-Aenderung

`POST /api/training/start` erhaelt `training_mode` im Request Body:

```json
{
  "model_type": "arrows",
  "architecture": "efficientnetv2_rw_s",
  "training_mode": "sincos",
  "epochs": 30,
  "seed": 42
}
```

**Validierung** in `routes/training.py → validate_training_mode()`:
- Erlaubte Werte: `"discrete"`, `"continuous"`, `"sincos"`
- `"sincos"` nur fuer `model_type=arrows` (digits unterstuetzt nur `"discrete"`)
- Default fuer arrows: `"sincos"`, Default fuer digits: `"discrete"`

### Datenfluss

```
UI (Dropdown) → POST /api/training/start {training_mode: "sincos"}
  → routes/training.py: validate_training_mode()
    → training_manager.start_training(config)
      → _execute_training(training_mode="sincos")
        → num_classes=2, MSELoss, kein Sigmoid
        → metadata.json: {"training_mode": "sincos"}
```

Bei Inference liest `_detect_training_mode()` den Wert aus `metadata.json` — der Mode ist also **im Modell gespeichert**, nicht in der Config.

### JS-Aenderung

`startTraining()` in `training.js` muss `training_mode` aus dem Dropdown lesen und mitsenden:

```javascript
const config = {
    model_type: modelType,
    architecture: architecture,
    training_mode: document.getElementById('training-mode')?.value || 'discrete',
    epochs: epochs,
    seed: seed
};
```

---

## 5. Training

### Hyperparameter

| Parameter | Wert | Begruendung |
|-----------|------|-------------|
| Optimizer | Adam | Standard, kein Tuning noetig |
| Learning Rate | 1e-3 | Standard fuer timm pretrained |
| LR Schedule | CosineAnnealingLR | Besser als konstant, kein extra HP |
| Warmup | 3 Epochen linear (1e-5 → 1e-3) | Schuetzt pretrained Features |
| Batch Size | 16 | ~90 Batches/Epoch bei 1435 Train Samples |
| Epochs | 30 | Regression braucht mehr als Classification |
| Weight Decay | 1e-4 | Leichte Regularisierung |
| Early Stopping | Patience=8 auf Val Angular Error | Verhindert Overfitting |
| Val Split | 20% | Stratifiziert nach Dial-Wert |

**Hinweis:** LR Schedule, Warmup und Early Stopping existieren im aktuellen Code NICHT — muessen neu implementiert werden.

### Balanced Sampling

Dataset hat 9-29 Bilder pro Klasse (3.2× Imbalance). Loesung: `WeightedRandomSampler` — unterrepraesentierte Positionen werden oefter gesampelt.

**Achtung:** `WeightedRandomSampler` und `shuffle=True` sind mutually exclusive in PyTorch. Der DataLoader muss `shuffle=False` verwenden wenn ein Sampler gesetzt ist.

### Stratified Split fuer Sin/Cos

Die bestehende `stratified_split_regression()` gruppiert nach dem skalaren Target (`f"{target:.4f}"`). Fuer sincos ist der Target ein 2D-Vektor `(sin, cos)`. **Loesung:** Den Original-Dial-Wert als drittes Element in `samples` speichern und zum Stratifizieren verwenden:

```python
# SinCosArrowDataset.samples:
# [(img_path, sin_target, cos_target, dial_value), ...]
#                                      ↑ fuer Stratifizierung
```

### Evaluations-Metriken

```python
# Zirkulaere Differenz in Dial-Einheiten [0.0, 5.0]
diff = atan2(sin(pred - target), cos(pred - target))
dial_error = abs(diff) * 10 / (2π)
```

| Metrik | Beschreibung |
|--------|-------------|
| **MAE_dial** | Mittlerer Fehler in Dial-Einheiten |
| **Within ±0.1** | % Vorhersagen mit < 0.1 Fehler (exakt richtig) |
| **Within ±0.5** | % Vorhersagen mit < 0.5 Fehler (tolerant) |
| **Max Error** | Worst-Case Fehler |

**Achtung:** Das Epoch-Log-Filter-Regex in `training.js` (L609) matched nur `Val Acc:`-Pattern. Fuer sincos muessen neue Patterns (`MAE=`, `W±0.1=`) ebenfalls matched werden.

---

## 6. Deployment-Pipeline

### ONNX/OpenVINO

```
ONNX-Modell: Input (1,3,128,128) → Output (1,2)  ← [sin_raw, cos_raw]
```

**atan2 ist NICHT im ONNX-Modell** — kein nativer atan2-Op in ONNX. Die gesamte Rueckrechnung passiert in Python:

```python
# OpenVINO Inference
result = compiled_model([input_tensor])[0]  # Shape: (1, 2)
sin_raw, cos_raw = result[0]

# Post-Processing (Python, FP64)
angle = math.atan2(sin_raw, cos_raw)
if angle < 0: angle += 2 * math.pi
dial = round(angle * 10 / (2 * math.pi), 1)
if dial >= 10.0: dial = 0.0
```

### Metadata

```json
{
    "training_mode": "sincos",
    "model_type": "arrows",
    "architecture": "efficientnetv2_rw_s",
    "resolution": 128,
    "num_outputs": 2,
    "output_format": "sin_cos",
    "encoding": "angle = value * 2pi / 10"
}
```

### Modell-Erkennung

`inference.py → _detect_training_mode()` erkennt den Modus anhand `metadata.json → training_mode`:
- `"discrete"` → Classifier (Softmax + argmax, 100 Klassen)
- `"continuous"` → Regressor (Sigmoid × 10, 1 Output) — Legacy
- `"sincos"` → SinCosRegressor (atan2 Post-Processing, 2 Outputs) — **NEU**

---

## 7. Koexistenz mit bestehenden Modi

Alle drei Training-Modi bleiben verfuegbar:

| Modus | Code-Wert | Outputs | Loss | Post-Processing | Wraparound |
|-------|-----------|---------|------|-----------------|------------|
| Klassifikation | `"discrete"` | 100 | CrossEntropy | argmax | Nein |
| Regression | `"continuous"` | 1 | MSE(sigmoid) | ×10 | **Bug!** |
| **Sin/Cos** | **`"sincos"`** | **2** | **MSE** | **atan2** | **Geloest** |

Der User waehlt den Modus im Training-UI Dropdown. Default fuer arrows: `"sincos"`.

---

## 8. Vollstaendige Integrationspunkte

### 8.1 Dateien und Aenderungsstellen

| Datei | Stellen | Aenderungen |
|-------|---------|------------|
| **training_core.py** | 3 neue | `SinCosArrowDataset`, `sincos_predict()`, `angular_error_batch()` |
| **training_manager.py** | ~15 Branches | Jede `if training_mode == "continuous"` Branch braucht `elif "sincos"` |
| **inference.py** | 4 Stellen | `_detect_training_mode()`, `SinCosRegressor` Klasse, `InferenceService.initialize()`, `InferenceService.reload_models()` |
| **routes/training.py** | 1 Stelle | `validate_training_mode()` → `"sincos"` akzeptieren |
| **routes/models.py** | 1 Stelle | `scan_mislabeled()` → zirkulaere Toleranz |
| **model_manager.py** | 0 | Keine Aenderung noetig |
| **app.py** | 0 | Keine Aenderung noetig (Routes delegieren an training_manager) |
| **training.html** | 2 Stellen | Training-Mode-Dropdown, sincos-spezifische Metriken-Anzeige |
| **training.js** | 3 Stellen | `startTraining()` sendet training_mode, Epoch-Log-Regex, Metriken-Parsing |

### 8.2 Kritische Integrationspunkte (aus Final Review)

| Punkt | Datei | Zeile | Was |
|-------|-------|-------|-----|
| `validate_training_mode` erweitern | routes/training.py | L43-47 | `"sincos"` als gueltig akzeptieren |
| `_detect_training_mode` erweitern | inference.py | L118-145 | `"sincos"` als dritten Rueckgabewert |
| `InferenceService.initialize()` | inference.py | L188-218 | Dritte Branch: `SinCosRegressor` instanziieren |
| `InferenceService.reload_models()` | inference.py | L262-292 | Dritte Branch: sincos-Modelle neu laden |
| 15× Training-Mode-Branch | training_manager.py | L480-832 | `elif training_mode == "sincos"` an jeder Stelle |
| **KEIN sigmoid()!** | training_manager.py | L606 | sincos-Branch darf NICHT `torch.sigmoid()` anwenden |
| Benchmark zirkulaerer Fehler | training_manager.py | L1172 | `min(abs(p-t), 10-abs(p-t))` statt `abs(p-t)` |
| Benchmark sincos-predict | training_manager.py | L1140 | `sincos_predict()` statt `regression_predict()` |
| Benchmark "correct within" | training_manager.py | L1148 | Zirkulaere Toleranz |
| `scan_mislabeled()` Toleranz | routes/models.py | L488-490 | Zirkulaere Distanz fuer sincos |
| `startTraining()` JS | training.js | L658-671 | `training_mode` aus Dropdown lesen und senden |
| Epoch-Log-Filter Regex | training.js | L609 | Neue Metriken-Patterns matchen |
| `stratified_split` anpassen | training_core.py | L232-262 | 2D-Targets brauchen Dial-Wert als Grouping-Key |

### 8.3 Was NICHT geaendert werden muss

| Bereich | Begruendung |
|---------|------------|
| `config.yaml` | training_mode ist im Modell-Metadata, nicht in Config |
| `model_manager.py` | Aktivierung/Loeschung sind mode-agnostisch |
| `watermeter_service.py` | Liest `pred["class"]` als String — Format bleibt identisch |
| MQTT/Home Assistant | Arrow-Wert fliesst als Float ein — sincos aendert das Format nicht |
| ONNX-Export | Standard timm-Export, nur `num_classes=2` statt 100 oder 1 |

---

## 9. Offene TODOs (vor Implementierung)

### ERLEDIGT
- [x] Training-Mode-Dropdown im UI (2026-02-16)
  - `training.html`: Dropdown mit discrete/continuous bei Arrows
  - `training.js`: `startTraining()` sendet `training_mode`, `onModelTypeChange()` zeigt/versteckt
  - Backend war bereits fertig

### TODO 1: Wraparound-Bug quantifizieren
**Prioritaet: HOCH — Voraussetzung fuer alles Weitere**

Bevor sincos implementiert wird, muss der Bug im aktuellen System messbar nachgewiesen werden:

1. Ueber das neue UI-Dropdown ein `"continuous"` (Regression/Sigmoid) Arrows-Modell trainieren
2. Benchmark laufen lassen
3. Per-Klassen-Accuracy auswerten, insbesondere im Bereich **9.5 - 0.5** (Wraparound-Zone)
4. Vergleich mit dem bestehenden discrete/classification Benchmark

**Erwartetes Ergebnis:**
- Wenn continuous-Modell im Wraparound-Bereich signifikant schlechter performt → sincos ist begruendet
- Wenn der Unterschied marginal ist → einfachere Alternative (Circular Label Smoothing) evaluieren
- Nur 37 von 1794 Bildern (2.1%) liegen im engen Wraparound-Bereich (9.8-0.1)

### TODO 2: Entscheidung — Replace statt Add
**Prioritaet: HOCH — aendert den gesamten Implementierungsansatz**

Devil's Advocate empfiehlt: `"continuous"` durch `"sincos"` **ersetzen**, nicht als dritten Modus hinzufuegen.

Begruendung:
- `"continuous"` hat den Wraparound-Bug (das ist die Motivation fuer sin/cos)
- `"continuous"` wurde nie produktiv eingesetzt (kein UI-Dropdown existierte bis heute)
- Replace = 0 neue Branches in `_execute_training()` statt 15 neue
- Halbiert den Implementierungsaufwand

**Entscheidung nach TODO 1:** Wenn Benchmark bestaetigt, dass continuous schlecht ist → Replace. Wenn continuous ok performt → ggf. beides beibehalten.

### TODO 3: Alternative evaluieren — Circular Label Smoothing
**Prioritaet: MITTEL — koennte sincos ueberfluessig machen**

Der bestehende 100-Klassen-Classifier koennte mit zirkulaerem Label Smoothing den Wraparound loesen:

```python
def circular_label_smoothing(target_class, num_classes=100, sigma=1.0):
    labels = torch.zeros(num_classes)
    for i in range(num_classes):
        dist = min(abs(i - target_class), num_classes - abs(i - target_class))
        labels[i] = exp(-dist**2 / (2 * sigma**2))
    return labels / labels.sum()
```

**Aufwand:** ~30 Zeilen Code vs. ~200+ Zeilen fuer sincos.
**Nachteil:** Output bleibt diskret (1 von 100 Klassen). Aber fuer 0.1er-Aufloesung reicht das.
**TODO:** Nach Benchmark-Ergebnis entscheiden, ob CLS als schnellerer Ansatz getestet wird.

### TODO 4: Confidence-Strategie ueberdenken
**Prioritaet: NIEDRIG — betrifft V1 nicht kritisch**

Die geplante Norm-basierte Confidence (`min(1.0, norm)`) ist fragwuerdig:
- MSE drueckt Outputs immer Richtung Einheitskreis (Norm ≈ 1.0)
- Norm-Varianz wird klein → Confidence fast immer 0.8-1.0 → uninformativ

Alternativen:
- Test-Time Augmentation (TTA): Varianz ueber augmentierte Inputs
- MC-Dropout: Varianz ueber Dropout-Samples
- Fuer V1 akzeptabel: Norm als Placeholder, spaeter verbessern

---

## 10. Work Packages (nach TODO-Entscheidungen)

**Achtung:** WPs aendern sich je nach Ergebnis der TODOs. Aktuelle Planung geht von "Replace continuous" aus.

| WP | Beschreibung | Agent | Abhaengigkeit | Aufwand |
|----|-------------|-------|---------------|---------|
| WP-0 | **Benchmark: Wraparound-Bug quantifizieren** (TODO 1) | user | Dropdown ✓ | manuell |
| WP-1 | Training Core: `SinCosArrowDataset`, `sincos_predict()`, `angular_error_batch()`, Stratified-Split | senior-dev | WP-0 | 1-2h |
| WP-2 | Training Manager: continuous→sincos Replace, LR Scheduler, Warmup, Early Stopping, WeightedRandomSampler | senior-dev | WP-1 | 2-3h |
| WP-3 | Inference: `SinCosRegressor`, `_detect_training_mode()`, `InferenceService` update | senior-dev | WP-1 | 2-3h |
| WP-4 | UI & Benchmark: Dropdown um sincos erweitern, Epoch-Log-Regex, Benchmark zirkulaere Fehler, `scan_mislabeled()` | frontend | WP-2, WP-3 | 1-2h |

**Geschaetzter Gesamtaufwand: 6-10 Stunden** (reduziert durch Replace statt Add)

---

## 11. Risiken & Mitigationen

| Risiko | Wahrscheinlichkeit | Mitigation |
|--------|-------------------|------------|
| Wraparound-Bug ist in der Praxis irrelevant | Mittel | **TODO 1: Messen vor Implementieren** |
| Copy-Paste sigmoid() in sincos-Branch | Hoch (bei Add) / **Eliminiert (bei Replace)** | Replace-Strategie bevorzugen |
| Benchmark zeigt falsche Metriken (linear statt zirkular) | **Hoch** | Unit Test fuer zirkulaere Fehlerberechnung |
| Overfitting bei 1794 Bildern | Mittel | Early Stopping, Weight Decay, Dropout |
| Klassen-Imbalance verzerrt Ergebnisse | Mittel | WeightedRandomSampler |
| Confidence-Heuristik unzuverlaessig | Mittel | V1: Norm als Proxy, spaeter TTA/MC-Dropout |
| ONNX-Export Probleme | Niedrig | Nur 2 Rohwerte, keine Custom Ops |

---

## Referenzen

- Zhou et al., "On the Continuity of Rotation Representations in Neural Networks", CVPR 2019
- Senior Review: `docs/tasks/2026_02_15_SinCos_Senior_Review.md`
- Final Review: `docs/tasks/2026_02_15_SinCos_Final_Review.md`
- Devil's Advocate Review: `docs/tasks/2026_02_15_SinCos_Devils_Advocate.md`
