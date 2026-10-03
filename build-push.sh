#!/bin/bash
set -euo pipefail

REGISTRY="${REGISTRY:?set REGISTRY, e.g. registry.example.com/user}"
IMAGE="watermeter-inference"
DATE=$(date +%Y-%m-%d)
LABEL="${1:-main}"

TAGS=(
  "${REGISTRY}/${IMAGE}:${LABEL}-${DATE}"
  "${REGISTRY}/${IMAGE}:latest"
)

BUILD_ARGS=""
for TAG in "${TAGS[@]}"; do
  BUILD_ARGS+=" -t ${TAG}"
done

docker build ${BUILD_ARGS} .

for TAG in "${TAGS[@]}"; do
  docker push "${TAG}"
done

echo "Pushed: ${TAGS[*]}"
