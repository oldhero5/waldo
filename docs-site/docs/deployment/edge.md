---
title: Edge devices
sidebar_position: 4
---

# Edge Deployment

Waldo exposes device registration, model-assignment metadata, heartbeats, and inference-log ingestion in [`app/api/serve.py`](https://github.com/oldhero5/waldo/blob/main/app/api/serve.py). The repository does not supply an edge inference runtime, a Jetson compose directory, or a Pi/Coral image.

## Register a device

These endpoints require bearer authentication. Registration creates a device record accessible through the API; the current Deploy page has no device-management view. The client must implement inference and communication separately.

```bash
curl -X POST https://waldo.example.com/api/v1/devices \
  -H "Authorization: Bearer $WALDO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "front-gate-jetson", "device_type": "jetson_orin", "location_label": "Front gate"}'
```

Returns `{ "id": "...", "status": "registered" }`. Optional fields are `target_id`, `model_id`, and `hardware_info`; `device_type` is a string label, not hardware validation. `GET /api/v1/devices` lists registered devices.

## Heartbeats and model assignments

```bash
curl -X POST "https://waldo.example.com/api/v1/devices/$DEVICE_ID/heartbeat?ip=192.168.1.42" \
  -H "Authorization: Bearer $WALDO_API_KEY"
```

The endpoint updates status, heartbeat time, and optional IP. It returns `assigned_model` with `model_id`, `name`, `version`, and `weights_key` when the device has an assigned model. `weights_key` is a MinIO object key, not a signed download URL. Heartbeat scheduling, downloading weights, verifying compatibility, and applying updates are client responsibilities; no automated OTA flow is supplied.

## Upload offline inference logs

```bash
curl -X POST "https://waldo.example.com/api/v1/devices/$DEVICE_ID/sync-logs" \
  -H "Authorization: Bearer $WALDO_API_KEY" \
  -F "file=@inference-logs.json"
```

The multipart `file` must contain a JSON array of entries with fields such as `timestamp`, `request_type`, `latency_ms`, `detection_count`, `avg_confidence`, `classes_detected`, `input_resolution`, and `error_code`. Returns `entries_imported` and updates the device's sync time.

## Export support

The exporter accepts `onnx`, `torchscript`, `coreml`, `tflite`, and `openvino`. `tflite_edgetpu` is unsupported. An accepted export format does not establish compatibility with a Jetson, Raspberry Pi, or Coral runtime; qualify the exported artifact on the intended hardware separately.
