#!/bin/bash
set -e

echo "=== Water Meter Dashboard Startup ==="

# Allow runtime UID/GID override via PUID/PGID environment variables.
# Default: 1000 (matches Dockerfile). Set to host user's UID/GID to avoid
# permission issues with bind-mounted volumes.
PUID=${PUID:-1000}
PGID=${PGID:-1000}
if [ "$PUID" != "1000" ] || [ "$PGID" != "1000" ]; then
    echo "Remapping watermeter user to UID=$PUID GID=$PGID..."
    groupmod -g "$PGID" watermeter 2>/dev/null || true
    usermod -u "$PUID" -g "$PGID" watermeter 2>/dev/null || true
fi

# Fix ownership of mounted volumes (may be owned by root from previous container runs)
chown -R watermeter:watermeter /app/models /training /config /data 2>/dev/null || true

# Initialize config directory if empty
if [ ! -f /config/config.yaml ]; then
    echo "No config found, copying defaults..."
    cp /config_default/config.yaml /config/config.yaml
fi
chown watermeter:watermeter /config/config.yaml 2>/dev/null || true
echo "OK config"

# Symlink config to /app for application to use
ln -sf /config/config.yaml /app/config.yaml

# Copy default digits model if models directory is empty
if [ -z "$(ls -A /app/models/digits 2>/dev/null)" ]; then
    if ls /app/digits/selected/*.xml >/dev/null 2>&1; then
        MODEL_NAME=$(basename /app/digits/selected/*.xml .xml)
        mkdir -p "/app/models/digits/${MODEL_NAME}"
        cp /app/digits/selected/* "/app/models/digits/${MODEL_NAME}/"

        # Create metadata if not already shipped with model
        if [ ! -f "/app/models/digits/${MODEL_NAME}/metadata.json" ]; then
            cat > "/app/models/digits/${MODEL_NAME}/metadata.json" <<EOF
{
  "model_type": "digits",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "notes": "Default model (shipped with container)",
  "classes": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "NAN"],
  "resolution": 128
}
EOF
        fi

        # Update config to point to this model
        sed -i "s|digits_model:.*|digits_model: \"/app/models/digits/${MODEL_NAME}/${MODEL_NAME}.xml\"|" /config/config.yaml
        echo "OK digits model: ${MODEL_NAME}"
    else
        echo "INFO: No default digits model shipped. Train or import a model via the Training page."
    fi
else
    echo "OK digits models (existing)"
fi

# Copy default arrows model if models directory is empty
if [ -z "$(ls -A /app/models/arrows 2>/dev/null)" ]; then
    if ls /app/arrows/selected/*.xml >/dev/null 2>&1; then
        MODEL_NAME=$(basename /app/arrows/selected/*.xml .xml)
        mkdir -p "/app/models/arrows/${MODEL_NAME}"
        cp /app/arrows/selected/* "/app/models/arrows/${MODEL_NAME}/"

        # Create metadata if not already shipped with model
        if [ ! -f "/app/models/arrows/${MODEL_NAME}/metadata.json" ]; then
            cat > "/app/models/arrows/${MODEL_NAME}/metadata.json" <<EOF
{
  "model_type": "arrows",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "notes": "Default model (shipped with container)",
  "classes": ["0.0", "1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0"],
  "resolution": 128
}
EOF
        fi

        # Update config to point to this model
        sed -i "s|arrows_model:.*|arrows_model: \"/app/models/arrows/${MODEL_NAME}/${MODEL_NAME}.xml\"|" /config/config.yaml
        echo "OK arrows model: ${MODEL_NAME}"
    else
        echo "INFO: No default arrows model shipped. Train or import a model via the Training page."
    fi
else
    echo "OK arrows models (existing)"
fi

# Fix ownership after all copies (entrypoint runs as root, files need to be owned by watermeter)
chown -R watermeter:watermeter /app/models /config /data 2>/dev/null || true

echo "=== Starting application ==="
cd /app
exec gosu watermeter "$@"
