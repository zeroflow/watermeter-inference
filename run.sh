#!/bin/bash
docker run -it --rm \
  --name wmi \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --group-add=$(stat -c "%g" /dev/dri/renderD128) \
  -p 8001:8001 \
  wmi