# Pretrained Model Management — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship pretrained efficientnet_lite0 models (digits + arrows) in the Docker image so new users have a working bootstrap model out of the box.

**Architecture:** Store model files in `pretrained/{digits,arrows}/` in the repo. Dockerfile COPYs them to `/app/{digits,arrows}/selected/`. The existing entrypoint auto-installs them into `/app/models/` on first container start. No entrypoint changes needed.

**Tech Stack:** Git, Docker, OpenVINO (.xml + .bin)

---

## Context

- Two pretrained models to ship: `efficientnet_lite0_c606f2` (digits) and `efficientnet_lite0_582b90` (arrows)
- Each model has: `.bin` (~6.5 MB), `.xml` (~185 KB), `.onnx` (~13 MB, not needed for inference), `metadata.json`, `training.log`, `_training.png`
- Only `.bin` + `.xml` + `metadata.json` are needed for inference — skip `.onnx`, `.png`, `.log` to save ~13 MB per model
- Total git footprint: ~14 MB (manageable without LFS)
- User wants date-based naming: `efficientnet_lite0_20260217`
- `docker-entrypoint.sh` already handles auto-install from `/app/{digits,arrows}/selected/`
- `.gitignore` globally ignores `*.bin` and `*.xml` — needs override for `pretrained/`
- Old tracked file `digits/ov_model/model_mobilenetv3_small_100_c11_r144.xml` should be cleaned up

---

### Task 1: Create pretrained directory structure

**Files:**
- Create: `pretrained/digits/` directory
- Create: `pretrained/arrows/` directory

**Step 1: Create directories and copy model files with date-based names**

```bash
mkdir -p pretrained/digits pretrained/arrows

# Digits model (from efficientnet_lite0_c606f2)
cp models_debug/digits/efficientnet_lite0_c606f2/efficientnet_lite0_c606f2.bin \
   pretrained/digits/efficientnet_lite0_20260217.bin
cp models_debug/digits/efficientnet_lite0_c606f2/efficientnet_lite0_c606f2.xml \
   pretrained/digits/efficientnet_lite0_20260217.xml

# Arrows model (from efficientnet_lite0_582b90)
cp models_debug/arrows/efficientnet_lite0_582b90/efficientnet_lite0_582b90.bin \
   pretrained/arrows/efficientnet_lite0_20260217.bin
cp models_debug/arrows/efficientnet_lite0_582b90/efficientnet_lite0_582b90.xml \
   pretrained/arrows/efficientnet_lite0_20260217.xml
```

**Step 2: Create metadata.json for each model**

`pretrained/digits/metadata.json`:
```json
{
  "model_type": "digits",
  "model_name": "efficientnet_lite0_20260217",
  "created_at": "2026-02-17T11:55:00Z",
  "notes": "Bootstrap model trained on synthetic data. ~30% accuracy on reference dataset, usable for initial data collection.",
  "classes": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "NAN"],
  "resolution": 128
}
```

`pretrained/arrows/metadata.json`:
```json
{
  "model_type": "arrows",
  "model_name": "efficientnet_lite0_20260217",
  "created_at": "2026-02-17T12:14:00Z",
  "notes": "Bootstrap model trained on synthetic data. ~15% accuracy on reference dataset, usable for initial data collection.",
  "classes": ["0.0", "1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0"],
  "resolution": 128
}
```

**Step 3: Verify files exist and sizes are reasonable**

```bash
ls -lh pretrained/digits/ pretrained/arrows/
# Expected: ~6.5 MB .bin, ~185 KB .xml, small metadata.json per model
```

---

### Task 2: Update .gitignore to allow pretrained models

**Files:**
- Modify: `.gitignore`

**Step 1: Add override for pretrained directory**

After the `*.xml` line in `.gitignore`, add:

```gitignore
# Pretrained bootstrap models (override *.bin / *.xml ignore above)
!pretrained/
!pretrained/**
```

Git's negation patterns (`!`) re-include files that match earlier ignore rules. The `!pretrained/` line un-ignores the directory itself, and `!pretrained/**` un-ignores all files inside it.

**Step 2: Verify git sees the pretrained files**

```bash
git status
# Should show pretrained/digits/ and pretrained/arrows/ files as untracked
git add -n pretrained/
# Should list all 6 files (2x .bin, 2x .xml, 2x metadata.json)
```

---

### Task 3: Update Dockerfile to include pretrained models

**Files:**
- Modify: `Dockerfile`

**Step 1: Add COPY commands for pretrained models**

After the `COPY watermeter/ /app/watermeter/` line, add:

```dockerfile
# Copy pretrained bootstrap models (auto-installed by entrypoint on first run)
COPY pretrained/digits/ /app/digits/selected/
COPY pretrained/arrows/ /app/arrows/selected/
```

The entrypoint already handles copying from `/app/{digits,arrows}/selected/` → `/app/models/` when models dir is empty.

**Step 2: Add ownership fix**

The existing `RUN chown -R watermeter:watermeter /app /config_default` line already covers `/app/digits/` and `/app/arrows/` since they're under `/app`. No change needed here.

**Step 3: Verify with dry-run build**

```bash
docker build . -t watermeter-dashboard --no-cache 2>&1 | tail -20
# Should succeed without errors
```

---

### Task 4: Remove old tracked model file

**Files:**
- Remove from git: `digits/ov_model/model_mobilenetv3_small_100_c11_r144.xml`

**Step 1: Check what's in the old ov_model directory**

```bash
git ls-files digits/ov_model/
```

**Step 2: Remove from git tracking**

```bash
git rm digits/ov_model/model_mobilenetv3_small_100_c11_r144.xml
```

The `.gitignore` pattern `**/ov_model/` already prevents re-tracking.

---

### Task 5: Commit and verify end-to-end

**Step 1: Stage all changes**

```bash
git add pretrained/ .gitignore Dockerfile
git add -u  # picks up the git rm
```

**Step 2: Commit**

```bash
git commit -m "claude: add pretrained efficientnet_lite0 bootstrap models

Ship digits + arrows models in pretrained/ directory.
Dockerfile COPYs them to /app/{digits,arrows}/selected/.
Entrypoint auto-installs on first container start."
```

**Step 3: Verify bootstrap flow with debug container**

```bash
# Clean slate: remove existing models
rm -rf models_debug/digits models_debug/arrows
mkdir -p models_debug/digits models_debug/arrows

# Build and run
./debug.sh --detach
# Should see:
# OK digits model: efficientnet_lite0_20260217
# OK arrows model: efficientnet_lite0_20260217
```

**Step 4: Verify model is loaded via API**

```bash
curl -s http://localhost:8002/api/models | python3 -m json.tool
# Should show efficientnet_lite0_20260217 for both digits and arrows
```
