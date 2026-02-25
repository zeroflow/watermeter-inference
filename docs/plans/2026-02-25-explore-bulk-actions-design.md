# Explore & Tune: Image Selection & Bulk Actions — Design

**Date:** 2026-02-25

## Goal

Add image selection and two bulk actions (Send to Labeling, Delete) to the Explore & Tune page's image grid.

## UI Changes

**Image grid thumbnails** become selectable:
- Click to toggle selection (CSS class `selected` on the image wrapper)
- Selected images get a colored border + semi-transparent overlay (reusing `--primary` color variable)
- Selection state stored as a JS `Set` of filenames, reset when switching classes or types

**Action bar** (top-right of image panel, next to class name/count):
- **"Select All" / "Deselect All"** — text link, toggles all currently loaded images
- **"Send to Labeling"** button — gray (`--bg-alt` styled), disabled when nothing selected, shows count: "Send to Labeling (N)"
- **"Delete"** button — red (`--danger` styled), disabled when nothing selected, shows count: "Delete (N)"

**Confirmation**: Delete shows `confirm()` dialog. Send to Labeling executes immediately (non-destructive).

## Backend

**`POST /api/training-data/bulk-move-to-input`**
- Request: `{ type, class_name, filenames: [...] }`
- Moves files from `ground_truth/{type}/{class}/` to `input/{type}/`
- Response: `{ success, moved_count, error_count, errors }`

**`POST /api/training-data/bulk-delete`**
- Request: `{ type, class_name, filenames: [...] }`
- Permanently deletes files from `ground_truth/{type}/{class}/`
- Response: `{ success, deleted_count, error_count, errors }`

Both endpoints validate paths with `safe_subpath` (existing pattern).

## Data Flow

```
User clicks images → toggle in selectedImages Set
  ↓
Action buttons update count: "Delete (3)"
  ↓
User clicks action → (confirm for delete) → POST endpoint
  ↓
On success: remove images from DOM, clear selection, update sidebar count, show toast
```

## Decisions

- Selection resets on class/type switch (no cross-class accumulation)
- No label hints on move-to-input (unlike mislabel flow)
- Permanent delete, no trash folder
- Click-to-toggle selection (no checkboxes)
