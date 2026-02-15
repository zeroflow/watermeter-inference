# BL-10: Handle Upstream Data License

**Status**: done
**Created**: 2026-02-14
**Backlog ref**: BL-10

## Goal

Address the licensing ambiguity around training data from jomjol's AI-on-the-edge-device project. The upstream repositories (`neural-network-digital-counter-readout` and `neural-network-analog-needle-readout`) contain training data with no explicit license (see [issue #4041](https://github.com/jomjol/AI-on-the-edge-device/issues/4041)). This task ensures that:

1. Our shipped data is clearly our own (self-collected meter photos).
2. Upstream data can be fetched separately by users who want it for local training.
3. Provenance is documented so anyone can understand what data they are using and under what terms.

## Plan

### WP-1: Task doc (this file)
Capture goal, plan, and done criteria.

### WP-2: `scripts/fetch_upstream_data.sh`
Bash script that downloads jomjol's training data into the local `/training/` directory structure.

- Downloads digit data from `jomjol/neural-network-digital-counter-readout` (classes 0-9 + NaN, stored in subfolders)
- Downloads analog data from `jomjol/neural-network-analog-needle-readout` (continuous regression labels in `data_raw_all`)
- Prints licensing warnings before downloading
- Idempotent (skips existing data)
- Requires `curl` and `unzip`
- Includes `--help` flag

### WP-3: `DATA_PROVENANCE.md`
Root-level document covering:
- What data ships with this project (our own meter photos)
- What data can be fetched from upstream (jomjol's data, no license)
- How to use the fetch script
- License status tracking with link to upstream issue

### WP-4: Update backlog
Mark BL-10 as done in `backlog.md`.

## Progress Log

- 2026-02-15: Created task doc. Researched upstream repos:
  - Digits: `github.com/jomjol/neural-network-digital-counter-readout` -- training data in `ziffer_raw/` and `01_data_raw_all_original/`, 11 classes (0-9 + NaN), images resized to 32x20 RGB
  - Analog: `github.com/jomjol/neural-network-analog-needle-readout` -- training data in `data_raw_all/`, continuous regression output (0.0-9.9), images resized to 32x32 RGB
  - Issue #4041 remains open with no resolution. Only response was a "FYI" ping to jomjol from a collaborator.
  - Our project uses AGPL-3.0 for code. Training data provenance was undocumented until now.
- 2026-02-15: Created `scripts/fetch_upstream_data.sh` (executable, --help works, bash syntax verified).
- 2026-02-15: Created `DATA_PROVENANCE.md` in project root.
- 2026-02-15: Updated `backlog.md` -- BL-10 marked as `done`.
- 2026-02-15: Verified 212/212 unit+regression tests pass (no Python code modified).

## Open Items

- Upstream issue #4041 may eventually be resolved with a license -- if so, `DATA_PROVENANCE.md` and the fetch script warning can be updated.
- The fetch script uses GitHub archive downloads which may change URL structure over time.

## Done Criteria

- [x] `scripts/fetch_upstream_data.sh` exists, is executable, and has `--help` output
- [x] `DATA_PROVENANCE.md` exists in project root with complete provenance documentation
- [x] `backlog.md` shows BL-10 as `done`
- [x] No Python source files or templates modified
- [x] Unit tests still pass (212/212 passed, no code changes)
