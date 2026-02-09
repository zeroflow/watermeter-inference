#!/bin/bash
set -e

echo "=== Water Meter Dashboard Startup ==="

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
if [ -z "$(ls -A /app/models/digits)" ]; then
    echo "Models directory empty, copying default digits model..."
    if [ -d /app/digits/selected ]; then
        MODEL_NAME=$(basename /app/digits/selected/*.xml .xml)
        mkdir -p "/app/models/digits/${MODEL_NAME}"
        cp /app/digits/selected/*.xml "/app/models/digits/${MODEL_NAME}/"
        cp /app/digits/selected/*.bin "/app/models/digits/${MODEL_NAME}/"
        echo "✓ Default digits model copied: ${MODEL_NAME}"

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
if [ -z "$(ls -A /app/models/arrows)" ]; then
    echo "Models directory empty, copying default arrows model..."
    if [ -d /app/arrows/selected ]; then
        MODEL_NAME=$(basename /app/arrows/selected/*.xml .xml)
        mkdir -p "/app/models/arrows/${MODEL_NAME}"
        cp /app/arrows/selected/*.xml "/app/models/arrows/${MODEL_NAME}/"
        cp /app/arrows/selected/*.bin "/app/models/arrows/${MODEL_NAME}/"
        echo "✓ Default arrows model copied: ${MODEL_NAME}"

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
