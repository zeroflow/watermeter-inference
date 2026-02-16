# Final Review: Sin/Cos Arrows Regression

**Datum**: 2026-02-15
**Rolle**: Senior ML Engineer / Code Architect
**Grundlage**: Konzeptdokument, Senior Review, vollstaendiger Codebase-Review

---

## 1. Konzept vs. Code-Realitaet

### 1.1 KRITISCH: `_detect_training_mode()` gibt "discrete"/"continuous" zurueck — NICHT "sincos"

Das Konzept definiert `training_mode = "sincos"` als dritten Modus. Der bestehende Code (`inference.py` L118-145) gibt aber nur `"discrete"` oder `"continuous"` zurueck:

```python
def _detect_training_mode(model_path: str) -> str:
    # ...
    return metadata.get("training_mode", "discrete")  # L136
    # Fallback: "_continuous_" im Dateinamen → "continuous"
```

Und in `InferenceService.initialize()` (L194-195):
```python
if arrows_mode == "continuous":
    self._arrows_classifier = Regressor(...)
```

**Das Konzept sagt `training_mode="sincos"`, aber der Code erwartet `"continuous"`.** Wenn `metadata.json` `"sincos"` enthaelt, faellt `_detect_training_mode()` auf `"discrete"` durch (weil es nicht `"continuous"` ist), und der `InferenceService` versucht, ein `Classifier`-Objekt mit `num_classes=2` und 100 Arrow-Klassen zu laden. Das knallt sofort bei `validate_model_config()`.

**Entscheidung noetig:** Entweder (a) `sincos` als eigener Modus in `_detect_training_mode()` und `InferenceService`, oder (b) `sincos` unter `"continuous"` subsumieren und den Regressor erweitern. Das Konzept ist hier nicht mit dem Code konsistent.

### 1.2 KRITISCH: `validate_model_config()` hat hardcodierte Filename-Patterns

`inference.py` L390-452 validiert Model-Dateinamen gegen Patterns:
- Discrete: `model_arrows_<arch>_c<num>_r<res>(_s<seed>)?`
- Continuous: `model_arrows_<arch>_continuous_r<res>(_s<seed>)?`

Aber der aktuelle Code generiert Modell-IDs als `{architecture}_{short_id}` (L476-490 in `training_manager.py`) — z.B. `efficientnetv2_rw_s_a3b2c1`. Das matched **keines der Patterns** in `validate_model_config()`. Das ist ein bestehender Bug: `validate_model_config()` validiert nur alte Modellnamen und gibt bei neuen stillschweigend Erfolg zurueck (kein match = kein Check).

Fuer sincos ist das kein Showstopper, weil die Validierung nur feuert wenn ein Pattern matched. Aber es bedeutet: **Es gibt keine Validierung, dass ein sincos-Modell 2 Outputs hat.** Wenn jemand ein falsches Modell aktiviert, faengt es keiner ab.

### 1.3 MITTEL: `model_manager.create_model_id()` wird nicht verwendet

`model_manager.py` L268-287 hat eine `create_model_id()`-Methode mit dem alten Namensschema (`model_{name}_c{classes}_r{res}_s{seed}_{timestamp}`). `_execute_training()` ignoriert diese und generiert eigene IDs (L472-490). Das Konzept erwaehnt `model_manager.create_model_id()` nicht — aber ein sincos-Modell braucht kein `_c{num}` im Namen (da `num_classes=2` irrelevant fuer den User ist).

**Kein Code-Aenderungsbedarf**, aber zur Dokumentation: `create_model_id()` ist toter Code und sollte nicht fuer sincos verwendet werden.

### 1.4 INFO: Konzept beschreibt `RegressionArrowDataset` korrekt

Die existierende `RegressionArrowDataset` (`training_core.py` L190-229) speichert `target = value / 10.0` und gibt `torch.tensor(target, dtype=torch.float32)` zurueck (Skalar). Das neue `SinCosArrowDataset` soll stattdessen `torch.tensor([sin_t, cos_t], dtype=torch.float32)` zurueckgeben (2D-Vektor). Das ist ein sauberer Parallel-Ansatz — die bestehende Klasse bleibt unveraendert.

### 1.5 INFO: `stratified_split_regression()` ist wiederverwendbar

Die bestehende `stratified_split_regression()` (`training_core.py` L232-262) gruppiert nach `dataset.samples[idx][1]` (dem Target-Wert als Float-Key). Fuer sincos wird der Target ein Tupel `(sin, cos)` statt ein Skalar. **Die Split-Funktion muss angepasst oder eine neue Variante geschrieben werden.** Der Key `f"{target:.4f}"` funktioniert nicht fuer 2D-Targets. Am einfachsten: den Dial-Wert (Float) als drittes Element in `samples` speichern und zum Stratifizieren verwenden.

---

## 2. Integrationsprobleme

### 2.1 KRITISCH: `_execute_training()` — 15 Stellen mit `if training_mode == "continuous"`

Die Training-Logik in `training_manager.py` L429-840 hat exakt 15 Branches auf `model_type == "arrows" and training_mode == "continuous"`:

- L480, L506, L519, L553, L568, L603, L622, L656, L676, L707, L739, L776, L794, L814, L832 (indirekt)

**Jede einzelne dieser Stellen muss fuer sincos angepasst werden.** Das Konzept erwaehnt 4 Work Packages, aber unterschaetzt die Tiefe der Aenderungen in `_execute_training()`. Insbesondere:

1. **L553-554**: `num_classes=1` → sincos braucht `num_classes=2`
2. **L568-569**: MSE-Loss mit Sigmoid → sincos braucht MSE ohne Sigmoid
3. **L603-606**: `torch.sigmoid(outputs)` → sincos darf kein Sigmoid anwenden
4. **L622-640**: Val-Metrik berechnet MAE/RMSE linear → sincos braucht `angular_error_batch()`
5. **L656-664**: Best-Model-Tracking auf `within_half` → sincos braucht angular-basiertes Tracking
6. **L676-688**: Progress-Update zeigt MAE → sincos zeigt MAE_dial
7. **L739-745**: Metadata speichert `best_val_mae`, `best_val_rmse` → sincos speichert andere Metriken

**Empfehlung:** Entweder ein drittes `elif training_mode == "sincos"` an jeder Stelle (haesslich, aber konsistent mit dem bestehenden Pattern), oder — besser — die Training-Loop-Logik in Strategie-Klassen/Funktionen refactoren (`ClassificationStrategy`, `RegressionStrategy`, `SinCosStrategy`). Das ist ein Pre-Requisite-Refactoring, das das Konzept nicht erwaehnt.

### 2.2 KRITISCH: `_execute_benchmark()` — Benchmark berechnet Fehler NICHT zirkulaer

`training_manager.py` L1172:
```python
errors = [abs(p - t) for p, t in all_predictions]
```

Das ist **lineare Differenz**. Fuer den Regression-Modus ist das der bestehende Bug (9.9 vs. 0.0 = Fehler 9.9 statt 0.1). Fuer sincos muss das zu zirkulaerer Differenz geaendert werden:
```python
diff = min(abs(p - t), 10.0 - abs(p - t))
```

Auch L1148 hat dasselbe Problem:
```python
if abs(pred_value - expected_value) < 0.5:
    correct += 1
```

Hier wuerde `pred=0.1, expected=9.9` als Fehler 9.8 gewertet statt 0.2.

**Das Konzept erwaehnt die Benchmark-Anpassung in WP-4, aber spezifiziert nicht, dass `regression_predict()` fuer sincos ersetzt werden muss.** Im Benchmark wird `regression_predict(result)` aufgerufen (L1140), welches Sigmoid anwendet — fuer sincos muss stattdessen `sincos_predict(result)` aufgerufen werden.

### 2.3 MITTEL: `InferenceService.get_classifier()` gibt `Classifier`-Typ zurueck

`inference.py` L350-358:
```python
def get_classifier(self, model_type: str) -> Classifier:
    """Get classifier (for backward compatibility)."""
```

Diese Methode hat den Return-Type `Classifier`, gibt aber fuer `arrows` moeglicherweise einen `Regressor` zurueck. Fuer sincos wuerde sie ein `SinCosRegressor`-Objekt zurueckgeben. Der Type-Hint ist falsch, aber Python erzwingt das nicht. Callers (`scan_mislabeled()` in `routes/models.py` L456-461) verwenden `hasattr(classifier, "classes")` um Regression zu erkennen:

```python
is_regression = hasattr(classifier, "predict") and not hasattr(classifier, "classes")
```

Ein `SinCosRegressor` haette ebenfalls kein `classes`-Attribut, also wuerde `is_regression = True` gesetzt. Aber die Mislabel-Toleranzberechnung (L488-490) verwendet lineare Distanz:
```python
is_match = abs(pred_val - folder_val) < 0.5
```

Fuer sincos muss das zirkulaer berechnet werden. **Der Mislabel-Scan (BL-03) bricht nicht, aber liefert falsche Ergebnisse nahe dem Wraparound.**

### 2.4 MITTEL: `watermeter_service.calculate_total()` — Arrows-Werte kommen als `pred["class"]` (String)

`watermeter_service.py` L722:
```python
arrows.append(float(pred["class"]))
```

Und L738:
```python
total += int(arrow) * multiplier
```

Das funktioniert, weil `Regressor.predict()` (L98) `class_str = f"{dial_position:.1f}"` zurueckgibt — einen String wie `"3.7"`. Der sincos-Regressor muss ebenfalls einen String im Format `"{value:.1f}"` zurueckgeben. Das Konzept (Section 3.7) zeigt:
```python
return {"class": f"{dial_position:.1f}", "confidence": confidence}
```

Das ist korrekt. **Keine Aenderung an `watermeter_service` noetig**, solange das Output-Format identisch bleibt.

### 2.5 NIEDRIG: `LR Schedule` — kein Scheduler im aktuellen Code

Der aktuelle `_execute_training()` (L566) nutzt nur:
```python
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
```

Kein LR-Scheduler, kein Warmup. Das Konzept empfiehlt `SequentialLR(LinearLR + CosineAnnealingLR)`. Das ist eine Verbesserung, aber es ist **kein bestehendes Feature, das erweitert wird** — es ist eine Neuimplementierung. Das hat den Vorteil, dass nichts kaputt gehen kann, aber der Entwickler muss wissen, dass er die Scheduler-Integration von Grund auf schreiben muss.

### 2.6 NIEDRIG: `WeightedRandomSampler` — DataLoader hat aktuell `shuffle=True`

`training_manager.py` L547-548:
```python
train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, ...)
```

`WeightedRandomSampler` und `shuffle=True` sind mutually exclusive in PyTorch. Der Entwickler muss `shuffle=True` durch den Sampler ersetzen. Das Konzept erwaehnt das korrekt (Section 3.5), aber es ist ein Fallstrick, weil PyTorch einen kryptischen Fehler wirft.

### 2.7 NIEDRIG: `Early Stopping` existiert nicht im aktuellen Code

Der aktuelle Code trainiert immer fuer exakt `epochs` Epochen (L586: `for epoch in range(epochs)`). Es gibt kein Early-Stopping-Mechanismus. Das Konzept empfiehlt `Patience=8`, was eine neue Implementierung erfordert. Der bestehende Code trackt bereits `best_model_state` — der Early-Stopping-Check waere ein einfacher Epoch-Counter-Vergleich.

---

## 3. Fehlende Details

### 3.1 KRITISCH: Wie erkennt `InferenceService` sincos vs. continuous?

Das Konzept sagt:
> `_detect_training_mode()` erkennt den Modus anhand `metadata.json → training_mode`
> - `"classification"` → Softmax + argmax
> - `"regression"` → Sigmoid × 10
> - `"sincos"` → atan2 Post-Processing

Aber die bestehende Code-Terminologie ist:
- `"discrete"` (nicht `"classification"`)
- `"continuous"` (nicht `"regression"`)

**Das Konzept verwendet andere Begriffe als der Code.** Im Code gibt es nirgendwo `"classification"` oder `"regression"` als `training_mode`-Werte — es ist immer `"discrete"` oder `"continuous"`. Der Entwickler muss wissen: `"sincos"` ist der dritte Wert, nicht ein Ersatz fuer `"continuous"`.

### 3.2 MITTEL: `predict_detailed()` fuer sincos nicht spezifiziert

`Regressor.predict_detailed()` (L108-115) gibt eine Single-Element-Liste zurueck (kein Top-K-Konzept). Fuer sincos gilt dasselbe — aber das Konzept erwaehnt `predict_detailed()` nicht. Das WP-3 muss dies spezifizieren.

### 3.3 MITTEL: `DataLoader` `num_workers` und `worker_init_fn`

Der bestehende Code verwendet `num_workers=2` und `worker_init_fn=worker_init_fn`. Das Konzept-Dataset (`SinCosArrowDataset`) muss kompatibel mit Multi-Worker-Loading sein. Da es PIL-basiert ist (wie `RegressionArrowDataset`), sollte das funktionieren. Aber: `worker_init_fn` setzt Seeds fuer Reproduzierbarkeit — der sincos-Datensatz hat keine Zufallskomponente in `__getitem__()`, also ist das unkritisch.

### 3.4 NIEDRIG: `config.yaml` — kein Aenderungsbedarf

Das Konzept erwaehnt `config.yaml` nicht. Nach Review: Die Config speichert `arrows_model` (Pfad), `arrows_classes` (Liste), `arrows_resolution` (int). Fuer sincos wird:
- `arrows_model` auf das neue Modell zeigen
- `arrows_classes` irrelevant (sincos hat keine diskreten Klassen)
- `arrows_resolution` wie bisher

`model_manager.activate_model()` (L226-239) setzt `arrows_classes` aus Metadata:
```python
if metadata.get("classes"):
    config["inference"][f"{model_type}_classes"] = metadata["classes"]
```

Fuer sincos ist `metadata["classes"] = None` (siehe Konzept L745), also wird `arrows_classes` **nicht aktualisiert**. Das bedeutet: Nach Aktivierung eines sincos-Modells bleibt der alte `arrows_classes`-Wert in der Config. Das ist harmlos, weil der sincos-Pfad die Klassen nie liest — aber es koennte verwirrend sein.

### 3.5 NIEDRIG: MQTT/Home Assistant — kein Aenderungsbedarf

Die Arrow-Vorhersage fliesst als `float(pred["class"])` in die Berechnung (L722). Das ist ein einfacher Float-Wert ("3.7"), der via MQTT/HA publiziert wird. Sincos aendert nichts am Output-Format — es ist immer noch ein String wie `"3.7"` mit einer Confidence. **Kein Aenderungsbedarf an MQTT/HA.**

### 3.6 NIEDRIG: Fehlende Spezifikation der Augmentation

Das Konzept erwaehnt, dass der bestehende Code nur `ColorJitter` verwendet (`training_core.py` L62). Das stimmt. Es wird keine Rotation-Augmentation hinzugefuegt. Das Senior Review (Section 2.3.5) warnt korrekt: Rotation-Augmentation wuerde den Ground-Truth-Winkel aendern. **Keine Aenderung noetig, aber eine Warnung im Code waere hilfreich.**

---

## 4. Widersprueche

### 4.1 Konzept vs. Senior Review: Epochen

- **Konzept**: 30 Epochen (Section 4)
- **Senior Review**: 30 Epochen (Section 3.4)
- **Bestehender Code**: Default 20 Epochen (`TrainingConfig.epochs = 20`, L36 in `routes/training.py`)

Kein Widerspruch zwischen Konzept und Review. Aber: Der User muss 30 in der UI eingeben (oder der Default muss fuer sincos angepasst werden). Das Konzept erwaehnt nicht, ob der Default in der UI geaendert werden soll.

### 4.2 Konzept vs. Senior Review: Architecture

- **Konzept**: `efficientnetv2_rw_s` (Section 3.1)
- **Senior Review**: Spricht von EfficientNet-B0 (Section 1.2), dann `efficientnetv2_rw_s` im Code-Snippet (Section 3.1)

Das ist ein Fehler im Senior Review — es mischt B0 und V2-S. Der Code unterstuetzt beliebige timm-Architekturen, also ist das irrelevant fuer die Implementierung. Der User waehlt die Architektur in der UI.

### 4.3 KEIN Widerspruch bei Hyperparametern

Konzept und Senior Review stimmen ueberein bei: LR 1e-3, Batch Size 16, Weight Decay 1e-4, Warmup 3 Epochen, Patience 8, MSE Loss, keine Normalisierung.

### 4.4 MITTEL: Terminologie-Widerspruch "continuous" vs "sincos" vs "regression"

- Code: `"continuous"` und `"discrete"`
- Konzept: `"sincos"`, `"regression"`, `"classification"`
- Senior Review: `"sincos"`, `"continuous"`, `"regression"` (gemischt)

Das ist verwirrend. **Empfehlung:** Im Code `"sincos"` als dritter Wert neben `"discrete"` und `"continuous"` einfuehren. Die UI sollte die User-freundlichen Labels "Classification", "Regression (legacy)", "Sin/Cos" zeigen. Die `TrainingConfig.validate_training_mode()` muss `"sincos"` akzeptieren.

---

## 5. Risikobewertung

### 5.1 Hoechstes Risiko: Training-Loop-Komplexitaet

Die `_execute_training()`-Funktion ist bereits 410 Zeilen lang mit 15 Mode-Branches. Ein dritter Modus fuegt ~15 weitere `elif`-Branches hinzu. Das macht den Code extrem fragil:

- Jeder Bug in einem Branch ist schwer zu finden (visuell identische Bloecke)
- Testen erfordert drei separate Durchlaeufe (discrete, continuous, sincos)
- Zukuenftige Aenderungen muessen an drei Stellen gemacht werden

**Risiko-Mitigation:** Den Training-Loop vorher refactoren (Strategy-Pattern oder separate Funktionen pro Modus). Das ist Mehraufwand, verhindert aber langfristige Wartungsprobleme.

**Realistisch aber:** Wenn Zeitdruck besteht, kann man das dritte `elif` einfuegen und spaeter refactoren. Der Code ist haesslich, aber funktioniert.

### 5.2 Mittleres Risiko: Benchmark-Korrektheit nahe Wraparound

Der Benchmark ist der primaere Vergleichsmassstab. Wenn die Benchmark-Metriken nicht zirkulaer berechnet werden, sieht sincos schlechter aus als es ist (weil Fehler nahe 0.0/9.9 ueberschaetzt werden). Das wuerde zu falschen Schlussfolgerungen fuehren.

**Besonders tueckisch:** Das Problem ist subtil — der Benchmark laeuft durch, zeigt Zahlen an, aber die Zahlen sind falsch. Es gibt keinen Crash, nur falsche Metriken.

### 5.3 Mittleres Risiko: Sigmoid-Entfernung im Training-Loop

Der bestehende Regression-Code wendet `torch.sigmoid(outputs)` explizit im Training-Loop an (L606):
```python
outputs = model(imgs).squeeze(-1)
loss = criterion(torch.sigmoid(outputs), labels)
```

Fuer sincos darf **kein Sigmoid** angewandt werden — die Rohwerte werden direkt als sin/cos interpretiert. Wenn ein Entwickler das Sigmoid versehentlich beibehalt (Copy-Paste vom continuous-Block), konvergiert das Training trotzdem, aber die Outputs liegen in (0,1) statt (-1,1), und `atan2` produziert nur Werte im 1. Quadranten (0 bis pi/4). **Das ist ein schwer zu debuggender Bug**, weil das Training-Loss sinkt, aber die Vorhersagen systematisch falsch sind.

### 5.4 Niedriges Risiko: Confidence-Heuristik

Die Norm-basierte Confidence (`min(1.0, norm / 1.0)`) ist ungetestet. Typische Output-Normen nach Training sind unbekannt. Wenn sie z.B. immer bei ~1.5 liegen, ist die Confidence immer 1.0 (uninformativ). Wenn sie stark schwanken (0.2-3.0), kann die Heuristik funktionieren.

**Mitigation:** Confidence-Werte im Training loggen (Norm-Statistiken pro Epoche auf dem Val-Set).

### 5.5 Niedriges Risiko: ONNX-Export

Das Modell ist ein Standard-timm-Modell mit `num_classes=2`. Der ONNX-Export (`export_to_openvino()` in `training_core.py` L130-166) aendert sich nicht. Es gibt kein Risiko hier.

---

## 6. Empfehlungen fuer die Implementierung

### Reihenfolge und Abhaengigkeiten

1. **Vor WP-1**: `TrainingConfig.validate_training_mode()` muss `"sincos"` als dritten Wert akzeptieren (`routes/training.py` L43-47). Triviale Aenderung.

2. **WP-1** (Dataset & Core): Wie im Konzept. Zusaetzlich: `stratified_split_sincos()` oder Anpassung von `stratified_split_regression()` fuer 2D-Targets.

3. **WP-2** (Training Manager): **Groesster Aufwand.** Alle 15 Mode-Branches in `_execute_training()` muessen ein drittes `elif training_mode == "sincos"` bekommen. Dazu: Scheduler, Sampler, Early-Stopping — alles neue Features, die auch fuer die anderen Modi noetig waeren, aber nur fuer sincos implementiert werden.

4. **WP-3** (Inference): `SinCosRegressor` Klasse, `_detect_training_mode()` Update, `InferenceService.initialize()` und `reload_models()` — je eine neue Branch fuer `"sincos"`.

5. **WP-4** (Benchmark & UI): Benchmark-Fehlerberechnung auf zirkulaer umstellen. UI: Training-Mode-Dropdown mit `discrete`, `continuous`, `sincos`. Mislabel-Scan: zirkulaere Toleranz.

### Was das Konzept explizit NICHT abdeckt (aber implementiert werden muss)

| Punkt | Wo | Was |
|-------|----|-----|
| `validate_training_mode` erweitern | `routes/training.py` L43-47 | `"sincos"` als gueltig akzeptieren |
| `_detect_training_mode` erweitern | `inference.py` L118-145 | `"sincos"` als dritten Rueckgabewert |
| `InferenceService.initialize()` | `inference.py` L188-218 | Dritte Branch fuer sincos |
| `InferenceService.reload_models()` | `inference.py` L262-292 | Dritte Branch fuer sincos |
| `validate_model_config()` | `inference.py` L390-452 | Optional: sincos-Pattern oder Bypass |
| `scan_mislabeled()` zirkulaere Toleranz | `routes/models.py` L488-490 | `min(abs(p-t), 10-abs(p-t))` |
| `_execute_benchmark()` zirkulaere Fehler | `training_manager.py` L1172 | Dito |
| `_execute_benchmark()` sincos-predict | `training_manager.py` L1140 | `sincos_predict()` statt `regression_predict()` |
| Benchmark-Metadaten-Persistierung | `training_manager.py` L993-1003 | Neue Keys fuer sincos-spezifische Metriken |
| `startTraining()` JS sendet training_mode | `static/training.js` L658-671 | `config.training_mode` muss gesetzt werden |
| Epochen-Log-Filter Regex | `static/training.js` L609 | Pattern matched nur `Val Acc:`, nicht `MAE=` |

### Abschliessende Einschaetzung

Das Konzept ist fachlich solide und die ML-Entscheidungen sind korrekt. Die **Hauptluecke ist die Unterschaetzung der Code-Integration**: 6 Dateien mit jeweils 3-15 Stellen, an denen sincos eingefuegt werden muss, plus UI-Aenderungen. Der Senior Review hat die ML-Seite gut abgedeckt, aber die Code-Realitaet nicht tief genug analysiert.

**Geschaetzter Aufwand (realistisch):**
- WP-1: 1-2 Stunden (neues Dataset, Hilfsfunktionen)
- WP-2: 3-4 Stunden (Training Manager ist komplex und fragil)
- WP-3: 2-3 Stunden (Inference Pipeline, 3 Dateien)
- WP-4: 2-3 Stunden (Benchmark, UI, Mislabel-Scan)
- **Gesamt: 8-12 Stunden** — nicht der "eine Tag" den der Senior Review vorschlaegt, weil die Integration-Tiefe unterschaetzt wurde.
