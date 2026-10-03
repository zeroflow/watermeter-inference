# Training Page Responsive Layout Fix

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the training page fit without clipping on half-1440p (1278px) and half-1080p (960px) viewports. Mobile layout should only trigger below ~900px.

**Architecture:** Two problems: (1) `.container` has `overflow: hidden` which silently clips wide content at any viewport, and (2) the mobile breakpoint at 1200px fires too early, turning half-1080p into a phone layout. Fix the overflow, lower the mobile breakpoint, and ensure all desktop elements fit within 848px content width (960px viewport minus 92px nav rail minus 20px right padding).

**Tech Stack:** CSS only — no JS changes

**Target viewports:**

| Viewport | Content area | Layout |
|----------|-------------|--------|
| 1278px (half 1440p) | 1166px | Full desktop |
| 960px (half 1080p) | 848px | Compact desktop |
| < 900px | < 788px | Mobile (existing layout) |

---

### Task 1: Fix `.container` overflow clipping

**Files:**
- Modify: `watermeter/static/style.css:130`

**Step 1: Change `overflow: hidden` to `overflow-x: auto`**

In `watermeter/static/style.css`, find the `.container` rule (around line 123-132). Change:

```css
overflow: hidden;
```
to:
```css
overflow-x: auto;
```

This allows horizontal scroll when content exceeds the container instead of silently clipping. This is the root cause of elements being "cut off on the right" at 1278px.

**Step 2: Verify no visual regressions at full desktop width**

Load the training page at full width — nothing should change. Load at 1278px — previously clipped elements should now be visible (or scroll).

**Step 3: Commit**

```bash
git add watermeter/static/style.css
git commit -m "claude: fix container overflow clipping — use overflow-x auto instead of hidden"
```

---

### Task 2: Lower mobile breakpoint from 1200px to 900px

**Files:**
- Modify: `watermeter/templates/training.html:990`

**Step 1: Change the breakpoint**

In `watermeter/templates/training.html`, find the responsive media query (line 990):

```css
@media (max-width: 1200px) {
```

Change to:

```css
@media (max-width: 900px) {
```

This stops the mobile layout from firing at half-1080p (960px). Mobile now only activates below 900px, which is well below the 960px target.

**Step 2: Remove `.section { overflow: hidden }` from the mobile block**

Inside the (now) `@media (max-width: 900px)` block, find (around line 997-999):

```css
.section {
    padding: 12px;
    overflow: hidden;
}
```

Remove the `overflow: hidden` line. The model table wrapper already has `overflow-x: auto` — the section shouldn't clip it.

```css
.section {
    padding: 12px;
}
```

**Step 3: Commit**

```bash
git add watermeter/templates/training.html
git commit -m "claude: lower training page mobile breakpoint from 1200px to 900px"
```

---

### Task 3: Make desktop layout fit at 848px content width

**Files:**
- Modify: `watermeter/templates/training.html` (CSS section)

At 960px viewport the full desktop layout now applies (since we moved mobile to 900px). Several elements need minor adjustments to fit in 848px of content width.

**Step 1: Add `flex-wrap: wrap` to `.progress-metrics` base rule**

Find `.progress-metrics` (around line 531-538). Add `flex-wrap: wrap`:

```css
.progress-metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
    margin-bottom: 16px;
    padding: 12px;
}
```

This lets metrics wrap naturally at narrow desktop widths instead of overflowing.

**Step 2: Reduce `.stats-grid` gap and allow reflow**

Find `.stats-grid` (around line 161-163). Change the fixed two-column grid to auto-fit with a reasonable minimum:

```css
.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 16px;
}
```

At 848px: `floor(848 / 380) = 2` columns → two columns fit. At very narrow widths it collapses to 1 column naturally. The mobile override that forces `1fr` in the `@media (max-width: 900px)` block still works as a fallback.

**Step 3: Add `flex-wrap: wrap` to `.form-actions` base rule**

Find `.form-actions` (around line 445-449). Add `flex-wrap: wrap`:

```css
.form-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
}
```

**Step 4: Reduce `.model-actions` button padding for compact fit**

Find `.model-actions button` (around line 740-747). Reduce padding:

```css
.model-actions button {
    padding: 4px 8px;
    font-size: 12px;
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
    flex: 1;
}
```

Changed `padding: 6px 12px` → `padding: 4px 8px`. This shaves ~32px off the actions column (4 buttons x 8px less padding).

**Step 5: Add `flex-wrap: wrap` to `.progress-header` base rule**

Find `.progress-header` (around line 473-478). Add `flex-wrap: wrap`:

```css
.progress-header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}
```

**Step 6: Verify at all target viewports**

Test the training page at:
- 1278px viewport — full desktop, no clipping, no overflow
- 960px viewport — compact desktop, all elements visible, table may scroll horizontally but should be accessible
- 899px viewport — mobile layout kicks in
- 768px viewport — mobile with bottom nav bar

**Step 7: Commit**

```bash
git add watermeter/templates/training.html
git commit -m "claude: make training page desktop layout fit at half-1080p (960px)"
```

---

## Summary

| Task | File | Change |
|------|------|--------|
| 1 | `style.css` | Fix container overflow: hidden → overflow-x: auto |
| 2 | `training.html` | Lower mobile breakpoint 1200px → 900px, remove section overflow: hidden |
| 3 | `training.html` | flex-wrap on metrics/actions/header, auto-fit stats grid, tighter button padding |

**Dependencies:** Task 1 is independent. Tasks 2 and 3 both touch `training.html` but different sections — can be done sequentially in one commit or separately.
