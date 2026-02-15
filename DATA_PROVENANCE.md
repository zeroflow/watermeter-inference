# Data Provenance

This document tracks the origin and licensing status of all training data used
in the Water Meter AI project.

## Summary

| Data source | Ships with project? | License | Notes |
|-------------|---------------------|---------|-------|
| Our own meter photos | Yes (ground truth) | AGPL-3.0 (same as project) | Self-collected from our water meter |
| Our pre-trained models | Yes (in `models/`) | AGPL-3.0 (same as project) | Trained exclusively on our own data |
| jomjol digit data | No (fetch script) | **No license** | See [issue #4041][issue] |
| jomjol analog data | No (fetch script) | **No license** | See [issue #4041][issue] |

## What Ships With This Project

### Ground truth images

The `digits/ground_truth/` and `arrows/ground_truth/` directories contain
labeled images collected from our own water meter. These are original photos
taken by the project maintainers, not derived from any upstream source.

- **Digits**: 11 classes (0-9 + NAN), approximately 450 images total
- **Arrows**: 100 classes (0.0 through 9.9), approximately 4,200 images total
- **License**: AGPL-3.0, same as the rest of this project

### Pre-trained models

Any models shipped in `models/` or `digits/selected/` and `arrows/selected/`
are trained exclusively on our own ground truth data. They are derivative works
of our data and the model architectures (timm/PyTorch, which are Apache-2.0
licensed), and are distributed under this project's AGPL-3.0 license.

## Upstream Data (Not Shipped)

### jomjol's training data

The [AI-on-the-edge-device][aiote] project by jomjol includes training data for
digit and analog meter reading in two separate repositories:

- **Digits**: [neural-network-digital-counter-readout][digits-repo]
  - Classes: 0-9 + NaN (11 classes)
  - Format: 32x20 pixel RGB images in class-labeled subfolders
  - Approximately 150 images per digit class, ~3,700 NaN images

- **Analog**: [neural-network-analog-needle-readout][analog-repo]
  - Approach: continuous regression (output 0.0-9.9)
  - Format: 32x32 pixel RGB images in labeled subfolders
  - Approximately 2,800+ labeled images

### Licensing status

**As of 2026-02-14, neither training data repository has an explicit license
file.** The main AI-on-the-edge-device project uses a dual-license model, but
the training data repos were never given their own license.

This is tracked in: [AI-on-the-edge-device issue #4041][issue]

The issue was opened by a downstream user requesting license clarification. As
of this writing, there has been no substantive response from the maintainer.

### Why we do not ship upstream data

Without an explicit license, we cannot legally redistribute the data or models
trained from it. Users who want to use jomjol's data for local training can
fetch it themselves using the provided script.

## How to Fetch Upstream Data

A convenience script is provided to download jomjol's training data into the
correct directory structure:

```bash
# Download all upstream training data to /training/ (default Docker mount)
./scripts/fetch_upstream_data.sh

# Download to a custom directory
./scripts/fetch_upstream_data.sh --output-dir ./my_training_data

# Download only digit data
./scripts/fetch_upstream_data.sh --digits-only

# Download only analog data
./scripts/fetch_upstream_data.sh --analog-only

# Skip confirmation prompt
./scripts/fetch_upstream_data.sh --yes

# See all options
./scripts/fetch_upstream_data.sh --help
```

The script will:
1. Display a licensing warning and ask for confirmation
2. Download the repository archives from GitHub
3. Extract labeled images into the appropriate class folders
4. Skip existing files (idempotent; use `--force` to overwrite)
5. Map upstream class names to our convention (e.g. `NaN` to `NAN`)

### Requirements

- `curl` (for downloading)
- `unzip` (for extracting archives)
- Internet access to GitHub

### Output structure

```
<output-dir>/
  digits/ground_truth/
    0/ 1/ 2/ ... 9/ NAN/     (upstream digit images)
  arrows/ground_truth/
    0.0/ 0.1/ ... 9.9/       (upstream analog images)
```

## Combining Data Sources

After fetching upstream data, it will be placed alongside any existing ground
truth. The training system treats all images in the ground truth folders equally
-- there is no distinction between our data and upstream data at training time.

If you want to keep data sources separate, use different output directories and
symlink or copy selectively.

## License Status Tracking

| Date | Event |
|------|-------|
| 2024-01-xx | [Issue #4041][issue] opened requesting license clarification |
| 2024-01-xx | Collaborator pings maintainer with "FYI" -- no substantive response |
| 2026-02-14 | Issue still open, no license added to upstream repos |

If the upstream issue is resolved, update this document and the warning in
`scripts/fetch_upstream_data.sh` accordingly.

[aiote]: https://github.com/jomjol/AI-on-the-edge-device
[digits-repo]: https://github.com/jomjol/neural-network-digital-counter-readout
[analog-repo]: https://github.com/jomjol/neural-network-analog-needle-readout
[issue]: https://github.com/jomjol/AI-on-the-edge-device/issues/4041
