#!/bin/bash
docker build . -t wmi
docker run -it --rm \
  --name wmi \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --group-add=$(stat -c "%g" /dev/dri/renderD128) \
  -p 8000:8000 \
  wmi

