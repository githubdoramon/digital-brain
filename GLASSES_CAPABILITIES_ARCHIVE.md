# Smart-glasses integration capability archive

This archive records the capabilities implemented in the Digital Brain
smart-glasses integration before its removal on 2026-09-29. It is an inventory
for a future restart, not a claim that every capability was reliable or
validated on a physical device. Source code and device diagnostics should be
recreated and independently reviewed before reimplementation.

## Device connection and maintenance

- Android pairing and saved-device selection through `@mentra/bluetooth-sdk`.
- Connection readiness tracking, controlled reconnect/recovery, and persisted
  desired connection state.
- Firmware version checks, update compatibility checks, and an in-app firmware
  update flow.
- SDK event adapters for connection, capture, audio, recording, and button
  events. Internal SDK events were adapted without broadening the public event
  allowlist.

## Hands-free wake commands

- An Android V8 wake path recognizing “hey brain” and “okay brain”. Native
  Sherpa-ONNX keyword spotting proposed candidates from 16 kHz mono PCM. An
  eight-second native PCM ring and ambient-level history avoided sending the
  continuous idle stream to JavaScript.
- Candidate verification used the app's openWakeWord mel and embedding ONNX
  models on a four-second candidate range, with a fixed classifier threshold of
  `0.7614435404638955`.
- On confirmation, native code retained a 1.8-second pre-roll and accumulated
  verification-time audio, then delivered command audio in 80 ms batches.
  On-device Whisper handled transcription; a command ID and phase timings
  correlated wake, transcription, backend processing, audio download, and
  playback.
- The backend command controller supported ordinary conversational requests,
  `slash new`, `front gate`, and `car gate`. Gate actions used fixed
  Home Assistant scripts, avoiding model-selected tools for those exact
  shortcuts. Commands used authenticated, idempotent requests.
- Accepted/rejected candidates, queue/inference pressure, audio levels,
  transcription timing, command results, and LED handoff were logged. The app
  could export wake snapshots and bounded post-wake WAV samples for diagnosis.
- Two persisted troubleshooting switches independently disabled wake
  processing or continuous glasses listening. A Mentra Live BES VAD experiment
  could request reduced audio during idle silence while preserving every PCM
  packet the firmware delivered. It was experimental and did not prove battery
  savings or preserve firmware-clipped audio.

## Spoken responses and alerts

- Conversational answers could be transformed for speech and synthesized by a
  separately configured OpenAI-compatible TTS endpoint. Configuration used
  `TTS_BASE_URL`, `TTS_API_KEY`, `TTS_MODEL`, `TTS_VOICE`, and
  `TTS_TIMEOUT_SECONDS`; credentials were separate from LLM credentials.
- The backend validated WAV responses and stored short-lived, authenticated
  audio references. The Android player tracked focus, command ID, actual and
  expected audio route, player position, and completion state. Speech stayed
  muted until the glasses output route was verified.
- Android notification-access integration could play selected app notification
  chimes and incoming-call alerts through a verified glasses audio route, with
  controls for app selection and notification access.

## Camera capture, recordings, and media sync

- Foreground capture requests and persisted automatic capture schedules shared
  a serialized capture worker. Missed ticks were coalesced, jobs persisted
  before native execution, and failed work retried with bounded backoff.
- Photo transfer used the SDK's automatic path, including direct Wi-Fi and
  phone-relayed BLE fallback. Physical-button capture/gallery reconciliation
  used the glasses local camera server, saved Wi-Fi details, and hotspot
  fallback.
- A durable phone-side capture queue validated local files before acknowledging
  or deleting glasses media, retried uploads, and preserved recoverable items.
  Backend upload endpoints stored capture metadata and uploaded assets to
  Immich; chunked uploads were supported for large media.
- Glasses audio recordings had local M4A capture, restart recovery, a cached
  recording index, playback, and file export. The app also reconciled video and
  gallery captures from physical-button events.
- Maintenance coordination paused conflicting capture, wake, or recording
  work during firmware updates and audio/video ownership handoffs.

## On-device scene understanding and Moments

- Automatic captured photos could pass through a serialized, two-stage image
  pipeline: Fast Vision produced object/count/OCR evidence, then a Balanced VLM
  generated a first-person scene observation. Only one model pipeline remained
  loaded at a time; native resources were released on completion and failure.
- Models were optional downloads with explicit deletion controls. Outputs used
  a shared `moment_observation.v1` schema. Source images stayed on-device; the
  queued backend payload contained the observation, capture time/timezone,
  source type, and nullable location provenance.
- An idempotent Moments API stored observations and exposed a web inspector.
  Event generation and embeddings were not part of that feature.

## Diagnostics and platform footprint

- Local Mentra, wake, command-audio, capture, recording, and model diagnostics
  included export/clear controls and bounded retention. Device snapshots
  included connection, mic recovery, packet/VAD, native queue, battery/current,
  CPU, and device awake/deep-sleep counters where available.
- A shared Android foreground runtime coordinated location, connection,
  capture, wake, recording, and call-alert ownership. The remaining
  `DigitalBrainRuntimeService` also supported independent background location;
  that location feature is outside the glasses integration and remains in the
  app.
- The integration included a patched, version-pinned Mentra SDK, an Android
  Expo native module, Sherpa-ONNX and ONNX Runtime wake assets, optional
  ExecuTorch/Fast Vision scene-analysis dependencies, notification-listener and
  playback services, settings screens, and backend command/capture/moment
  endpoints.

## Validation status to carry forward

TypeScript/lint/native compilation and focused scripted checks were used during
development. The wake-processing controls and VAD power behavior were not
validated by a controlled physical-device experiment. Perfetto and battery
observations motivated the rollback; treat app responsiveness, battery use,
route reliability, wake accuracy, and capture recovery as open questions for a
future design.

Historical database migration files remain immutable for upgrade compatibility.
Migration `0066_drop_smart_glasses_data.sql` removes the legacy capture,
command-execution, and moment tables, including their rows, when deployed. The
application no longer registers or serves the related runtime APIs.
