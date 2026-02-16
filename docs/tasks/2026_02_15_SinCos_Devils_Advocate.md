# Devil's Advocate Review: Sin/Cos Arrows Regression

**Datum**: 2026-02-15
**Rolle**: Adversarial Reviewer -- Fehler finden, Annahmen zerlegen, Alternativen aufzeigen
**Grundlage**: Konzeptdokument, Final Review, Senior Review, vollstaendiger Code-Review

---

## A. ML/Math-Korrektheit

### A.1 Die sin/cos-Kodierung ist korrekt -- aber das Konzept verkauft sie als schwieriger als sie ist

Die Mathematik stimmt. `theta = 2*pi*v/10`, Target `(sin(theta), cos(theta))`, Rueckrechnung via `atan2`. Das ist Lehrbuchmaterial fuer zyklische Regression, bekannt seit den 1990ern. Das Problem: **Drei Researcher und zwei Reviews fuer etwas, das in jedem ML-Kurs fuer Zeitreihen in einer halben Folie abgehandelt wird.** Die Zhou et al. CVPR 2019 Referenz ist intellektuell korrekt, aber masslos uebertrieben fuer diesen Anwendungsfall -- Zhou behandelt 6D Rotationsdarstellungen in SO(3), wir kodieren einen einzigen skalaren Winkel auf S^1.

**Verdikt: Mathematik korrekt, aber 3 Forschungsberichte + 2 Reviews dafuer zu bemuehen war Overkill.**

### A.2 MSE auf (sin, cos): Der Loss ist NICHT optimal, und das Konzept weiss es

Das Konzept behauptet: "MSE auf (sin, cos) ist die richtige Wahl." Das ist halbwahr. MSE auf (sin, cos) minimiert den **euklidischen Abstand im 2D-Raum**, nicht den **Winkelfehler**. Diese sind nur fuer kleine Winkel annaehernd proportional. Konkret:

```
Euklidischer Abstand zwischen (sin(a), cos(a)) und (sin(b), cos(b)):
d = sqrt(2 - 2*cos(a-b)) = 2*|sin((a-b)/2)|

Winkelfehler: |a - b|

Fuer kleine Fehler: d ≈ |a-b|  (gut)
Fuer grosse Fehler: d = 2*sin(|a-b|/2) ≤ 2  (saturiert!)
```

**Das bedeutet:** MSE bestraft einen Fehler von 5.0 Dial-Einheiten (gegenueberliegender Punkt) mit euklidischem Abstand 2.0, aber einen Fehler von 2.5 Dial-Einheiten mit Abstand sqrt(2) = 1.41. Der Loss **saturiert fuer grosse Fehler** -- das Gradient-Signal fuer komplett falsche Vorhersagen ist schwaech als es sein sollte.

In der Praxis? Wahrscheinlich irrelevant, weil ein pretrained Backbone selten Vorhersagen macht, die um pi+ danebenliegen. Aber das Konzept haette das diskutieren muessen, statt MSE als perfekt zu verkaufen.

### A.3 Confidence = Norm: Theoretisch fragwuerdig, praktisch ungetestet

Das Konzept postuliert: "Norm des Output-Vektors als Confidence-Proxy". Die Begruendung: "Grosse Norm = ueberzeugt, kleine Norm = unsicher." **Das ist eine unbelegte Heuristik.**

Das Problem: MSE-Targets liegen auf dem Einheitskreis (Norm = 1.0). Der Loss drueckt die Outputs also **immer in Richtung Norm 1.0**, egal ob das Modell "sicher" oder "unsicher" ist. Es gibt keinen Trainings-Mechanismus, der das Modell belohnt, die Norm als Unsicherheitssignal zu nutzen.

Was tatsaechlich passiert:
- Ein gut trainiertes Modell wird Outputs nahe am Einheitskreis produzieren (Norm ca. 0.8-1.2)
- Die Norm-Varianz wird **klein** sein, weil MSE genau das erzwingt
- Die Confidence wird fuer fast alle Vorhersagen zwischen 0.8 und 1.0 liegen -- **uninformativ**

Die Sigmoid-Confidence (`|sigmoid - 0.5| * 2`) des aktuellen Regressors ist zwar auch eine Heuristik, aber sie hat wenigstens ein natuerliches "unsicheres Zentrum" bei sigmoid=0.5. Die Norm-Heuristik hat kein solches Zentrum.

**Empfehlung:** Wenn Confidence wichtig ist (und fuer den Mislabel-Scan ist sie es), waere Test-Time Augmentation (TTA) oder MC-Dropout ein belastbarerer Ansatz. Fuer V1 koennte man die Norm nehmen, muss aber im Benchmark validieren, dass sie tatsaechlich mit der Vorhersagequalitaet korreliert.

### A.4 "atan2 ist scale-invariant" -- korrekt, aber MSE ist es NICHT

Das Konzept argumentiert: "Keine Normalisierung noetig, weil atan2 scale-invariant ist." Das stimmt fuer die **Inferenz**. Aber waehrend des **Trainings** ist MSE gerade **nicht** scale-invariant:

```
Target: (sin(theta), cos(theta))     -- Norm 1.0
Output: (2*sin(theta), 2*cos(theta)) -- Norm 2.0, GLEICHER WINKEL

MSE = (2*sin - sin)^2 + (2*cos - cos)^2 = sin^2 + cos^2 = 1.0

Aber fuer ein Output auf dem Einheitskreis mit korrektem Winkel:
MSE = 0.0
```

**Ein Output mit perfektem Winkel aber doppelter Norm hat MSE = 1.0, nicht 0.0.** Das Netz wird also trainiert, sowohl den richtigen Winkel ALS AUCH die richtige Norm (1.0) zu treffen. Das ist an sich kein Bug -- es ist sogar wuenschenswert, weil es die Outputs nahe am Einheitskreis haelt. Aber es widerspricht der Behauptung "die Norm ist egal" waehrend des Trainings.

Der perverse Anreiz: Wenn das Netz den Winkel nicht genau treffen kann, koennte es die Norm reduzieren, um den MSE-Loss zu senken. Beispiel:

```
Target: (0.0, 1.0)  -- 0 Grad
Output: (0.5, 0.0)  -- 90 Grad, Norm 0.5
MSE = 0.5^2 + 1.0^2 = 1.25

Alternatives Output: (0.0, 0.0) -- undefiniert, Norm 0.0
MSE = 0.0^2 + 1.0^2 = 1.0  ← NIEDRIGERER Loss trotz voellig sinnloser Vorhersage!
```

In der Praxis lernt das Netz natuerlich nicht, (0,0) auszugeben, weil das fuer ALLE Targets suboptimal ist. Aber in fruehen Trainingsphasen oder bei ambigen Bildern koennte die Norm-Reduktion ein Ausweichemanismus sein.

**Fazit:** Nicht kritisch, aber das Konzept sollte ehrlich sein: MSE auf (sin,cos) trainiert implizit sowohl Winkel als auch Norm. Das ist kein Bug, aber es ist auch nicht "die Norm ist irrelevant".

---

## B. Implementierbarkeit

### B.1 15 elif-Branches: Technische Schulden, die das Konzept wissentlich eingeht

Das Konzept und der Final Review erkennen beide, dass `_execute_training()` bereits 15 Branches fuer `training_mode == "continuous"` hat. Die Loesung: **15 weitere `elif`-Branches.** Der Final Review schlaegt ein Strategy-Pattern vor, sagt aber sofort: "Wenn Zeitdruck besteht, kann man das dritte elif einfuegen."

**Das ist der falsche Kompromiss.** Nach der Implementierung hat die Funktion **30+ Mode-Branches in einer 400-Zeilen-Funktion.** Das ist nicht "haesslich aber funktional" -- das ist eine aktive Quelle von Bugs. Das Konzept selbst identifiziert "Copy-Paste sigmoid() in sincos-Branch" als **hoechstes Risiko**. Warum? Weil die Funktion so lang und repetitiv ist, dass Copy-Paste-Fehler quasi garantiert sind.

**Gegenvorschlag:** Vor der sincos-Implementation ein Refactoring in WP-0: Die drei Modi als separate Funktionen (`_train_classification`, `_train_regression`, `_train_sincos`) extrahieren, die eine gemeinsame Hilfsfunktion fuer den Boilerplate (DataLoader, Export, Metadata) nutzen. Das kostet 2-3 Stunden, spart aber Debugging-Zeit und verhindert die Copy-Paste-Fehler, die das Konzept selbst als groesstes Risiko benennt.

Alternativ, noch einfacher: Den `"continuous"` Modus **komplett durch `"sincos"` ersetzen** (siehe C.3).

### B.2 "8-12 Stunden" -- realistisch oder optimistisch?

Das Konzept schaetzt 8-12 Stunden. Bei Agent-basierter Entwicklung bedeutet das:

- 4 Work Packages, jedes braucht: Prompt schreiben, Agent ausfuehren, Output reviewen, Fixes einplanen
- Pro WP: mindestens 2-3 Agent-Zyklen (erster Versuch + Fixes)
- Plus Testing, Review, Integration

Meine realistische Schaetzung: **12-18 Stunden Agent-Zeit, 2-3 Stunden Koordinationszeit.** Das ist kein "ein Tag Arbeit", das ist eher 2-3 Tage mit Unterbrechungen.

Das waere akzeptabel, wenn der Nutzen klar waere. Aber ist er das? (Siehe C.1)

### B.3 Simpler Alternative: Circular MSE Loss auf dem existierenden Regressor

Bevor man einen komplett neuen Trainingspfad implementiert, sollte man die einfachste Loesung betrachten: **Den Wraparound-Bug im existierenden Sigmoid-Regressor fixen, statt einen neuen Modus zu bauen.**

```python
# Circular MSE Loss fuer den bestehenden 1-Output-Regressor
def circular_mse_loss(pred, target, period=1.0):
    """MSE auf dem kuerzesten Weg um den Kreis."""
    diff = pred - target
    diff = diff - period * torch.round(diff / period)  # Wrap to [-period/2, period/2]
    return (diff ** 2).mean()

# Anwendung: pred und target in [0, 1), period = 1.0
loss = circular_mse_loss(torch.sigmoid(outputs), labels, period=1.0)
```

**Aufwand:** ~20 Zeilen Code-Aenderung. Kein neues Dataset, keine neuen Branches, kein neuer Inference-Pfad.

**Warum das Konzept das nicht erwaehnt:** Keiner der drei Researcher hat den existierenden Code analysiert. Alle sind direkt auf sin/cos gesprungen, weil es die "elegantere" Loesung ist. Aber eleganter != besser fuer ein Projekt mit 1794 Bildern und einer Einmann-Maintenance.

**Einschraenkung:** Circular MSE auf Sigmoid hat ein subtiles Problem: Sigmoid kann 0.0 und 1.0 nicht sauber darstellen (asymptotisch). Der "Sprung" von 0.99 nach 0.01 existiert immer noch im Funktionsraum des Netzes, auch wenn der Loss ihn bestraft. Sin/cos hat dieses Problem nicht, weil die Repraesentation nativ zirkulaer ist. **Sin/cos ist die sauberere Loesung, aber Circular MSE waere ein valider Quickfix.**

---

## C. Dinge, die niemand gefragt hat

### C.1 KRITISCH: Existiert der Wraparound-Bug IN DER PRAXIS?

Das Konzept beschreibt den Bug theoretisch korrekt: 9.9 und 0.0 haben maximalen Sigmoid-Abstand. Aber **wie viele Bilder liegen tatsaechlich im kritischen Bereich?**

Datenlage (aus dem aktuellen Ground Truth):

| Klasse | Bilder |
|--------|--------|
| 9.7    | 14     |
| 9.8    | 10     |
| 9.9    | 13     |
| 0.0    | 29     |
| 0.1    | 22     |
| 0.2    | 24     |
| 0.3    | 19     |

Von 1794 Bildern liegen **37 im engen Wraparound-Bereich (9.8-0.1)**, das sind **2.1% des Datasets.** Davon sind die echten Problemfaelle (wo das Sigmoid-Modell falsch liegt) noch weniger -- das Modell muss 9.9 und 0.0 ja nicht gleich vorhersagen, es muss nur jeweils korrekt vorhersagen.

**Frage:** Hat jemand ueberhaupt gemessen, ob das aktuelle Sigmoid-Modell im Bereich 9.8-0.1 tatsaechlich schlechter performt als im Rest? **Nein.** Es gibt kein Benchmark-Ergebnis, das den Bug quantifiziert. Das gesamte Konzept basiert auf der THEORETISCHEN Analyse, dass Sigmoid fuer zirkulaere Werte schlecht ist. Das ist wahr -- aber der Effekt koennte bei 37 Bildern vernachlaessigbar klein sein.

**Empfehlung:** Vor der Implementierung einen Benchmark des aktuellen Sigmoid-Modells ausfuehren und die per-Klassen-Accuracy fuer den Bereich 9.5-0.5 analysieren. Wenn die Accuracy dort nicht signifikant schlechter ist als im Durchschnitt, lohnt sich der Aufwand nicht.

### C.2 Ist 1794 Bilder genug fuer IRGENDEIN Regressionsmodell?

Das Dataset hat:
- 100 Klassen (0.0 bis 9.9)
- Im Schnitt 17.9 Bilder pro Klasse
- Minimum 9 Bilder pro Klasse (!)
- 3.2x Imbalance-Ratio

Ein EfficientNetV2-S hat **21 Millionen Parameter**. Selbst mit pretrained Backbone und nur 1280*2 = 2560 trainierbaren Parametern im Head: **Das Verhaeltnis von trainierbaren Parametern zu Datenpunkten ist kritisch.** Fine-Tuning mit 1794 Bildern funktioniert erfahrungsgemaess, aber die Varianz zwischen Seeds wird hoch sein.

Fuer sin/cos-Regression kommt hinzu: Es gibt keine diskreten Klasssen mehr, die als "Anker" dienen. Das Modell muss eine kontinuierliche Abbildung lernen -- mit 9 Bildern pro Klasse an den duennsten Stellen. Ein Classifier kann sich "0.1 sieht anders aus als 0.2" einpraegen; ein Regressor muss "0.15 liegt zwischen 0.1 und 0.2" interpolieren. Das ist mit 9-29 Bildern pro Position schwierig.

**Die unausgesprochene Wahrheit:** Der Aufwand fuer sin/cos waere besser in **Datensammlung** investiert. Der Backup-Datensatz hat 4456 Bilder (2.5x mehr). Warum wird der nicht verwendet? Wenn es Qualitaetsprobleme gibt, waere Ground-Truth-Cleanup + der bestehende Classifier wahrscheinlich effektiver als ein neuer Regressionsmodus.

### C.3 Warum drei Modi beibehalten? Continuous ist kaputt, ersetze es.

Das Konzept fuegt `"sincos"` als **dritten** Modus neben `"discrete"` und `"continuous"` hinzu. Aber:

- `"continuous"` hat den Wraparound-Bug -- das ist die MOTIVATION fuer das ganze Projekt
- `"continuous"` wurde (soweit erkennbar) nie produktiv eingesetzt
- `"sincos"` ist in jeder Hinsicht besser als `"continuous"`

**Warum `"continuous"` nicht einfach durch `"sincos"` ersetzen?** Das wuerde:
- 0 neue Branches erzeugen (die 15 bestehenden `continuous`-Branches werden zu sincos-Branches)
- Die Code-Komplexitaet unveraendert lassen statt sie um 50% zu erhoehen
- Den UI-Dropdown vereinfachen (nur 2 statt 3 Optionen)

**Argument dagegen:** "Bestehende continuous-Modelle wuerden nicht mehr laden." Gibt es solche? Wenn nein: Drop-In-Replacement. Wenn ja: Migration-Path (Modell-Metadata pruefen, altes Modell mit Legacy-Code laden, neues Training empfehlen).

**Meine starke Empfehlung: Ersetze `"continuous"` durch `"sincos"`, fuege keinen dritten Modus hinzu.** Das halbiert den Implementierungsaufwand und eliminiert das groesste Risiko (die 15 neuen Branches).

---

## D. Alternative Ansaetze, die nicht betrachtet wurden

### D.1 Circular MSE auf dem bestehenden 1-Output-Regressor

Bereits in B.3 beschrieben. Aufwand: ~20 Zeilen. Loest den Wraparound-Bug zu 80-90%. Nachteil: Sigmoid hat an den Raendern 0 und 1 steile Gradienten, die zu Instabilitaet fuehren koennen.

### D.2 Ordinal Regression

Die 100 Arrow-Klassen haben eine natuerliche Ordnung (0.0 < 0.1 < ... < 9.9, zirkulaer). Ordinal Regression (z.B. CORAL, Niu et al. 2016) modelliert kumulative Wahrscheinlichkeiten und respektiert die Ordnung. **Vorteile gegenueber sin/cos:**
- Interpretierbare Uncertainty (Wahrscheinlichkeitsverteilung ueber benachbarte Klassen)
- Keine neue Inference-Pipeline noetig (Output ist immer noch eine Klasse)
- Natuerliche Confidence-Metrik (wie "peaky" ist die Verteilung?)

**Nachteil:** Ordinal Regression ist nicht nativ zirkulaer -- aber man koennte den Kreis bei 5.0 aufschneiden (maximale Distanz zum Wraparound) und so den gleichen Effekt erzielen. Nicht perfekt, aber deutlich simpler als sin/cos.

**Warum wurde das nicht erwaehnt?** Keiner der Researcher hat es vorgeschlagen. Das deutet darauf hin, dass die Forschungsfrage zu eng gestellt war ("Wie machen wir sin/cos?" statt "Was ist die beste Loesung fuer zirkulaere Regression?").

### D.3 Klassifikation mit zirkulaerem Label Smoothing

Der bestehende 100-Klassen-Classifier funktioniert. Sein Problem bei Wraparound: Hard Labels behandeln 9.9 und 0.0 als komplett verschiedene Klassen. **Label Smoothing, das den Kreis respektiert**, wuerde das beheben:

```python
# Statt harter Labels [0, 0, ..., 1, ..., 0]:
# Zirkulaeres Smoothing: Wahrscheinlichkeit faellt mit zirkulaerer Distanz ab
def circular_label_smoothing(target_class, num_classes=100, sigma=1.0):
    labels = torch.zeros(num_classes)
    for i in range(num_classes):
        # Zirkulaere Distanz
        dist = min(abs(i - target_class), num_classes - abs(i - target_class))
        labels[i] = exp(-dist**2 / (2 * sigma**2))
    return labels / labels.sum()
```

**Aufwand:** ~30 Zeilen Code-Aenderung, kein neuer Inference-Pfad, keine neuen Branches. Der Classifier bleibt, nur der Loss aendert sich.

**Nachteil:** Die Vorhersage ist immer noch diskret (eine von 100 Klassen), nicht kontinuierlich. Aber fuer einen Wasserzaehler mit 0.1er-Aufloesung ist das exakt das, was man braucht.

### D.4 Warum werden die Alternativen abgelehnt?

Ich behaupte: **Keine dieser Alternativen wurde ernsthaft evaluiert, weil die Aufgabestellung bereits "Sin/Cos" im Titel trug.** Die Researcher sollten sin/cos untersuchen, nicht die optimale Loesung finden. Das ist ein klassischer Confirmation Bias im Forschungsprozess.

---

## E. Prozesskritik

### E.1 Drei Researcher, zwei Reviews, null Zeilen Code

Das Konzept wurde erstellt durch:
1. Researcher 1 (Loss Functions)
2. Researcher 2 (Normalization)
3. Researcher 3 (Architecture & Practical)
4. Senior Review (Konsolidierung)
5. Final Review (Code-Integration)

Fuenf Agent-Aufrufe, bevor eine einzige Zeile Code geschrieben wurde. Der resultierende Konzeptdokument ist 414 Zeilen lang. Fuer ein Feature, das im Kern aus ~200 Zeilen neuem Python-Code besteht.

**Ueberfluss-Analyse:**
- **Researcher 1 und 2 haetten ein Agent sein koennen.** Die Frage "welcher Loss?" und "welche Normalisierung?" sind nicht unabhaengig voneinander.
- **Der Senior Review hat 90% der Arbeit geleistet.** Die Researcher-Berichte waren Rohmaterial, das der Senior Review ohnehin komplett neu aufgearbeitet hat.
- **Der Final Review war wertvoll** -- er hat die konkreten Code-Stellen identifiziert. Aber er haette der ERSTE Schritt sein sollen, nicht der letzte.

**Optimaler Prozess:**
1. Planner: "Welche Dateien muessen sich aendern?" (Final Review zuerst)
2. Senior-Dev: "Wie sieht die Implementation konkret aus?" (Konzept + Code-Snippets)
3. Implementierung.

Drei statt fuenf Schritte. Gleicher Output, halbe Token-Kosten.

### E.2 Das Konzept-Dokument ist laenger als die Implementation

414 Zeilen Konzept fuer geschaetzte 200-300 Zeilen neuen Code. Das Verhaeltnis Dokumentation:Code liegt bei 1.5:1. Das ist fuer eine Dissertation angemessen, fuer ein Hobby-Projekt mit einem Maintainer ist es Overengineering.

Zum Vergleich: Der gesamte `RegressionArrowDataset` + `regression_predict` + `stratified_split_regression` Code sind zusammen 70 Zeilen. Der sincos-Equivalent wird aehnlich lang sein.

### E.3 Die Forschungsfrage war zu eng gestellt

"Untersuche sin/cos-Encoding fuer das Arrow-Modell" statt "Finde die beste Loesung fuer den Wraparound-Bug im Arrow-Modell." Ersteres schliesst D.1-D.3 aus. Das ist kein Vorwurf an die Researcher (sie haben ihre Aufgabe korrekt erfuellt), sondern an die Aufgabenstellung.

---

## F. Was gut ist (kurz)

1. **Die Problemanalyse ist korrekt.** Der Sigmoid-Regressor hat einen mathematischen Defekt bei Wraparound. Sin/cos behebt ihn.
2. **Die Entscheidung gegen Normalisierung im Netz ist richtig.** atan2 in Python ist numerisch sicherer als L2-Norm im ONNX-Export.
3. **Der Final Review hat excellente Arbeit geleistet** bei der Identifikation aller Integrationspunkte.
4. **Das Metadata-Konzept (training_mode im Modell statt in der Config) ist sauber.**
5. **Die Edge-Case-Analyse (10.0 -> 0.0, NaN-Output, Origin Singularity) ist gruendlich.**

---

## Verdikt: REVISE

### Bedingungen fuer PROCEED:

1. **Quantifiziere den Bug zuerst.** Fuehre einen Benchmark des aktuellen Sigmoid-Modells (falls vorhanden) oder eines schnell trainierten durch und analysiere die per-Klassen-Accuracy im Bereich 9.5-0.5. Wenn der Bug weniger als 5% Accuracy-Verlust im Wraparound-Bereich verursacht, ist der Aufwand fraglich.

2. **Ersetze `"continuous"` statt einen dritten Modus hinzuzufuegen.** Wenn es keine produktiven continuous-Modelle gibt, ist ein Drop-In-Replacement die richtige Strategie. 0 neue Branches statt 15.

3. **Evaluiere Circular Label Smoothing als Alternative.** 30 Zeilen Code-Aenderung am bestehenden Classifier vs. 200+ Zeilen neuer Regressionspfad. Wenn CLS 90% des Wraparound-Problems loest, ist sin/cos ueberdimensioniert.

4. **Kuerze das Konzept auf das Wesentliche.** Die Implementation braucht: Dataset-Klasse, Predict-Funktion, Training-Branch, Inference-Klasse. Nicht 414 Zeilen Prosa.

5. **Refactore _execute_training() VORHER (WP-0).** Wenn doch ein dritter Modus eingefuehrt wird: Die 400-Zeilen-Funktion in Strategien aufteilen. Das kostet 2-3 Stunden, verhindert aber die Copy-Paste-Bugs, die das Konzept selbst als groesstes Risiko benennt.

### Was das Verdikt REJECT statt REVISE machen wuerde:

- Wenn der Benchmark zeigt, dass das aktuelle System im Wraparound-Bereich NICHT schlechter performt als anderswo.
- Wenn das Dataset zu klein ist und ein sin/cos-Modell schlechter performt als der bestehende 100-Klassen-Classifier (weil Regression mit 9 Bildern pro Position nicht zuverlaessig ist).

### Was das Verdikt zu PROCEED aendern wuerde:

- Entscheidung: Replace statt Add (continuous -> sincos, kein dritter Modus)
- Nachweis: Benchmark zeigt messbaren Wraparound-Defekt im aktuellen System
- Alternativ-Evaluation: Circular Label Smoothing getestet und verworfen mit Begruendung
