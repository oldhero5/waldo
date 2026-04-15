---
title: Edge devices
sidebar_position: 4
---

# Edge Deployment

Waldo can push trained models to remote inference endpoints — small Linux boxes running close to the cameras. Two reference targets are documented here.

## NVIDIA Jetson (Orin Nano / NX / AGX)

Jetson devices run Ubuntu (L4T) and have a CUDA-capable GPU. The Waldo edge image is a slimmed compose stack: just `app` (in lightweight mode) + a single inference worker.

```bash
# On the Jetson
git clone https://github.com/your-org/waldo.git --depth 1
cd waldo/edge/jetson
docker compose up -d
```

Register the device with the central API so it shows up on the **Deploy** page:

```bash
curl -X POST https://waldo.example.com/api/v1/devices \
  -H "Authorization: Bearer $WALDO_API_KEY" \
  -d '{"name": "front-gate-jetson", "kind": "jetson-orin-nano", "ip": "192.168.1.42"}'
```

Heartbeats are sent every 30 seconds.

## Raspberry Pi 5 + Coral USB TPU

For lower-power deployments, export the YOLO model to TFLite Edge TPU format:

```bash
curl -X POST https://waldo.example.com/api/v1/models/$MODEL_ID/export \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"format": "tflite_edgetpu"}'
```

On the Pi:

```bash
# Coral runtime
sudo apt install -y libedgetpu1-std
# Pull the edge runtime image (matches the central Waldo version)
docker run -d --device /dev/bus/usb \
  -e WALDO_API=https://waldo.example.com \
  -e WALDO_API_KEY=... \
  -e DEVICE_NAME=garage-pi \
  ghcr.io/your-org/waldo-edge:pi5-coral
```

## OTA model updates

When you promote a new model in the central UI (or call `POST /models/{id}/promote`), every connected edge device picks up the new version on its next heartbeat and downloads the weights from MinIO via a presigned URL.

Logs from each device are pushed back via `POST /devices/{id}/sync-logs` so you can audit edge predictions centrally.
