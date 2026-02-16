# Senior Review: Sin/Cos Regression fuer Arrow-Modell

> **Autor**: Senior ML Researcher (Claude Opus 4.6)
> **Datum**: 2026-02-15
> **Kontext**: Watermeter-Projekt, Arrow-Position 0.0-9.9 (100 diskrete Stufen, eine volle Umdrehung)
> **Dataset**: 1794 Bilder in 100 Klassen (9-29 Bilder pro Klasse), EfficientNet-B0 Backbone, PyTorch -> ONNX -> OpenVINO

---

## 1. Kritik der Forschungsberichte

### 1.1 Researcher 1 (Loss Functions) -- Bewertung: 7/10

**Was richtig ist:**
- Das Zhou et al. CVPR 2019-Zitat ("Representation matters more than loss function") ist das zentrale Insight und absolut korrekt. Die Wahl des Encodings dominiert; die Loss Function ist zweitrangig.
- MSE auf (sin, cos) als erste Wahl ist richtig. Es ist die Standard-Baseline, gut verstanden, stabil.
- Die Warnung vor reinem Angular Loss (Gradient-Probleme bei pi) ist berechtigt.

**Was fehlt oder uebersimplifiziert wurde:**
- **Keine Analyse der Gewichtung zwischen sin und cos Komponenten.** Bei MSE auf (sin, cos) werden beide Komponenten gleich gewichtet. Das ist fuer unseren Fall korrekt (beide sind gleich informativ), aber es haette explizit begruendet werden muessen.
- **Keine Diskussion von Label Noise.** Unser Ground Truth hat 0.1-Schritte, aber die tatsaechliche Nadelposition ist kontinuierlich. Ein Bild mit Label "3.5" koennte physisch bei 3.48 oder 3.52 liegen. Das ist ~0.02 Einheiten Rauschen, was zu sin/cos-Targets fuehrt, die um ~0.01 von den "wahren" Werten abweichen. MSE ist hier tolerant, aber das haette erwaehnt werden muessen.
- **Kein Vergleich mit der existierenden Baseline.** Der aktuelle Code nutzt `sigmoid -> [0,1] -> *10` als Regression. Jeder Vorschlag muss zeigen, *warum* er besser ist als der Status Quo, nicht nur warum er theoretisch sauber ist.
- **Combined Loss (MSE + Angular) Empfehlung ist zu vorsichtig.** "5-10% marginal improvement" ist nicht belegt -- woher kommt diese Zahl? Bei 1794 Bildern und 100 Klassen ist jede zusaetzliche Komplexitaet ein Risiko (Overfitting, Hyperparameter-Tuning).

### 1.2 Researcher 2 (Normalization) -- Bewertung: 6/10

**Was richtig ist:**
- L2-Normalization als Empfehlung ist technisch korrekt fuer das allgemeine Problem.
- Die Warnung vor Tanh/Sigmoid ist richtig -- sie garantieren keine Unit-Circle Eigenschaft.
- Die Practitioner-Tabelle nach Domaene ist nuetzlich.

**Was problematisch ist:**
- **Unkritische Uebernahme von 6D Rotation Estimation.** Zhou et al.'s L2-Norm-Empfehlung gilt fuer 6D Rotation Representations (SO(3)), wo die Norm-Constraint eine Grassmann-Mannigfaltigkeit erzwingt. Unser Problem ist viel einfacher: 1D Winkel auf einem Kreis (S^1). Die Analogie ist nicht falsch, aber die Dringlichkeit wird uebertrieben.
- **Das eigentliche Risiko von Raw Outputs wird ueberbewertet.** Die "origin singularity" (sin=0, cos=0) ist theoretisch real, aber praktisch irrelevant: Ein trainiertes Netz produziert keine (0,0)-Outputs, weil der Loss dafuer bestraft. In der Praxis liegen die Outputs in einem Bereich von ~0.3-1.5 Norm, nie nahe Null.
- **Keine Diskussion der Quantisierungsimplikationen.** Wir exportieren zu OpenVINO (FP16/INT8). L2-Normalization im Netz erfordert eine Division durch die Norm, was bei Quantisierung zu numerischen Artefakten fuehren kann. Raw Outputs + Post-Processing atan2 in Python (FP64) ist numerisch sicherer.
- **Die entscheidende Frage wird nicht beantwortet:** Soll die Normalization *im Netz* (vor ONNX-Export) oder *im Post-Processing* (Python) stattfinden? Das macht einen grossen Unterschied fuer die Deployment-Pipeline.

### 1.3 Researcher 3 (Architecture & Practical) -- Bewertung: 8/10

**Was richtig ist:**
- "DO NOT include atan2 in ONNX" -- das ist die wichtigste praktische Empfehlung und absolut korrekt. ONNX hat keinen nativen atan2-Op, und der Workaround ueber Atan + Conditional ist fragil.
- Single shared head mit 256 Hidden Units ist ein vernuenftiger Default fuer EfficientNet-B0.
- CosineAnnealingLR ist besser als konstante LR, besonders bei kleinen Datasets.
- atan2-basierte Angular Error Metrik ist korrekt.

**Was fehlt:**
- **Der Warmup-Vorschlag (10 Epochen) ist zu aggressiv fuer 20 Epochen Training.** Bei 20 Epochen total sind 10 Epochen Warmup absurd -- das Modell trainiert effektiv nur 10 Epochen. 2-3 Epochen Warmup sind angemessen.
- **Keine Diskussion des existierenden Regressor-Codes.** Der aktuelle Code (`inference.py:54-115`) nutzt `num_classes=1` mit Sigmoid, was einen fundamentalen Flaw hat: Sigmoid mappt auf (0,1), und die Werte 0.0 und 9.9 werden zu 0.0 und 0.99 -- **der Wrap-around von 9.9 nach 0.0 wird nicht geloest!** Das ist genau der Grund, warum wir sin/cos brauchen.
- **Batch Size 32-64 ist fuer unser Dataset moeglicherweise zu gross.** Mit 1794 Bildern und 80/20 Split haben wir ~1435 Training Samples. Batch Size 64 ergibt ~22 Batches pro Epoch -- das ist grenzwertig wenig fuer stabiles Gradient-Estimation. Batch Size 16-32 ist besser.
- **Keine Erwaehnung von Data Augmentation fuer Rotation.** Fuer ein Zeiger-Modell waere leichte Rotation-Augmentation (+-5 Grad) sinnvoll, aber es muss mit Vorsicht eingesetzt werden -- die Rotation des Bildes aendert die korrekte Klasse!

---

## 2. Finale Empfehlungen

### 2.1 Loss Function: MSE auf (sin, cos) -- Punkt.

**Confidence: HOCH (9/10)**

**Formel:**
```
target_angle = value * 2*pi / 10     # value in [0.0, 9.9]
target_sin = sin(target_angle)
target_cos = cos(target_angle)

L = MSE(pred_sin, target_sin) + MSE(pred_cos, target_cos)
  = (pred_sin - target_sin)^2 + (pred_cos - target_cos)^2
```

**Begruendung:**
1. **Einfachheit.** Ein kleines Projekt mit 1794 Bildern braucht keine fancy Losses. Die Fehlerquelle wird nicht der Loss sein, sondern Data Quality, Augmentation, und Overfitting.
2. **Stabilet Gradienten.** MSE hat ueberall wohldefinierte Gradienten, keine Singularitaeten.
3. **Das Encoding loest das Problem.** Der Wrap-around (9.9 -> 0.0) wird durch sin/cos vollstaendig aufgefangen. Bei value=9.9 und value=0.0 sind die sin/cos-Vektoren fast identisch: sin(2pi*9.9/10)=sin(2pi*0.99)=-0.063 vs sin(0)=0.0, cos(2pi*0.99)=0.998 vs cos(0)=1.0. Der MSE-Abstand ist winzig (0.002), wie es sein soll.
4. **Kein zusaetzlicher Hyperparameter.** Combined Losses brauchen lambda-Tuning, was bei kleinem Dataset und ohne Compute-Budget fuer Grid Search nicht vertretbar ist.

**Was wuerde meine Meinung aendern:** Wenn MSE-Training zu Modellen fuehrt, die systematisch in bestimmten Winkelbereichen schlecht sind (z.B. rund um 0/5), *dann* wuerde ich Combined Loss mit Angular-Komponente testen. Aber erst als Debugging-Massnahme, nicht als Default.

### 2.2 Normalization: KEINE im Netz. Raw Outputs + Post-Processing.

**Confidence: HOCH (8/10)**

**Begruendung:**
1. **atan2 ist scale-invariant.** `atan2(k*sin, k*cos) = atan2(sin, cos)` fuer jedes k>0. Die Norm der Outputs ist irrelevant fuer die finale Vorhersage.
2. **OpenVINO-Kompatibilitaet.** Keine Division im Netz = keine Quantisierungsprobleme. Das Netz gibt einfach 2 Float-Werte aus, Python macht den Rest.
3. **Einfacherer ONNX-Export.** Kein Custom-Op noetig, kein Normalization-Layer der bei Export-Problemen debuggt werden muss.
4. **Das "Origin Singularity"-Problem ist theoretisch.** In der Praxis konvergiert ein MSE-trainiertes Netz nie zu (0,0)-Outputs, weil das maximal weit von allen Targets entfernt ist. Selbst wenn ein Outlier-Input (0.01, 0.01) produziert: atan2(0.01, 0.01) = pi/4 = 1.25, was eine valide (wenn auch falsche) Vorhersage ist. Es gibt keinen Crash.
5. **L2-Norm im Netz wuerde die Loss-Landschaft veraendern.** Die Gradienten fliessen durch die Normalization zurueck, was die effektive Learning Rate beeinflusst. Bei unserem kleinen Dataset ist das ein unnoetig riskanter Freiheitsgrad.

**Optionaler Soft-Guard:** Falls wider Erwarten im Betrieb (0,0)-nahe Outputs auftreten, kann ein einfacher Fallback im Post-Processing ergaenzt werden:
```python
if norm < 1e-6:
    return last_valid_prediction  # oder Confidence = 0.0
```

**Was wuerde meine Meinung aendern:** Wenn Training-Experimente zeigen, dass die Norm der Outputs stark schwankt (z.B. zwischen 0.1 und 10.0) und dies mit schlechter Generalisierung korreliert, wuerde ich Soft L2 Penalty (lambda=0.01 auf `(norm-1)^2`) hinzufuegen -- aber nicht L2-Norm im Forward Pass.

### 2.3 Concerns die alle drei Researcher verpasst haben

**2.3.1 Das bestehende Sigmoid-Regression-Modell hat einen fundamentalen Bug**

Der aktuelle Code (`training_core.py:211`, `inference.py:91`) mappt den Zielwert auf `value/10.0 -> [0, 1)` und nutzt Sigmoid. Das bedeutet:
- Wert 9.9 -> Target 0.99
- Wert 0.0 -> Target 0.00
- Abstand 9.9 zu 0.0 im Target-Raum: 0.99 (MAXIMAL)
- Abstand 9.9 zu 0.0 in der Realitaet: 0.1 (eine Stufe)

**Das Sigmoid-Modell sieht 9.9 und 0.0 als die am weitesten entfernten Werte.** Das ist der Bug, den sin/cos loesen soll. Alle drei Researcher haben diesen konkreten Bug im bestehenden Code nicht benannt, obwohl er die primaere Motivation fuer die Umstellung ist.

**2.3.2 Class Imbalance bei kleinem Dataset**

Das Dataset hat 9-29 Bilder pro Klasse (3.2x Imbalance Ratio). Bei Classification verwendet der existierende Code `compute_class_weight('balanced')`. Fuer Regression mit MSE auf sin/cos gibt es kein Pendant. Moegliche Auswirkung: Klassen mit wenigen Bildern (z.B. "2.9" mit 9 Bildern) werden systematisch schlechter vorhergesagt.

**Loesung:** Sample Weights im DataLoader (`WeightedRandomSampler`), sodass unterrepraesentierte Positionen oefter gesampelt werden. Einfach zu implementieren, signifikanter Effekt bei dieser Imbalance.

**2.3.3 Confidence-Schaetzung fuer sin/cos ist nicht trivial**

Der aktuelle Regressor-Code nutzt `|sigmoid - 0.5| * 2` als Confidence-Heuristik. Das ist fuer Sigmoid-Output sinnvoll (weit von 0.5 = confident). Fuer sin/cos-Output brauchen wir eine neue Confidence-Metrik. Die Norm des (sin, cos)-Vektors ist dafuer ein natuerlicher Kandidat:
- Grosse Norm (>1.0): Modell ist "ueberzeugt"
- Kleine Norm (<0.5): Modell ist unsicher

Alternativ koennen wir einen dritten Output hinzufuegen (Confidence), oder die Varianz ueber Augmentations nutzen (Test-Time Augmentation). Fuer V1 reicht die Norm als Proxy.

**2.3.4 Metriken muessen angepasst werden**

Der aktuelle Benchmark (`training_manager.py`) nutzt "within-half" (`|pred-target| < 0.05`). Fuer sin/cos muss die Metrik auf Angular Distance umgestellt werden:
```python
angular_error = atan2(sin(pred - target), cos(pred - target))  # in [-pi, pi]
dial_error = abs(angular_error) * 10 / (2*pi)  # in Dial-Einheiten [0, 5]
```
"Within-half" waere dann `dial_error < 0.5`.

**2.3.5 Data Augmentation muss bedacht werden**

Fuer Classification ist Augmentation einfach -- das Label aendert sich nicht durch ColorJitter oder Rotation. Fuer sin/cos-Regression aendert eine Bildrotation den Ground-Truth-Winkel! Der aktuelle Code nutzt nur ColorJitter, was sicher ist. Aber wenn jemand spaeter Rotation-Augmentation hinzufuegt, muss der sin/cos-Target mitgedreht werden.

---

## 3. Konkrete Implementierungsspezifikation

### 3.1 Model Head Architecture

```python
# timm Backbone: EfficientNet-B0 mit pretrained=True
# Default Head wird durch num_classes=2 ersetzt
model = timm.create_model('efficientnetv2_rw_s', pretrained=True, num_classes=2)
```

**Begruendung gegen Custom Head mit 256 Hidden:** timm's Default-Head fuer EfficientNet ist bereits `GlobalAvgPool -> Dropout(0.2) -> Linear(1280, num_classes)`. Das ist fuer unser kleines Dataset *ausreichend*. Ein zusaetzliches 256-Hidden-Layer erhoehen die Overfitting-Gefahr ohne klaren Benefit. Wenn wir spaeter schlechtere Performance sehen, koennen wir den Head erweitern. YAGNI.

**Output:** 2 Rohwerte (sin_raw, cos_raw). Keine Activation Function, keine Normalization.

### 3.2 Dataset und Target Encoding

```python
import math
import torch
from torch.utils.data import Dataset
from pathlib import Path
from PIL import Image

class SinCosArrowDataset(Dataset):
    """Arrow dataset mit sin/cos Targets fuer zirkulaere Regression."""

    def __init__(self, root_dir: Path, transform=None):
        self.transform = transform
        self.samples = []  # (image_path, sin_target, cos_target, dial_value)

        for class_dir in sorted(root_dir.iterdir()):
            if not class_dir.is_dir():
                continue
            try:
                value = float(class_dir.name)  # 0.0, 0.1, ..., 9.9
            except ValueError:
                continue

            angle = value * 2 * math.pi / 10.0  # Map to [0, 2*pi)
            sin_target = math.sin(angle)
            cos_target = math.cos(angle)

            for img_path in sorted(class_dir.glob("*.jpg")):
                self.samples.append((str(img_path), sin_target, cos_target, value))

        if not self.samples:
            raise ValueError(f"No images found in {root_dir}")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, sin_t, cos_t, _ = self.samples[idx]
        img = Image.open(img_path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        target = torch.tensor([sin_t, cos_t], dtype=torch.float32)
        return img, target
```

### 3.3 Loss Function

```python
# Einfach MSE auf beide Komponenten
criterion = torch.nn.MSELoss()

# Im Training Loop:
outputs = model(imgs)            # (batch, 2) -- raw sin/cos
loss = criterion(outputs, targets)  # targets: (batch, 2) -- sin/cos GT
```

**Kein class weighting noetig?** Doch -- siehe 3.5 (WeightedRandomSampler).

### 3.4 Training Hyperparameter

| Parameter | Wert | Begruendung |
|-----------|------|-------------|
| Optimizer | Adam | Bewaehrt, kein Tuning noetig |
| Learning Rate | 1e-3 | Standard fuer timm pretrained models |
| LR Schedule | CosineAnnealingLR(T_max=epochs) | Besser als konstant, kein extra HP |
| Warmup | 3 Epochen linear (LR 1e-5 -> 1e-3) | Schuetzt pretrained Features |
| Batch Size | 16 | Bei 1435 Train Samples -> ~90 Batches/Epoch, gut |
| Epochs | 30 | Mehr als die aktuellen 20, weil Regression langsamer konvergiert |
| Weight Decay | 1e-4 | Leichte Regularisierung gegen Overfitting |
| Early Stopping | Patience=8 auf Val Angular Error | Verhindert Overfitting |

**Warmup-Implementierung (SequentialLR):**
```python
from torch.optim.lr_scheduler import LinearLR, CosineAnnealingLR, SequentialLR

warmup = LinearLR(optimizer, start_factor=0.01, total_iters=3)
cosine = CosineAnnealingLR(optimizer, T_max=epochs - 3)
scheduler = SequentialLR(optimizer, schedulers=[warmup, cosine], milestones=[3])
```

### 3.5 Balanced Sampling (gegen Class Imbalance)

```python
from torch.utils.data import WeightedRandomSampler

# Berechne Gewicht pro Sample basierend auf Klassenhaeufigkeit
class_counts = {}
for _, _, _, value in dataset.samples:
    key = f"{value:.1f}"
    class_counts[key] = class_counts.get(key, 0) + 1

sample_weights = []
for _, _, _, value in dataset.samples:
    key = f"{value:.1f}"
    sample_weights.append(1.0 / class_counts[key])

sampler = WeightedRandomSampler(
    weights=sample_weights,
    num_samples=len(sample_weights),
    replacement=True
)

train_loader = DataLoader(train_ds, batch_size=16, sampler=sampler, ...)
# Achtung: sampler und shuffle sind mutually exclusive
```

### 3.6 Validation Metrik: Mean Angular Error (MAE_angle)

```python
import math
import numpy as np

def angular_error_batch(pred_sin, pred_cos, target_sin, target_cos):
    """Berechne Angular Error in Dial-Einheiten (0.0 - 5.0).

    Args:
        pred_sin, pred_cos: Model outputs (batch,)
        target_sin, target_cos: Ground truth (batch,)

    Returns:
        Array of errors in dial units [0.0, 5.0]
    """
    # Rekonstruiere Winkel
    pred_angle = np.arctan2(pred_sin, pred_cos)      # [-pi, pi]
    target_angle = np.arctan2(target_sin, target_cos)  # [-pi, pi]

    # Zirkulaere Differenz
    diff = pred_angle - target_angle
    diff = np.arctan2(np.sin(diff), np.cos(diff))  # Wrap to [-pi, pi]

    # Konvertiere zu Dial-Einheiten
    dial_error = np.abs(diff) * 10.0 / (2 * math.pi)  # [0, 5.0]

    return dial_error


# Metriken im Validation Loop:
# - MAE_dial: mean(dial_error)                    -> Durchschnittsfehler in Dial-Einheiten
# - Within_0.1: (dial_error < 0.1).mean() * 100   -> % korrekt auf 0.1 genau
# - Within_0.5: (dial_error < 0.5).mean() * 100   -> % korrekt auf 0.5 genau
# - Max Error: max(dial_error)                     -> Worst Case
```

### 3.7 Inference Pipeline (Post-Processing)

```python
import math
import numpy as np

def sincos_predict(raw_output: np.ndarray) -> dict:
    """
    Konvertiere sin/cos Model-Output zu Dial-Position.

    Args:
        raw_output: Shape (2,) -- [sin_raw, cos_raw] vom Netz

    Returns:
        dict mit 'class' (str "0.0"-"9.9") und 'confidence' (float 0.0-1.0)
    """
    sin_val = float(raw_output[0])
    cos_val = float(raw_output[1])

    # Norm als Confidence-Proxy
    norm = math.sqrt(sin_val**2 + cos_val**2)

    # Winkel rekonstruieren (atan2 ist scale-invariant, braucht keine Normalization)
    angle = math.atan2(sin_val, cos_val)  # [-pi, pi]

    # Angle zu Dial-Position: angle=0 -> value=0.0, angle=2*pi -> value=10.0
    # atan2 gibt [-pi, pi], wir brauchen [0, 2*pi)
    if angle < 0:
        angle += 2 * math.pi

    # Dial-Position
    dial_position = angle * 10.0 / (2 * math.pi)  # [0.0, 10.0)

    # Auf 0.1 runden und clampen
    dial_position = round(dial_position, 1)
    if dial_position >= 10.0:
        dial_position = 0.0
    dial_position = max(0.0, min(9.9, dial_position))

    # Confidence: Norm-basiert
    # Typische Norm nach Training: 0.8-1.5
    # Niedrige Norm (<0.3) = unsicher, hohe Norm (>0.8) = sicher
    confidence = min(1.0, norm / 1.0)  # Normalisiert auf [0, 1], cap bei 1.0

    return {
        "class": f"{dial_position:.1f}",
        "confidence": confidence
    }
```

### 3.8 ONNX/OpenVINO Export

**Keine Aenderung am Export-Prozess noetig.** Der Export funktioniert identisch:
```python
model = timm.create_model('efficientnetv2_rw_s', pretrained=True, num_classes=2)
# ... training ...
model.cpu()
model.eval()
dummy = torch.randn(1, 3, 128, 128)
torch.onnx.export(model, dummy, "model.onnx", ...)
```

Das ONNX-Modell hat Output Shape `(1, 2)` statt `(1, 100)`. Die gesamte sin/cos -> atan2 -> dial Berechnung findet in Python statt (in `sincos_predict()`).

### 3.9 Metadata-Aenderungen

`metadata.json` im Model-Verzeichnis braucht ein neues Feld:
```json
{
    "training_mode": "sincos",
    "model_type": "arrows",
    "architecture": "efficientnetv2_rw_s",
    "resolution": 128,
    "num_outputs": 2,
    "output_format": "sin_cos",
    "encoding": "angle = value * 2*pi / 10"
}
```

Der `_detect_training_mode()` in `inference.py` muss `"sincos"` als dritten Modus erkennen und den entsprechenden `SinCosRegressor` instanziieren.

### 3.10 Edge Cases

| Situation | Behandlung |
|-----------|-----------|
| Output (0, 0) -- Origin | Confidence = 0.0, nutze letzte gueltige Vorhersage |
| Output mit extremer Norm (>10) | Normal -- atan2 funktioniert trotzdem, Confidence wird auf 1.0 gecappt |
| Dial-Position genau 10.0 nach Rundung | Wird zu 0.0 gemappt (Wrap-around) |
| NaN/Inf im Output | Catch in Post-Processing, Confidence = 0.0, Fallback |
| Bild komplett schwarz/weiss | Modell gibt trotzdem (sin, cos) aus, Confidence wird niedrig sein |

---

## 4. Confidence-Ranking der Empfehlungen

| Empfehlung | Confidence | Begruendung | Was wuerde mich umstimmen |
|-----------|-----------|-------------|---------------------------|
| **MSE Loss** | **HOCH (9/10)** | Theoretisch fundiert, praktisch bewaehrt, keine HPs | Systematische Fehler in bestimmten Winkelbereichen bei Experimenten |
| **Raw Outputs (keine Norm im Netz)** | **HOCH (8/10)** | atan2 ist scale-invariant, ONNX-kompatibel, einfacher | Output-Norm schwankt stark (>10x) und korreliert mit schlechter Performance |
| **2 Outputs (sin, cos) via num_classes=2** | **HOCH (9/10)** | Einfachster Weg, timm-kompatibel | Wenn hidden head noetig fuer Konvergenz (unwahrscheinlich bei pretrained backbone) |
| **WeightedRandomSampler** | **MITTEL (7/10)** | 3.2x Imbalance ist signifikant, einfach zu implementieren | Wenn Ablation zeigt dass es keinen Unterschied macht |
| **30 Epochen + CosineAnnealing** | **MITTEL (7/10)** | Regression braucht mehr Epochen als Classification | Wenn Validation Loss nach 15 Epochen konvergiert |
| **Norm als Confidence-Proxy** | **NIEDRIG (5/10)** | Heuristik, nicht kalibriert | Bessere Alternative: MC Dropout oder Ensemble, aber teurer |
| **Early Stopping (Patience=8)** | **MITTEL (7/10)** | Schuetzt gegen Overfitting bei kleinem Dataset | Wenn Dataset waechst (>5000 Bilder), kann Patience erhoehen |

---

## 5. Implementierungsreihenfolge

Die Umstellung sollte in 4 Work Packages (WPs) erfolgen:

### WP-1: Dataset & Training Core (Prio 1)
- `SinCosArrowDataset` in `training_core.py`
- `sincos_predict()` in `training_core.py`
- `angular_error_batch()` in `training_core.py`
- Unit Tests fuer Encoding/Decoding Round-Trip

### WP-2: Training Manager Integration (Prio 2)
- Neuer `training_mode="sincos"` in `_execute_training()`
- `num_classes=2`, MSE Loss, SequentialLR Scheduler
- WeightedRandomSampler Integration
- Progress-Updates mit Angular Error Metriken

### WP-3: Inference Pipeline (Prio 3)
- `SinCosRegressor` Klasse in `inference.py`
- `_detect_training_mode()` Update fuer "sincos"
- `sincos_predict()` Post-Processing Integration
- Metadata Schema Update

### WP-4: Benchmark & UI (Prio 4)
- Benchmark-Logik fuer sin/cos Modelle
- Angular Error Metriken im Benchmark-Report
- UI-Anpassungen in `training.html` (Training Mode Dropdown: discrete/sincos)

---

## 6. Zusammenfassung

**Das wichtigste Ergebnis dieser Analyse:** Sin/cos Encoding ist die richtige Wahl fuer das Arrow-Modell. Es loest den Wrap-around-Bug des aktuellen Sigmoid-Regressors, und die Implementierung ist ueberraschend einfach: `num_classes=2`, MSE Loss, atan2 im Post-Processing.

Die drei Researcher haben solide Grundlagenarbeit geleistet, aber alle haben den konkreten Bug im bestehenden Code uebersehen und die praktischen Deployment-Constraints (ONNX-Export, Quantisierung, kleines Dataset) nicht ausreichend beruecksichtigt.

**Meine staerkste Empfehlung: Start simple.** MSE, Raw Outputs, kein Custom Head. Messen, dann optimieren. Die erste Version sollte in einem Tag implementierbar sein.
