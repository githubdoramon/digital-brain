# Robot Gateway MQTT Protocol

This document is the **source of truth** for the MQTT contract between a physical robot and the gateway. Firmware must match this spec exactly — any deviation means messages get silently dropped or rejected.

## 1. Connection

| Setting | Value |
|---------|-------|
| Broker host | (deployment-specific, defaults to `mosquitto` inside Docker) |
| Port | `1883` (MQTT), `9001` (WebSocket) |
| Authentication | Username + password (required — broker rejects anonymous connections) |
| Keep-alive | 60 seconds recommended |
| Clean session | `true` recommended unless you need QoS 1 offline delivery |

MQTT credentials are provisioned per-robot (separate from the `gateway` user). Ask the operator for the robot's credentials.

## 2. Identifiers

The topic hierarchy is built from two free-form identifiers:

- **`robot_id`** — the whole physical device (e.g. `robot-001`)
- **`module_id`** — a logical subsystem within the robot (e.g. `head-sensors`, `head-vision`, `body-motion`)

A robot with only one subsystem should still define one module (e.g. `head-sensors`). This keeps the data model uniform.

**Both IDs must be registered with the gateway before publishing.** Messages from unknown robots/modules are dropped with a `REJECTED reason=unregistered_robot_or_module` warning. See §7.

## 3. Topic Hierarchy

The gateway **subscribes** to these patterns and **publishes** commands. From the firmware's perspective, publish to the first four and subscribe to the command topic.

| Topic | Direction | QoS | Purpose |
|-------|-----------|-----|---------|
| `robot/{robot_id}/module/{module_id}/telemetry` | **Robot → Gateway** | 0 | Sensor data |
| `robot/{robot_id}/module/{module_id}/status` | **Robot → Gateway** | 1 | Module state transitions |
| `robot/{robot_id}/module/{module_id}/command/ack` | **Robot → Gateway** | 1 | Acknowledge a received command |
| `robot/{robot_id}/module/{module_id}/command` | **Gateway → Robot** | 1 | Commands the module must execute |

A camera or microphone is just another module. Its structured metadata (frame counts, fps, detected-object tags, codec, etc.) goes in the normal `telemetry` topic as JSON. Raw binary data — image frames, audio chunks — is planned for a sibling topic under the same module path:

- `robot/{robot_id}/module/{module_id}/media` *(planned, not yet implemented)* — raw binary payload. Separate from `telemetry` only because JSON is the wrong wrapper for large binary blobs.

Until the media pipeline is built, cameras/microphones should publish their metadata via `telemetry` and omit the raw payload. Transport and storage for binary media are still being designed (likely HTTP upload or pre-signed URLs rather than MQTT for anything sizeable).

## 4. Payload Schemas

**All payloads are JSON.** UTF-8 encoded. The gateway rejects malformed JSON with a `payload is not valid JSON` warning.

### 4.1 Telemetry — `robot/{robot_id}/module/{module_id}/telemetry`

```json
{
  "measured_at": "2026-04-20T09:59:11Z",
  "payload_type": "environment",
  "data": {
    "temperature_c": 22.5,
    "humidity_pct": 45.0
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `measured_at` | ISO 8601 timestamp (string) | **yes** | Robot's wall-clock time when the reading was taken. UTC preferred. Formats accepted: `2026-04-20T09:59:11Z`, `2026-04-20T09:59:11+00:00`, `2026-04-20T09:59:11.123Z`. |
| `payload_type` | string | no (default `"generic"`) | Discriminator for filtering queries. Use short, snake_case identifiers: `temperature`, `imu`, `battery`, `position`, `distance`, `motion`. |
| `data` | object | no (default `{}`) | Arbitrary nested JSON. Stored as-is. No schema enforced — each `payload_type` defines its own shape. |

**Conventions for `data`** (advisory — pick a convention and stick to it per module):

- Numeric readings include units in the key name: `temperature_c`, `distance_mm`, `voltage_v`, `angle_deg`.
- Arrays for vector quantities: `accel: [x, y, z]`, `gyro: [x, y, z]`.
- Booleans for binary states: `motion_detected: true`.

**Do NOT** include `robot_id` or `module_id` inside `data` — they're already in the topic.

### 4.2 Module status — `robot/{robot_id}/module/{module_id}/status`

```json
{
  "status": "online",
  "detail": "calibration complete"
}
```

| Field | Type | Required | Allowed values |
|-------|------|----------|----------------|
| `status` | string | **yes** | `online`, `offline`, `error` |
| `detail` | string | no | Human-readable context (e.g. error message) |

Publish on boot (`online`), before disconnect (`offline`), and on error state transitions (`error` + `detail`).

### 4.3 Command acknowledgement — `robot/{robot_id}/module/{module_id}/command/ack`

```json
{
  "command_id": "cmd_a1b2c3d4e5f6"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `command_id` | string | **yes** | Exactly the `command_id` received on the command topic. |

Publish as soon as the command has been received and accepted (not when it finishes executing — that would be a separate telemetry event).

### 4.4 Command (received from gateway) — `robot/{robot_id}/module/{module_id}/command`

The gateway publishes this **to** the robot. Firmware subscribes and processes it.

```json
{
  "command_id": "cmd_a1b2c3d4e5f6",
  "command_type": "move",
  "payload": {
    "x": 1.0,
    "y": 2.0,
    "speed": 0.5
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `command_id` | string | Echo in the ACK. |
| `command_type` | string | Application-defined (`move`, `stop`, `calibrate`, etc.). |
| `payload` | object | Command-specific arguments. |

## 5. QoS Guidance

- **Telemetry**: QoS 0. High-frequency sensor data; occasional loss is acceptable.
- **Status, Commands, ACKs**: QoS 1. State transitions and control flow must be reliably delivered.

## 6. Retained Messages

- **Retained: yes** — only for module `status` topics. This lets new subscribers see the last known state immediately.
- **Retained: no** — for telemetry, commands, and ACKs.

## 6.1 Presence semantics

- The gateway does not store or expose a separate robot-level status.
- Module presence is derived from activity, not from retained `online` flags alone.
- A module is considered `offline` if the gateway has not received telemetry or a module status message for more than 60 seconds.
- A fresh module with recent activity is treated as `online` unless its latest explicit module status is `error` or `offline`.

## 7. Registration (required before publishing)

The gateway stores telemetry and status updates against foreign-key constrained tables. **The robot and its modules must be registered via HTTP API first**, otherwise every published message will be dropped.

### Register a robot

```
POST /robots
Headers: x-service-api-key: <service-key>
Body:
{
  "robot_id": "head-sensors",
  "name": "Head Sensors Unit",
  "description": "ESP32 with IMU + dist sensors",
  "tags": ["esp32", "head"]
}
```

### Register each module

```
POST /robots/head-sensors/modules
Headers: x-service-api-key: <service-key>
Body:
{
  "module_id": "main",
  "name": "Main Sensor Module",
  "module_type": "sensor",
  "capabilities": ["imu", "distance", "motion"]
}
```

Valid `module_type` values: `sensor`, `actuator`, `camera`, `microphone`, `speaker`, `generic`.

## 8. Complete ESP32 Firmware Example

For a single-module ESP32 identified as `head-sensors`:

### Registration (one-time, via HTTP)

```
POST /robots                         → robot_id: "robot-1"
POST /robots/head-sensors/modules    → module_id: "head-sensors"
```

### On boot

```
PUBLISH robot/robot-1/module/head-sensors/status        (retained, QoS 1)
{"status": "online"}
```

### Telemetry loop (e.g. every 500 ms)

```
PUBLISH robot/robot-1/module/head-sensors/telemetry     (QoS 0)
{
  "measured_at": "2026-04-20T10:00:05Z",
  "payload_type": "sensors",
  "data": {
    "motion_detected": false,
    "distance_mm": 1240,
    "rssi": -72,
    "uptime_ms": 15798
  }
}
```

### On disconnect (via MQTT Last Will)

```
Last Will topic:   robot/robot-1/module/head-sensors/status  (retained, QoS 1)
Last Will payload: {"status": "offline"}
```

Setting a Last Will on connect ensures the broker publishes `offline` if the module drops without a graceful disconnect.

### Subscriptions

```
SUBSCRIBE robot/robot-1/module/head-sensors/command      (QoS 1)
```

On message, execute the command and publish an ACK:

```
PUBLISH robot/robot-1/module/head-sensors/command/ack    (QoS 1)
{"command_id": "<from received command>"}
```

## 9. Common Mistakes (from real traffic)

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Publishing to `robot/{id}/telemetry` (no `/module/{mod}/`) | Message reaches broker but gateway REJECTS with `unrecognized_pattern` | Publish to `robot/{id}/module/{mod}/telemetry` |
| Using `"state"` instead of `"status"` in status payload | `REJECTED reason=missing_status_field` | Rename the field to `status` |
| Putting `module_id` inside the JSON payload and omitting it from the topic | Topic parses as robot-level, module-level data is lost | The topic carries the IDs — payloads must not duplicate them |
| Publishing before registering via HTTP API | `REJECTED reason=unregistered_robot_or_module` | Call `POST /robots` and `POST /robots/{id}/modules` first |
| Sending Unix timestamp in `measured_at` | `REJECTED reason=schema_validation` | Use ISO 8601 strings (`2026-04-20T10:00:05Z`) |
| Publishing command ACKs without `command_id` | `REJECTED reason=missing_command_id` | Include the exact `command_id` from the received command |

## 10. Debugging

- Every received message is logged at INFO with `[mqtt.recv] topic=... size=...`
- Rejections log the reason: `[mqtt.recv] REJECTED topic=... reason=...` or per-handler `[telemetry]/[status]/[commands.ack] REJECTED ... reason=...`
- The gateway subscribes to `robot/#` as a diagnostic catch-all, so any message under the `robot/` namespace is visible in the logs even if it doesn't match a structured pattern

View logs locally:

```
docker compose logs -f robot-gateway
```
