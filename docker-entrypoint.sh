#!/bin/bash
set -e

echo "=== Water Meter Dashboard Startup ==="

# Parse model filename to extract metadata
# Format: model_{type}_{architecture}[_c{classes}][_r{resolution}][_s{seed}]
parse_model_name() {
    local filename="$1"
    local model_type="$2"

    # Remove model_{type}_ prefix
    local rest="${filename#model_${model_type}_}"

    # Extract seed (_s followed by digits at end)
    PARSED_SEED=""
    if [[ "$rest" =~ ^(.+)_s([0-9]+)$ ]]; then
        rest="${BASH_REMATCH[1]}"
        PARSED_SEED="${BASH_REMATCH[2]}"
    fi

    # Extract resolution (_r followed by digits)
    PARSED_RESOLUTION=""
    if [[ "$rest" =~ ^(.+)_r([0-9]+)$ ]]; then
        rest="${BASH_REMATCH[1]}"
        PARSED_RESOLUTION="${BASH_REMATCH[2]}"
    fi

    # Extract num_classes (_c followed by digits)
    PARSED_CLASSES=""
    if [[ "$rest" =~ ^(.+)_c([0-9]+)$ ]]; then
        rest="${BASH_REMATCH[1]}"
        PARSED_CLASSES="${BASH_REMATCH[2]}"
    fi

    # What remains is the architecture
    PARSED_ARCH="$rest"
}

# Initialize config directory if empty
if [ ! -f /config/config.yaml ]; then
    echo "No config found in /config, copying defaults..."
    cp /config_default/config.yaml /config/config.yaml
    echo "✓ Default config copied to /config/config.yaml"
else
    echo "✓ Using existing config from /config/config.yaml"
fi

# Create data directory for state persistence
mkdir -p /data
echo "✓ Data directory created"

# Ensure model cache directory exists
mkdir -p /root/.cache
if [ -n "$HF_TOKEN" ]; then
    echo "✓ HuggingFace API token configured"
else
    echo "⚠ No HF_TOKEN set - downloads may be rate-limited"
fi

# Create training directories
mkdir -p /training/digits/input
mkdir -p /training/digits/ground_truth
mkdir -p /training/arrows/input
mkdir -p /training/arrows/ground_truth
echo "✓ Training directories created"

# Initialize models directory with default models if empty
mkdir -p /app/models/digits
mkdir -p /app/models/arrows

# Check and copy default digits model
if [ -z "$(ls -A /app/models/digits 2>/dev/null)" ]; then
    echo "Models directory empty, copying default digits model..."
    if [ -d /app/digits/selected ]; then
        MODEL_NAME=$(basename /app/digits/selected/*.xml .xml)
        mkdir -p "/app/models/digits/${MODEL_NAME}"
        cp /app/digits/selected/*.xml "/app/models/digits/${MODEL_NAME}/"
        cp /app/digits/selected/*.bin "/app/models/digits/${MODEL_NAME}/"

        # Parse architecture and resolution from filename
        parse_model_name "${MODEL_NAME}" "digits"
        D_ARCH="${PARSED_ARCH:-unknown}"
        D_RES="${PARSED_RESOLUTION:-128}"
        D_CLASSES="${PARSED_CLASSES:-11}"

        # Create metadata.json so ModelManager can discover this model
        cat > "/app/models/digits/${MODEL_NAME}/metadata.json" <<METAEOF
{
  "model_type": "digits",
  "architecture": "${D_ARCH}",
  "resolution": ${D_RES},
  "num_classes": ${D_CLASSES},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "active",
  "notes": "Default model (shipped with container)"
}
METAEOF
        echo "✓ Default digits model copied: ${MODEL_NAME} (arch=${D_ARCH}, res=${D_RES})"

        # Update config to point to new model location
        sed -i "s|digits_model:.*|digits_model: \"/app/models/digits/${MODEL_NAME}/${MODEL_NAME}.xml\"|" /config/config.yaml
        echo "✓ Config updated to use /app/models/digits/${MODEL_NAME}/${MODEL_NAME}.xml"
    else
        echo "⚠ Warning: No default digits model found in /app/digits/selected"
    fi
else
    echo "✓ Using existing digits models from /app/models/digits"
fi

# Check and copy default arrows model
if [ -z "$(ls -A /app/models/arrows 2>/dev/null)" ]; then
    echo "Models directory empty, copying default arrows model..."
    if [ -d /app/arrows/selected ]; then
        MODEL_NAME=$(basename /app/arrows/selected/*.xml .xml)
        mkdir -p "/app/models/arrows/${MODEL_NAME}"
        cp /app/arrows/selected/*.xml "/app/models/arrows/${MODEL_NAME}/"
        cp /app/arrows/selected/*.bin "/app/models/arrows/${MODEL_NAME}/"

        # Parse architecture, resolution, and classes from filename
        parse_model_name "${MODEL_NAME}" "arrows"
        A_ARCH="${PARSED_ARCH:-unknown}"
        A_RES="${PARSED_RESOLUTION:-128}"
        A_CLASSES="${PARSED_CLASSES:-100}"

        # Create metadata.json so ModelManager can discover this model
        cat > "/app/models/arrows/${MODEL_NAME}/metadata.json" <<METAEOF
{
  "model_type": "arrows",
  "architecture": "${A_ARCH}",
  "resolution": ${A_RES},
  "num_classes": ${A_CLASSES},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "active",
  "notes": "Default model (shipped with container)"
}
METAEOF
        echo "✓ Default arrows model copied: ${MODEL_NAME} (arch=${A_ARCH}, res=${A_RES}, classes=${A_CLASSES})"

        # Update config to point to new model location
        sed -i "s|arrows_model:.*|arrows_model: \"/app/models/arrows/${MODEL_NAME}/${MODEL_NAME}.xml\"|" /config/config.yaml
        echo "✓ Config updated to use /app/models/arrows/${MODEL_NAME}/${MODEL_NAME}.xml"
    else
        echo "⚠ Warning: No default arrows model found in /app/arrows/selected"
    fi
else
    echo "✓ Using existing arrows models from /app/models/arrows"
fi

# Symlink config to /app for application to use
ln -sf /config/config.yaml /app/config.yaml
echo "✓ Config linked to /app"

echo "=== Starting application ==="
cd /app

# Execute the CMD
exec "$@"
