# Fixed glasses commands

Android validates the wake phrase and matches complete known commands locally after English Whisper transcription. Gate execution uses `POST /mobile/glasses/commands` through the frontend proxy with the existing Google bearer-token authentication. The backend receives no audio/transcript and invokes no model or agent tool loop. Photo capture stays native and uses the existing glasses gallery/Immich sync. The phone briefly prepares the camera through the public SDK and enables glasses-native photo sound after readiness; warm-camera firmware plays its shutter snap without preparation clicks. Unsupported warm-up retains silent capture. There is no bundled shutter PCM or phone media route for this feedback.

The request schema forbids extra fields and accepts only:

- `command`: `open_front_gate` or `toggle_car_gate`.
- `request_id`: a random UUID reserved in private capture metadata before dispatch.
- `captured_at_ms`: the completed capture timestamp; requests older than ninety seconds or more than ten seconds in the future expire.

The authenticated user comes from `get_current_user`. Clients cannot supply a Home Assistant script, entity, service, credentials or user identity. The controller selects the script from private deployment configuration:

| Intent | Environment variable |
| --- | --- |
| `open_front_gate` | `GLASSES_FRONT_GATE_SCRIPT` |
| `toggle_car_gate` | `GLASSES_CAR_GATE_SCRIPT` |

Each value must be an existing `script.*` entity ID, for example `script.example_front_gate`. The existing `HA_URL` and `HA_TOKEN` configure Home Assistant. The backend calls `/api/services/script/turn_on` once with the selected script entity and bounded timeouts; Home Assistant owns the relay pulse duration. Blank or invalid configuration returns `not_configured` without performing an action. `configuration_issues` contains only allow-listed missing/invalid configuration codes (ha_url_missing, ha_token_missing, script_entity_missing, script_entity_invalid); no credential or entity value is returned. Digital Brain action names such as open_front_gate are not Home Assistant script entity IDs. Recreate the orchestrator container after changing its env file. Variables are documented in `backend/env.template`; real mappings belong only in the ignored deployment env file.

At-most-once receipts are written before dispatch under `GLASSES_COMMAND_RECEIPT_DIR` (default `storage/glasses-commands`), isolated by hashed authenticated account and protected by a file lock. Journal writes are atomically replaced and fsynced. The Compose bind mount persists them across container replacement; retain it when deploying the backend. Receipts contain only fixed intent/outcome and random UUID file names. They rotate after one day with a 4096-receipt per-account limit; the short timestamp window prevents old commands from being replayed after rotation.

The response has a bounded `status`: `accepted`, `failed`, `unknown`, `expired` or `not_configured`. A repeated UUID returns its reserved outcome without contacting Home Assistant again; using it for a different intent returns HTTP 409. A crash after reservation, a network failure, redirect or server error remains `unknown`, never an automatic second toggle. Accepted means the script request was accepted, not sensor-confirmed gate movement. Neither the phone nor backend uses an offline action queue or automatic replay. Phone-side capture save recovery does not execute actions.

Native matching, eligibility, private result metadata and photo request details are documented in `mobile/GLASSES_LISTENING.md`. Pending commands are invalidated by listening-generation/account changes or pauses for calls, recordings, OTA or disconnection. The app requests orange LED feedback and a rising double tone only after an accepted execution result, separately from the green wake blink and short single wake tone; neither LED response nor accepted execution proves gate movement. Invalid wakes and unknown commands retain audio/transcripts for inspection without executing anything. The wearer is not authenticated by wake detection.

Validation covers fixed routing, authentication, schema rejection, stale input, configuration errors, identity isolation, duplicate/conflicting IDs, uncertain responses and reservation recovery after a simulated process interruption. Tests use mocked Home Assistant responses and fake entities; they must not operate real gates. Hardware capture, installed firmware behavior, network routing and real relay operation require separate device/deployment validation.
