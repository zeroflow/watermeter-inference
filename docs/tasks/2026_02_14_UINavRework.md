# UI Navigation Rework

## Goal

Replace the ad-hoc navigation buttons on the dashboard with a persistent, adaptive navigation bar visible on all pages. Mobile gets a bottom tab bar; desktop gets a left navigation rail.

## Plan

### Phase 1: Shared Navigation Partial
- Create `watermeter/templates/_nav.html` — a `<nav>` element with 5 links (Dashboard, Label, Training, ROI, Config)
- Each link has an inline SVG icon + short label
- Active page highlighted via Jinja2 variable `nav_active`
- Pass `nav_active` from every page route

### Phase 2: Navigation CSS
- Add nav styles to `watermeter/static/style.css`
- Mobile (<768px): fixed bottom bar, 56px tall, `flex-direction: row`, icons above labels
- Desktop (>=768px): fixed left rail, 72px wide, `flex-direction: column`, icons above labels
- Active state: accent color fill + label color change
- Content offset: `padding-bottom: 56px` mobile, `margin-left: 72px` desktop

### Phase 3: Template Integration
- Include `_nav.html` in all 5 page templates (dashboard, label, training, roi_config, config_editor)
- Remove navigation buttons from dashboard header (keep Jetzt Auslesen, Reset, toggles)
- Remove "back to dashboard" links/buttons from sub-pages
- Ensure each template passes correct `nav_active` value

### Phase 4: Cleanup & Polish
- Verify all pages render correctly at mobile and desktop widths
- Adjust any layout conflicts (z-index, overflow, scroll behavior)
- Run unit/regression tests
- Commit

## Nav Items

| Key | Label | Icon | Route |
|-----|-------|------|-------|
| dashboard | Dashboard | gauge/tachometer | `/` |
| label | Label | tag | `/label` |
| training | Training | brain/neural-net | `/training` |
| roi | ROI | crosshair/crop | `/roi-config` |
| config | Config | gear | `/config-editor` |

## Responsive Breakpoints

- `<768px` — bottom tab bar (fixed, 56px height, full width)
- `>=768px` — left navigation rail (fixed, 72px width, full height)

## Active State

- Inactive: muted icon + label color (`--text-light`)
- Active: primary color icon + label (`--primary`)
- Hover: slight background highlight

## Done Criteria

- [ ] `_nav.html` partial exists with 5 items and inline SVG icons
- [ ] Navigation visible on all 5 pages
- [ ] Correct active state on each page
- [ ] Mobile: bottom tab bar, thumb-friendly touch targets (48px min)
- [ ] Desktop: left rail, content not overlapped
- [ ] Dashboard header cleaned up (no nav buttons, only actions)
- [ ] Sub-pages no longer need "back to dashboard" links
- [ ] Unit/regression tests pass
- [ ] Committed

## Progress Log

- 2026-02-14: Research complete, task created
- 2026-02-14: Implementation complete
  - Created `_nav.html` with 5 SVG icons (Home, Label, Training, ROI, Config)
  - Added nav CSS to `style.css` (adaptive bottom bar / left rail)
  - Updated `pages.py` to pass `nav_active` context to all page routes
  - Updated all 5 templates: added nav include, removed old back/nav buttons
  - All 134 unit/regression tests pass
