# Robot Gateway

MQTT-based communication gateway for physical robots. Receives telemetry, dispatches commands, and tracks robot/module activity. Runs as its own container alongside a Mosquitto MQTT broker.

> **Firmware developers**: See [MQTT_PROTOCOL.md](MQTT_PROTOCOL.md) for the exact topic hierarchy and JSON payload schemas the gateway expects.

## Architecture

```
Robot (physical device)
  ├── Module A (sensor)  ──┐
  ├── Module B (actuator) ─┤── MQTT ──→ Mosquitto Broker ──→ Robot Gateway ──→ PostgreSQL
  └── Module C (camera)  ──┘                                       ↑
                                                        Frontend/Middleware
                                                    (HTTP API with service key)
```

Each robot has multiple modules. Each module subscribes as a separate MQTT entity under `robot/{robot_id}/module/{module_id}/...`.

## MQTT Topic Hierarchy

| Topic | Direction | QoS | Purpose |
|-------|-----------|-----|---------|
| `robot/{robot_id}/module/{module_id}/telemetry` | Robot → Gateway | 0 | Sensor data |
| `robot/{robot_id}/module/{module_id}/status` | Robot → Gateway | 1 | Module online/offline/error |
| `robot/{robot_id}/module/{module_id}/command` | Gateway → Robot | 1 | Commands to module |
| `robot/{robot_id}/module/{module_id}/command/ack` | Robot → Gateway | 1 | Command acknowledgement |
| `robot/{robot_id}/module/{module_id}/media` | Reserved | - | Future: image/audio binary payloads |

## HTTP API

All endpoints require `x-service-api-key` header (shared `ORCHESTRATOR_API_KEY`).

### Robots & Modules
- `POST /robots` — Register a robot
- `GET /robots` — List all robots with modules
- `GET /robots/{robot_id}` — Robot detail
- `PUT /robots/{robot_id}` — Update robot
- `DELETE /robots/{robot_id}` — Deregister (cascades)
- `POST /robots/{robot_id}/modules` — Register module
- `GET /robots/{robot_id}/modules` — List modules
- `PUT /robots/{robot_id}/modules/{module_id}` — Update module
- `DELETE /robots/{robot_id}/modules/{module_id}` — Deregister module

### Telemetry
- `GET /robots/{robot_id}/telemetry` — Query telemetry (all modules)
- `GET /robots/{robot_id}/modules/{module_id}/telemetry` — Query by module
- `GET /robots/{robot_id}/modules/{module_id}/telemetry/latest` — Latest reading

Query params: `since`, `until`, `payload_type`, `limit` (max 1000), `offset`

### Commands
- `POST /robots/{robot_id}/modules/{module_id}/commands` — Send command
- `GET /robots/{robot_id}/commands` — List commands
- `GET /commands/{command_id}` — Command status

### Capture Relay
- `POST /api/capture/streams` — Create or reuse a relay session for one module
- `GET /api/capture/streams/{session_id}/status` — Read relay state and viewer paths
- `GET /api/capture/streams/{session_id}/camera.mjpg` — Viewer MJPEG stream
- `WS /api/capture/streams/{session_id}/audio.pcm` — Viewer PCM audio stream
- `WS /api/capture/relay/connect` — Device upstream relay socket using a short-lived Bearer token

### Health
- `GET /health` — MQTT connection + DB reachability

## Database

Uses the same PostgreSQL instance as the orchestrator. Own migration table (`robot_gateway_migrations`).

| Table | Purpose |
|-------|---------|
| `robots` | Robot registry + aggregate last-seen tracking |
| `robot_modules` | Modules per robot (composite PK: robot_id + module_id) |
| `robot_telemetry` | Time-series telemetry with JSONB payloads |
| `robot_commands` | Command lifecycle tracking (pending → sent → acknowledged) |

## Status Definitions

- Robots do not have a first-class status field. The gateway tracks only robot metadata plus aggregate `last_seen_at` based on module traffic.
- Module `status` in API responses is derived, not copied directly from MQTT.
- A module is `offline` when it has not sent telemetry or a module status message for more than 60 seconds.
- A fresh module is `online` by default, even if it never published an explicit `online` status message.
- A fresh explicit module `error` status is surfaced as `error`.
- A fresh explicit module `offline` status is surfaced as `offline` until newer telemetry/status activity arrives.

## Telemetry Payload Format

Robots publish JSON to the telemetry topic:

```json
{
  "measured_at": "2026-04-17T14:30:00Z",
  "payload_type": "temperature",
  "data": {
    "temperature_c": 22.5,
    "humidity_pct": 45
  }
}
```

`payload_type` is a free-form discriminator used for filtering queries. `data` is stored as JSONB.

## Command Flow

1. Frontend sends `POST /robots/{robot_id}/modules/{module_id}/commands`
2. Gateway creates DB record (status: `pending`)
3. Gateway publishes to MQTT topic `robot/{robot_id}/module/{module_id}/command`
4. Status updates to `sent`
5. Robot processes and publishes ACK to `robot/{robot_id}/module/{module_id}/command/ack`
6. Gateway receives ACK, updates status to `acknowledged`

## Unregistered Robots

If a robot or module sends data before being registered via the HTTP API:
- **Telemetry**: Dropped with a warning log (FK constraint prevents storage)
- **Status updates**: Silently ignored (UPDATE matches 0 rows), warning logged
- **Command ACKs**: No-op (commands can only target registered modules)

## Configuration

Uses shared `backend/.env`. Gateway-specific variables:

```bash
# MQTT broker (required — broker rejects anonymous connections)
MQTT_BROKER_HOST=mosquitto
MQTT_BROKER_PORT=1883
MQTT_USERNAME=gateway
MQTT_PASSWORD=change-me-mqtt

# HTTP port
ROBOT_GATEWAY_PORT=8001

# Module presence
# ROBOT_MODULE_OFFLINE_AFTER_SECONDS=60

# Capture relay
CAPTURE_RELAY_PUBLIC_BASE_URL=https://brain.example.com
CAPTURE_RELAY_TOKEN_SECRET=change-me-relay-secret
# CAPTURE_RELAY_TOKEN_TTL_SECONDS=300
# CAPTURE_RELAY_SESSION_TTL_SECONDS=300
# CAPTURE_RELAY_IDLE_TIMEOUT_SECONDS=30
# CAPTURE_RELAY_HEARTBEAT_TIMEOUT_SECONDS=20
# CAPTURE_RELAY_AUDIO_BUFFER_CHUNKS=32

# Migrations (default: true)
# ROBOT_GATEWAY_AUTO_MIGRATE=true
# ROBOT_GATEWAY_AUTO_MIGRATE_FAIL_FAST=true
```

Reuses: `POSTGRES_*`, `ORCHESTRATOR_API_KEY`, `TZ`

## Development

```bash
# With Docker Compose (starts mosquitto + gateway + dependencies)
docker compose up mosquitto robot-gateway

# Locally
pip install -r requirements.txt
uvicorn app:api --host 0.0.0.0 --port 8001 --reload
```

## Mobile Routes (planned)

The middleware is pre-configured to bypass session auth for `/api/robot-gateway/mobile/` paths with Bearer tokens. When mobile routes are needed:
1. Add `google-auth` to `requirements.txt`
2. Implement `get_current_user` in `auth.py` (follow `backend/orchestrator/auth.py` pattern)
3. Add dual-decorated routes (`/mobile/robots`, etc.)

## Future: LLM Integration

The gateway is designed to grow its own intelligence. When LLM processing is needed:
- Add `ollama`/`httpx` to requirements
- Reuse `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL_*` env vars from `backend/.env`
- The `extra_hosts: host.docker.internal:host-gateway` Docker mapping provides local Ollama access

## Future: Image & Audio

- `robot_modules.module_type` includes `camera` and `microphone`
- MQTT topic `robot/{id}/module/{module_id}/media` is reserved for binary media payloads (cameras/mics are just modules)
- Transport mechanism TBD (MQTT binary, HTTP upload, pre-signed URLs)
