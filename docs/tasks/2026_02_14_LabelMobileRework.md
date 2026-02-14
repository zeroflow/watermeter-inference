# Label Page Mobile Rework

## Goal

Make the labeling page work well on phones, especially when the soft keyboard is open. The current layout has a 500px image, large input, and lots of vertical content that doesn't fit on mobile.

## Plan

### Layout changes (mobile <768px)
- Image: 150px height (normal), 80px (keyboard open)
- Hide "next dial" reference image
- Hide instructions (badge is sufficient context)
- Hide keyboard shortcuts hint (not relevant for touch)
- Replace standalone delete button with compact action bar (Submit + Delete side by side)
- Remove min-height: 600px
- Compact padding

### Keyboard handling
- Use `visualViewport` API to detect keyboard open/close
- Toggle `keyboard-open` class on body
- When keyboard open: hide header, hide nav bar, shrink image to 80px, hide badge
- Input stays visible and focused throughout

### Header compaction
- Show only "Total" progress bar on mobile (hide Digits/Arrows separately)
- Remove min-width: 200px from progress items

### Input improvements
- Set `inputmode="decimal"` for arrows, `inputmode="text"` for digits
- Add visible Submit + Delete action buttons for touch

## Done Criteria

- [ ] Label page usable on phone in portrait with keyboard open
- [ ] Image visible while typing
- [ ] Submit and Delete accessible as touch buttons
- [ ] Smooth keyboard open/close transitions
- [ ] Tests pass
- [ ] Committed
