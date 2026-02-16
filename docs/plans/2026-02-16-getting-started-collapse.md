# Getting Started Block Auto-Collapse Bugfix

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the "Getting Started" block on the Training page so it auto-collapses when enough training data exists.

**Architecture:** Two-bug fix — the HTML `<details>` element is missing an `id` attribute that the JavaScript references, causing the show/hide logic to silently fail. Additionally, the behavior should be changed from fully hiding the section to collapsing the `<details>` element, so users can still re-expand it if needed.

**Tech Stack:** HTML (Jinja2 template), vanilla JavaScript

---

### Root Cause

1. `training.html:1060` has `<details open>` but no `id` attribute
2. `training.js:300` does `document.getElementById('getting-started-section')` → returns `null`
3. The subsequent `.style.display = ...` throws a TypeError, caught silently by the try/catch at line 307
4. Result: Getting Started block is **always visible and expanded**, regardless of training data

### Task 1: Fix the HTML template — add missing id

**Files:**
- Modify: `watermeter/templates/training.html:1060`

**Step 1: Add id to the `<details>` element**

In `watermeter/templates/training.html`, change line 1060 from:
```html
                <details open>
```
to:
```html
                <details id="getting-started-details" open>
```

Note: We put the `id` on the `<details>` element (not the `<section>`) because we want to toggle its `open` attribute directly.

**Step 2: Commit**

```bash
git add watermeter/templates/training.html
git commit -m "claude: add missing id to getting-started details element"
```

---

### Task 2: Fix the JS — collapse instead of hide

**Files:**
- Modify: `watermeter/static/training.js:298-305`

**Step 1: Change the show/hide logic to toggle the `open` attribute**

In `watermeter/static/training.js`, replace lines 298-305:
```javascript
        // Show "Getting Started" section only if there are classes with 0 images
        const hasEmptyClasses = checkForEmptyClasses(digitsStats, arrowsStats, totalDigits, totalArrows);
        const gettingStartedSection = document.getElementById('getting-started-section');
        if (hasEmptyClasses) {
            gettingStartedSection.style.display = 'block';
        } else {
            gettingStartedSection.style.display = 'none';
        }
```

with:
```javascript
        // Auto-collapse "Getting Started" when all classes have data; expand if classes are missing
        const hasEmptyClasses = checkForEmptyClasses(digitsStats, arrowsStats, totalDigits, totalArrows);
        const gettingStartedDetails = document.getElementById('getting-started-details');
        if (gettingStartedDetails) {
            if (hasEmptyClasses) {
                gettingStartedDetails.setAttribute('open', '');
            } else {
                gettingStartedDetails.removeAttribute('open');
            }
        }
```

Key changes:
- References `getting-started-details` (the `<details>` element) instead of `getting-started-section`
- Toggles the `open` attribute instead of `display` CSS property
- Adds null-check (`if (gettingStartedDetails)`) for safety
- When data is complete: collapses the details (user can still expand manually)
- When data is missing: expands the details to guide the user

**Step 2: Run tests**

```bash
.venv/bin/python -m pytest tests/ -x -q
```

Expected: all tests pass (this is a JS-only change, Python tests should be unaffected).

**Step 3: Commit**

```bash
git add watermeter/static/training.js
git commit -m "claude: collapse getting-started block when enough training data exists"
```

---

### Task 3: Manual verification with Playwright

**Step 1: Start debug container if not running**

```bash
./debug.sh --detach
```

**Step 2: Navigate to training page and verify collapse behavior**

Using Playwright MCP, navigate to `http://localhost:8002/training` and:
1. Take a snapshot — check if the "Getting Started" `<details>` is present
2. If data exists, verify the details is collapsed (no `open` attribute)
3. If no data, verify the details is expanded (`open` attribute present)

---

### Threshold Logic (unchanged)

The existing `checkForEmptyClasses()` function (training.js:313-345) is kept as-is. It returns `true` (expand getting started) when:
- No data at all (fresh install)
- Any digit class (0-9, NAN) has 0 images (when some digit data exists)
- Any arrow class (0.0-9.9) has 0 images (when some arrow data exists)

This is reasonable: once every class has at least 1 sample, the user has a working dataset and the getting started guide auto-collapses.
