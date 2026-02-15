# BL-11: Translate UI to English

## Summary
Replace all remaining German text with English across templates, backend, and documentation.
Breaking changes OK (unreleased). No i18n framework — hardcoded English.

Key terminology: "Zählerstand" → "Meter Reading"

## Findings

### Templates (user-facing)
| File | Line | German | English |
|------|------|--------|---------|
| `label.html` | 2 | `<html lang="de">` | `<html lang="en">` |
| `training.html` | 2 | `<html lang="de">` | `<html lang="en">` |
| `roi_config.html` | 2 | `<html lang="de">` | `<html lang="en">` |
| `dashboard.html` | 2 | `<html lang="de">` | `<html lang="en">` |
| `status_fragment.html` | 3 | MQTT nicht verbunden — Daten werden nicht an Home Assistant gesendet. | MQTT not connected — Data will not be sent to Home Assistant. |
| `status_fragment.html` | 9 | Keine Daten verfügbar | No data available |
| `status_fragment.html` | 10 | Klicke auf "Jetzt Auslesen" um eine Messung zu starten. | Click "Read Now" to start a measurement. |
| `status_fragment.html` | 15 | Verarbeitung läuft... | Processing... |
| `status_fragment.html` | 16 | Bilder werden geladen und analysiert | Loading and analyzing images |
| `status_fragment.html` | 23 | Aktueller Zählerstand | Current Meter Reading |
| `status_fragment.html` | 38 | Fehler aufgetreten / Warnungen | Error occurred / Warnings |
| `status_fragment.html` | 55 | Zählerstände (Digits + Arrows) | Meter Readings (Digits + Arrows) |
| `dashboard.html` | 21 | Jetzt Auslesen | Read Now |
| `dashboard.html` | 28 | Previous Value zurücksetzen? | Reset previous value? |
| `dashboard.html` | 47 | Lade Status... | Loading status... |

### Python (backend)
| File | Line | German | English |
|------|------|--------|---------|
| `routes/service.py` | 119 | Bild gespeichert: {filename} | Image saved: {filename} |
| `routes/service.py` | 127 | Fehler beim Speichern: {str(e)} | Error saving: {str(e)} |

### Documentation
| File | Status |
|------|--------|
| `Progress.md` | Extensively German — translate to English |
| `Integrated_training.md` | Extensively German — translate to English |

## Work Packages

### WP-1: Templates (`frontend`)
Translate all German strings in `templates/status_fragment.html` and `templates/dashboard.html`.
Change `lang="de"` to `lang="en"` in all 4 template files.

### WP-2: Backend strings (`junior-dev`)
Translate 2 German strings in `routes/service.py`.

### WP-3: Documentation (`junior-dev`)
Translate `Progress.md` and `Integrated_training.md` to English.
