# BL-13: UI Beautification

## Context

After inspecting all five pages (Dashboard, Label, Training, ROI Config, Config Editor), their templates, `style.css`, and the navigation partial, the following pain points were identified:

### Primary Pain Point: Dashboard Status/Error Badge

The `.status` badge inside `.total-value .meta` is the main offender. Currently it renders as:
- A small `<span>` with `padding: 6px 16px`, `border-radius: 20px`, `font-size: 12px`, uppercase text
- The "ERROR" state uses `background: rgba(220, 38, 38, 0.3)` -- a translucent red on the gradient background
- This looks washed out and hard to read because it's a semi-transparent color on a purple/blue gradient
- The "OK" state has the same problem with `rgba(5, 150, 105, 0.3)`
- The timestamp next to it shows "None" when no timestamp is available, which looks broken

The error/warning section below the meter reading card also has issues:
- The `.warnings` and `.error` divs use simple `border-left` styling that looks dated
- No icon differentiation between warning and error states (both use the same warning emoji)
- The error div class `.error` is a very generic name and could conflict

### Cross-Page Issues Identified

1. **Inconsistent page titles in headers**: The Label page and Config Editor page both show "AI Water Meter Dashboard" as their `<h1>` instead of their actual page name. Only ROI Config has its own title. The Training page also says "AI Water Meter Dashboard".

2. **Missing `nav_active` on some pages**: The nav highlight may not work correctly if `nav_active` is not set in the template context. Need to verify this is passed correctly for all pages.

3. **Status message bar inconsistency**: Dashboard uses `.status-message` (blue bar), Label uses `.message` (floating toast), Training uses `.message` (floating toast), Config Editor uses `.message-toast` (floating toast). Three different patterns for the same concept.

4. **Image card submit buttons**: The magnifying glass button on dashboard image cards has no label/tooltip beyond `title="Submit for training"`. The button looks like a search icon rather than a submit action.

5. **Confidence badge styling on dashboard vs ROI**: Dashboard uses `.confidence-high/medium/low` on the card border + a colored background on the confidence text. This works but the confidence text colors could be stronger.

6. **Training page header badge**: The "Training: Idle" badge in the header uses `.badge.badge-idle` with `background: var(--bg-light); color: var(--text-light)` -- this is barely visible against the dark header.

7. **No favicon**: Console shows 404 for `/favicon.ico`.

8. **Empty `.header-actions` div**: Config Editor has an empty `<div class="header-actions"></div>` in the header, wasting space.

9. **Gradient background bleeds**: The body gradient (`linear-gradient(135deg, #667eea 0%, #764ba2 100%)`) is visible around the edges of every page. On the label page in particular, it looks odd on the left side next to the nav bar.

10. **Dashboard image card ID labels**: The "D1 (x100)" and "A1 (x0.1)" labels use `font-size: 16px` which is large for an identifier, making the cards feel cramped.

## Work Packages

### WP-1: Dashboard Status Badge Overhaul
- **Agent**: frontend
- **Files**: `watermeter/static/style.css`, `watermeter/templates/status_fragment.html`
- **Task**: Fix the ugly status/error badge and improve the error/warning sections
- **Details**:
  1. **Status badge** (`.total-value .status`): Replace translucent backgrounds with solid, high-contrast pill badges:
     - `.status.ok`: solid green background (`#059669`), white text
     - `.status.warning`: solid amber background (`#d97706`), white text
     - `.status.error`: solid red background (`#dc2626`), white text
     - Add a subtle `box-shadow` for depth (e.g., `0 2px 4px rgba(0,0,0,0.2)`)
     - Increase `font-size` from 12px to 13px for readability
  2. **Timestamp display**: In `status_fragment.html`, change `{{ last_update }}` to show a fallback when `None`: `{{ last_update or 'N/A' }}`
  3. **Error section** (`.error`): Add a slightly rounded container style, improve the error icon, increase padding:
     - Add `border-radius: 8px` (already has this)
     - Use a red-tinted background with slightly more opacity for contrast
  4. **Warning section** (`.warnings`): Same treatment -- ensure consistent padding and border-radius

### WP-2: Page Header Consistency
- **Agent**: frontend
- **Files**: `watermeter/templates/label.html`, `watermeter/templates/config_editor.html`, `watermeter/templates/training.html`
- **Task**: Give each page its own contextual header title instead of the generic "AI Water Meter Dashboard"
- **Details**:
  1. `label.html`: Change `<h1>` from "AI Water Meter Dashboard" to "Label Images"
  2. `config_editor.html`: Change `<h1>` from "AI Water Meter Dashboard" to "Config Editor" (and remove the redundant `<h2>Config Editor</h2>` from the toolbar, replacing it with just the file path or "config.yaml")
  3. `training.html`: Change `<h1>` from "AI Water Meter Dashboard" to "Training"
  4. Remove the empty `<div class="header-actions"></div>` from `config_editor.html` header, or move the toolbar actions (Reload, Save) into the header for consistency
  5. Remove the water drop emoji from label.html header (it has it but other pages don't)

### WP-3: Training Page Header Badge Fix
- **Agent**: frontend
- **Files**: `watermeter/templates/training.html` (inline styles)
- **Task**: Make the "Training: Idle" / "Training: Running" badge visible against the dark header
- **Details**:
  1. `.badge-idle`: Change from `background: var(--bg-light); color: var(--text-light)` to a subtle outline style: `background: transparent; border: 1px solid rgba(255,255,255,0.3); color: rgba(255,255,255,0.7)`
  2. `.badge-running`: Keep the orange `var(--warning)` background but ensure white text, and add a subtle pulse animation to draw attention
  3. Add a new `.badge-completed` style: `background: var(--success); color: white`

### WP-4: Dashboard Image Card Polish
- **Agent**: frontend
- **Files**: `watermeter/static/style.css`
- **Task**: Refine the dashboard image cards for cleaner visual presentation
- **Details**:
  1. Reduce `.image-card .id` font-size from `16px` to `12px` and use `color: var(--text-light)` to de-emphasize the identifier
  2. Increase `.image-card .prediction` to `20px` to make the actual prediction value more prominent (currently 16px)
  3. Give `.image-card .submit-btn` a smaller, icon-only appearance: reduce padding, make it round (`border-radius: 50%; width: 28px; height: 28px; padding: 0`), and position it in the top-right corner of the card using `position: absolute`
  4. Add a subtle separator between the digits row and arrows row -- a thin vertical divider line or a small gap with a visual break
  5. Add `border-top` with confidence color (2-3px solid) to image cards instead of full border, for a cleaner look

### WP-5: Cross-Page Spacing and Typography Cleanup
- **Agent**: frontend
- **Files**: `watermeter/static/style.css`
- **Task**: Fix minor spacing, alignment, and typography inconsistencies
- **Details**:
  1. **Main padding**: Increase `main` padding from `20px` to `24px 32px` on desktop for better breathing room, matching the header padding
  2. **Container max-width**: The `1800px` max-width is very wide. Consider reducing to `1400px` for better readability on ultra-wide monitors, or keep it but add a note that this is intentional for the image grid
  3. **Body background on nav overlap**: On desktop, the gradient body background peeks through between the nav rail and the container. Add `background: var(--bg-dark)` to the body area behind the nav, or extend the container to fill the viewport height with `min-height: calc(100vh - 40px)`
  4. **Loading state**: The `.loading` and `.no-data` states have adequate styling but could use a subtle spinner animation for `.loading`
  5. **Error section** (`.error`): The generic `.error` class name can conflict. Leave as-is for now (renaming would require route/JS changes) but add specificity: `.status-fragment .error` or scope within `main`

### WP-6: Favicon
- **Agent**: frontend
- **Files**: `watermeter/static/favicon.svg` (new), `watermeter/templates/dashboard.html`, `watermeter/templates/label.html`, `watermeter/templates/roi_config.html`, `watermeter/templates/config_editor.html`, `watermeter/templates/training.html`
- **Task**: Add a simple SVG favicon to eliminate the 404 and give the app a polished touch
- **Details**:
  1. Create a minimal SVG favicon -- a simple water drop or meter icon in the primary blue color (`#2563eb`)
  2. Add `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">` to the `<head>` of all five page templates
  3. Alternatively, create a shared `<head>` partial to avoid duplication (but this is a larger refactor -- keep it simple for now, just add the link to each template)

## Implementation Order

1. **WP-1** (Status badge) -- highest impact, addresses the primary pain point
2. **WP-2** (Page headers) -- quick wins, improves navigation clarity
3. **WP-3** (Training badge) -- small fix with visible improvement
4. **WP-4** (Image cards) -- visual polish on the most-viewed page
5. **WP-5** (Spacing/typography) -- subtle improvements across all pages
6. **WP-6** (Favicon) -- small but professional touch

## Notes

- All changes are CSS/HTML only -- no backend or JS logic changes needed
- The existing color scheme (CSS variables) must be preserved
- The overall layout (nav rail, container, header pattern) must not change
- Mobile responsiveness must be tested after changes (existing media queries should be preserved)
- The ROI Config page and Label page look good overall -- focus is on dashboard and cross-page consistency
