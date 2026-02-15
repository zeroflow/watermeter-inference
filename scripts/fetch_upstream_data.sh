#!/usr/bin/env bash
set -euo pipefail

# fetch_upstream_data.sh
# Downloads training data from jomjol's AI-on-the-edge-device project repos.
#
# Upstream repositories:
#   Digits:  https://github.com/jomjol/neural-network-digital-counter-readout
#   Analog:  https://github.com/jomjol/neural-network-analog-needle-readout
#
# WARNING: As of 2026-02-14, the upstream training data has NO explicit license.
# See: https://github.com/jomjol/AI-on-the-edge-device/issues/4041
#
# By running this script, you acknowledge that:
#   - You are downloading data for personal/local training only
#   - You will not redistribute the downloaded data
#   - You will not redistribute models trained exclusively from this data
#   - The licensing status may change; check the upstream issue for updates
#
# See DATA_PROVENANCE.md in the project root for full context.

###############################################################################
# Configuration
###############################################################################

DIGITS_REPO="jomjol/neural-network-digital-counter-readout"
ANALOG_REPO="jomjol/neural-network-analog-needle-readout"
DIGITS_BRANCH="master"
ANALOG_BRANCH="master"

# GitHub archive download URLs
DIGITS_ARCHIVE="https://github.com/${DIGITS_REPO}/archive/refs/heads/${DIGITS_BRANCH}.zip"
ANALOG_ARCHIVE="https://github.com/${ANALOG_REPO}/archive/refs/heads/${ANALOG_BRANCH}.zip"

# Source directories within the extracted archives (where labeled images live)
# Digits: images are in subfolders named 0/ 1/ ... 9/ NaN/ inside the training data dir
DIGITS_DATA_SUBDIR="neural-network-digital-counter-readout-${DIGITS_BRANCH}/ziffer_sortiert_resize"
# Analog: images are in subfolders inside data_raw_all/
ANALOG_DATA_SUBDIR="neural-network-analog-needle-readout-${ANALOG_BRANCH}/data_raw_all"

# Default output base directory (Docker volume mount point)
DEFAULT_OUTPUT_DIR="/training"

# Digit class names (upstream uses 0-9 + NaN)
DIGIT_CLASSES=("0" "1" "2" "3" "4" "5" "6" "7" "8" "9" "NaN")
# Our project uses "NAN" (uppercase) -- the script maps NaN -> NAN

###############################################################################
# Functions
###############################################################################

usage() {
    cat <<'USAGE'
Usage: fetch_upstream_data.sh [OPTIONS]

Downloads training data from jomjol's AI-on-the-edge-device project into
the local training directory structure.

WARNING: The upstream training data has no explicit license.
         See https://github.com/jomjol/AI-on-the-edge-device/issues/4041

Options:
  -o, --output-dir DIR   Base output directory (default: /training)
  -d, --digits-only      Download only digit training data
  -a, --analog-only      Download only analog training data
  -f, --force            Re-download even if data already exists
  -y, --yes              Skip confirmation prompt
  -h, --help             Show this help message

Output structure:
  <output-dir>/
    digits/ground_truth/
      0/ 1/ 2/ ... 9/ NAN/    (digit class folders with .jpg images)
    arrows/ground_truth/
      0.0/ 0.1/ ... 9.9/      (analog class folders with .jpg images)

Examples:
  # Download everything to /training (default, e.g. inside Docker)
  ./fetch_upstream_data.sh

  # Download to a custom directory
  ./fetch_upstream_data.sh -o ./my_training_data

  # Download only digits, skip confirmation
  ./fetch_upstream_data.sh -d -y
USAGE
}

log() {
    echo "[fetch] $*"
}

warn() {
    echo "[fetch] WARNING: $*" >&2
}

error() {
    echo "[fetch] ERROR: $*" >&2
    exit 1
}

check_dependencies() {
    local missing=()
    for cmd in curl unzip; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing required tools: ${missing[*]}. Install them and try again."
    fi
}

show_license_warning() {
    cat <<'WARNING'
================================================================================
  DATA LICENSE WARNING
================================================================================

  The training data you are about to download comes from:

    - https://github.com/jomjol/neural-network-digital-counter-readout
    - https://github.com/jomjol/neural-network-analog-needle-readout

  As of 2026-02-14, these repositories have NO explicit license file.
  The licensing status is tracked at:

    https://github.com/jomjol/AI-on-the-edge-device/issues/4041

  By proceeding, you acknowledge that:
    1. You download this data at your own risk
    2. You will use it for personal/local training only
    3. You will NOT redistribute the data or models trained from it
    4. You will check the upstream issue for license updates

================================================================================
WARNING
}

confirm_download() {
    if [[ "$SKIP_CONFIRM" == "true" ]]; then
        return 0
    fi
    read -rp "Do you want to proceed with the download? [y/N] " answer
    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) log "Download cancelled."; exit 0 ;;
    esac
}

download_and_extract() {
    local url="$1"
    local dest_zip="$2"
    local desc="$3"

    if [[ -f "$dest_zip" ]] && [[ "$FORCE" != "true" ]]; then
        log "$desc archive already downloaded, using cached version."
        log "  (use --force to re-download)"
        return 0
    fi

    log "Downloading $desc from $url ..."
    curl -fSL --progress-bar -o "$dest_zip" "$url" || error "Failed to download $desc"
    log "Download complete: $(du -h "$dest_zip" | cut -f1)"
}

###############################################################################
# Install digits data
###############################################################################

install_digits() {
    local archive_zip="$WORK_DIR/digits_archive.zip"
    local extract_dir="$WORK_DIR/digits_extracted"
    local target_dir="$OUTPUT_DIR/digits/ground_truth"

    # Check if target already has data
    if [[ -d "$target_dir" ]] && [[ "$FORCE" != "true" ]]; then
        local existing_count
        existing_count=$(find "$target_dir" -type f -name "*.jpg" -o -name "*.png" -o -name "*.jpeg" 2>/dev/null | wc -l)
        if [[ "$existing_count" -gt 0 ]]; then
            log "Digit ground truth already contains $existing_count images in $target_dir"
            log "  (use --force to overwrite)"
            return 0
        fi
    fi

    download_and_extract "$DIGITS_ARCHIVE" "$archive_zip" "digit training data"

    log "Extracting digit training data..."
    rm -rf "$extract_dir"
    mkdir -p "$extract_dir"
    unzip -q "$archive_zip" -d "$extract_dir" || error "Failed to extract digit archive"

    # Look for the training data subdirectory
    local src_dir="$extract_dir/$DIGITS_DATA_SUBDIR"
    if [[ ! -d "$src_dir" ]]; then
        # Try alternative known paths
        local alt_paths=(
            "$extract_dir/neural-network-digital-counter-readout-${DIGITS_BRANCH}/03_data_resize_all-use_for_training"
            "$extract_dir/neural-network-digital-counter-readout-${DIGITS_BRANCH}/01_data_raw_all_original"
        )
        for alt in "${alt_paths[@]}"; do
            if [[ -d "$alt" ]]; then
                src_dir="$alt"
                break
            fi
        done
    fi

    if [[ ! -d "$src_dir" ]]; then
        warn "Could not find expected digit data directory."
        warn "Expected: $DIGITS_DATA_SUBDIR"
        warn "Archive contents:"
        ls -la "$extract_dir"/neural-network-digital-counter-readout-*/ 2>/dev/null || true
        error "Please check the upstream repository structure and update this script."
    fi

    log "Installing digit images to $target_dir ..."
    mkdir -p "$target_dir"

    local total_copied=0
    for class_dir in "$src_dir"/*/; do
        local class_name
        class_name=$(basename "$class_dir")

        # Map upstream class names to our convention
        local target_class="$class_name"
        if [[ "$class_name" == "NaN" ]] || [[ "$class_name" == "nan" ]]; then
            target_class="NAN"
        fi

        mkdir -p "$target_dir/$target_class"

        local count=0
        while IFS= read -r -d '' img; do
            local basename_img
            basename_img=$(basename "$img")
            local dest="$target_dir/$target_class/$basename_img"
            if [[ -f "$dest" ]] && [[ "$FORCE" != "true" ]]; then
                continue
            fi
            cp "$img" "$dest"
            count=$((count + 1))
        done < <(find "$class_dir" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.bmp" \) -print0 2>/dev/null || true)
        total_copied=$((total_copied + count))

        if [[ "$count" -gt 0 ]]; then
            log "  Class $target_class: copied $count images"
        fi
    done

    log "Digit data installed: $total_copied images total"
}

###############################################################################
# Install analog data
###############################################################################

install_analog() {
    local archive_zip="$WORK_DIR/analog_archive.zip"
    local extract_dir="$WORK_DIR/analog_extracted"
    local target_dir="$OUTPUT_DIR/arrows/ground_truth"

    # Check if target already has data
    if [[ -d "$target_dir" ]] && [[ "$FORCE" != "true" ]]; then
        local existing_count
        existing_count=$(find "$target_dir" -type f -name "*.jpg" -o -name "*.png" -o -name "*.jpeg" 2>/dev/null | wc -l)
        if [[ "$existing_count" -gt 0 ]]; then
            log "Analog ground truth already contains $existing_count images in $target_dir"
            log "  (use --force to overwrite)"
            return 0
        fi
    fi

    download_and_extract "$ANALOG_ARCHIVE" "$archive_zip" "analog training data"

    log "Extracting analog training data..."
    rm -rf "$extract_dir"
    mkdir -p "$extract_dir"
    unzip -q "$archive_zip" -d "$extract_dir" || error "Failed to extract analog archive"

    # Look for the training data subdirectory
    local src_dir="$extract_dir/$ANALOG_DATA_SUBDIR"
    if [[ ! -d "$src_dir" ]]; then
        # Try alternative known paths
        local alt_paths=(
            "$extract_dir/neural-network-analog-needle-readout-${ANALOG_BRANCH}/data_raw_all_originals"
            "$extract_dir/neural-network-analog-needle-readout-${ANALOG_BRANCH}/Train-CNN_Analog-Needle-Readout/data_resize_all"
        )
        for alt in "${alt_paths[@]}"; do
            if [[ -d "$alt" ]]; then
                src_dir="$alt"
                break
            fi
        done
    fi

    if [[ ! -d "$src_dir" ]]; then
        warn "Could not find expected analog data directory."
        warn "Expected: $ANALOG_DATA_SUBDIR"
        warn "Archive contents:"
        ls -la "$extract_dir"/neural-network-analog-needle-readout-*/ 2>/dev/null || true
        error "Please check the upstream repository structure and update this script."
    fi

    log "Installing analog images to $target_dir ..."
    mkdir -p "$target_dir"

    # Upstream analog data uses folder names like 0.0, 0.1, ..., 9.9
    # Our convention matches: arrows/ground_truth/{0.0,0.1,...,9.9}/
    local total_copied=0
    for class_dir in "$src_dir"/*/; do
        local class_name
        class_name=$(basename "$class_dir")

        mkdir -p "$target_dir/$class_name"

        local count=0
        while IFS= read -r -d '' img; do
            local basename_img
            basename_img=$(basename "$img")
            local dest="$target_dir/$class_name/$basename_img"
            if [[ -f "$dest" ]] && [[ "$FORCE" != "true" ]]; then
                continue
            fi
            cp "$img" "$dest"
            count=$((count + 1))
        done < <(find "$class_dir" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.bmp" \) -print0 2>/dev/null || true)
        total_copied=$((total_copied + count))

        if [[ "$count" -gt 0 ]]; then
            log "  Class $class_name: copied $count images"
        fi
    done

    log "Analog data installed: $total_copied images total"
}

###############################################################################
# Cleanup
###############################################################################

cleanup() {
    if [[ -n "${WORK_DIR:-}" ]] && [[ -d "$WORK_DIR" ]]; then
        log "Cleaning up temporary files..."
        rm -rf "$WORK_DIR"
    fi
}

###############################################################################
# Main
###############################################################################

main() {
    # Defaults
    OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
    DOWNLOAD_DIGITS="true"
    DOWNLOAD_ANALOG="true"
    FORCE="false"
    SKIP_CONFIRM="false"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -o|--output-dir)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            -d|--digits-only)
                DOWNLOAD_ANALOG="false"
                shift
                ;;
            -a|--analog-only)
                DOWNLOAD_DIGITS="false"
                shift
                ;;
            -f|--force)
                FORCE="true"
                shift
                ;;
            -y|--yes)
                SKIP_CONFIRM="true"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Unknown option: $1 (use --help for usage)"
                ;;
        esac
    done

    # Validate
    check_dependencies

    if [[ "$DOWNLOAD_DIGITS" == "false" ]] && [[ "$DOWNLOAD_ANALOG" == "false" ]]; then
        error "Cannot use --digits-only and --analog-only together"
    fi

    # Show warning and confirm
    show_license_warning
    confirm_download

    # Create work directory
    WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/fetch_upstream_XXXXXX")
    trap cleanup EXIT

    log "Output directory: $OUTPUT_DIR"
    mkdir -p "$OUTPUT_DIR"

    # Download and install
    if [[ "$DOWNLOAD_DIGITS" == "true" ]]; then
        log ""
        log "=== Digit Training Data ==="
        install_digits
    fi

    if [[ "$DOWNLOAD_ANALOG" == "true" ]]; then
        log ""
        log "=== Analog Training Data ==="
        install_analog
    fi

    log ""
    log "Done. Training data is ready in $OUTPUT_DIR"
    log ""
    log "NOTE: This data has no explicit license from the upstream author."
    log "      Use for personal/local training only."
    log "      Track: https://github.com/jomjol/AI-on-the-edge-device/issues/4041"
}

main "$@"
