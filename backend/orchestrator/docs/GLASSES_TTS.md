# Smart-glasses speech output

Smart-glasses agent replies use `kokoro-onnx` and `onnxruntime` on CPU only.
Synthesis is complete (not streamed), encoded as mono 16-bit WAV, and held in
the process-local ephemeral audio store. The canonical answer text is persisted
through the existing conversation/session flow so follow-ups retain the same
answer that was sent to TTS. Only generated audio is ephemeral: it is not
written to conversations, documents, or memory.
When NumPy is available, float-to-PCM16 conversion uses a vectorized path;
the standard-library fallback remains available.

The Docker image bundles the Kokoro v1.0 INT8 ONNX model and v1.0 voices file
from the upstream `kokoro-onnx` release. The build pins both artifact URLs and
SHA-256 checksums, and fails if either checksum does not match. No model volume
or server-side download is required. The INT8 graph keeps the CPU and image
footprint lower than the full-precision model.

The image owns the bundled artifact paths and English language setting. They
do not need to be configured on the server. The effective image defaults are:

```text
KOKORO_MODEL_PATH=/app/models/kokoro/model.onnx
KOKORO_VOICES_PATH=/app/models/kokoro/voices.bin
KOKORO_VOICE=af_heart
KOKORO_LANG_CODE=en-us
KOKORO_MAX_CONCURRENCY=1
```

Only the operational overrides `KOKORO_VOICE`, `KOKORO_MAX_CONCURRENCY`, and
`GLASSES_AUDIO_TTL_SECONDS` are listed as optional settings in
`backend/env.template`. Model paths remain image internals, and English
(`en-us`) remains fixed for v1 deployments.

Synthesis is serialized by default to bound CPU and memory use; increase the
concurrency only after measuring the deployment's CPU capacity. During FastAPI
startup, each backend worker runs one short synthetic inference (`Ready.`) to
load the model and initialize the inference path before accepting requests.
The process-local engine stays referenced for that worker's lifetime, so no
periodic keepalive synthesis is needed. Warmup is best-effort: missing optional
configuration or a warmup failure is logged and does not prevent backend
startup. Each worker loads and warms its own model instance.

The per-command `[glasses] command latency` record includes aggregate
`tts_synthesis_ms` plus import, engine lock/load, semaphore wait, engine create,
WAV encode, and total-call timings. Instrumentation inside the pinned
`kokoro-onnx` 0.4.9 engine further records `phonemize_ms`, `tokenize_ms`,
`onnx_inference_ms` and its call count, `trim_audio_ms` and its call count, and
the remaining `engine_create_other_ms` (batch splitting, voice/style setup,
array assembly, and library overhead). It also records the worker PID, CPU
count, process CPU-affinity count, cgroup CPU quota when available, text
character/word counts (never text), generated audio duration and WAV size, and
whether the selected ONNX providers include an accelerator. Timings are
measured around the dependency's existing calls; the wrappers do not alter
audio output. Recheck this instrumentation when upgrading `kokoro-onnx`.

The record also includes the installed `kokoro-onnx` and `onnxruntime` versions,
artifact basenames and byte sizes (never configured paths), providers active in
the session and available in the runtime, common thread-count environment
overrides, and synthesis real-time factor. The encoded samples are summarized
as peak, RMS, and near-silence fraction with the analysis duration. These
signal values let mobile playback evidence be compared against what Kokoro
actually generated; they do not record or upload speech samples or answer text.

## Controlled CPU benchmark

Use `scripts/benchmark_kokoro.py` for offline thread-count comparisons. It
creates an independent CPU inference session with the requested ONNX intra-op
thread count, warms it, and reports per-run and median inference, phonemization,
trimming, total synthesis, audio duration, and real-time factor. It prints text
lengths but never prints or saves the benchmark text. The default benchmark
fixture is synthetic and contains no user data. It attempts to lower process
priority to reduce interference with live service work.

Run it from the orchestrator checkout or pipe it into an existing container so
the benchmark uses the deployed image's model, libraries, CPU affinity, and
cgroup limits without adding an HTTP endpoint or changing a serving worker:

```sh
docker exec -i -w /app mem-orchestrator python - --threads 0 --iterations 3 \
  < backend/orchestrator/scripts/benchmark_kokoro.py
```

Repeat with `--threads 1`, `--threads 2`, `--threads 4`, and `--threads 8`.
Run at a quiet time: even at lower process priority, the benchmark consumes CPU
and memory in the same container as the live service. Compare medians from the
same host and model artifact; `--threads 0` is ONNX Runtime's automatic default.
Use `--model-path` and `--voices-path` to compare candidate artifacts already
present on the host. This benchmark measures warm full-utterance synthesis; it
does not measure mobile download, Bluetooth playback, or streaming time to first
audio.

The startup log `[glasses] Kokoro startup warmup` records the same stages for
its synthetic inference. A successful warmup should make a user request report
`cold_start=0`; compare startup `engine_load_ms` and `engine_create_ms` to
separate model initialization from inference. A process restart repeats the
warmup.

The same record carries the request's complete `command_id`, assigned at the
confirmed wake and retained through transcription, mobile transport, audio
download, and playback. The mobile `X-Glasses-Command-Id` header is preserved by
the web proxy and appears in `[mobile-proxy]` request/upstream/body-completion
records and backend route/auth records. Command and audio responses return
backend route duration/completion time and proxy receive, session-resolution,
upstream-header, and response-ready timings as headers; the app copies these
allow-listed fields into its exported debug JSONL. The proxy separately logs
downstream body completion or cancellation, including byte count and elapsed
time. Compare mobile fetch/download duration with proxy timing and backend
route duration to locate time before the proxy, between proxy/backend, in the
backend handler, or while returning the body. Epoch timestamps help align
services but duration fields are the reliable comparison if host clocks differ.
Backend logs emitted while a command is executing, including the existing LLM
and tool lifecycle logs, also carry that ID. The mobile export retains complete
command IDs, proxy/backend response timings, audio download phases, and native
playback route/focus diagnostics while continuing to redact credentials, paths,
and audio data.

Android speech events also record the selected native Whisper backend, whether
GPU was requested and actually activated, the native reason when acceleration
is unavailable, model cache/size/modified-time metadata, model-file and native
context initialization durations, and the configured transcription thread
count. The Hugging Face URL currently resolves the mutable `main` revision, so
these fields identify the cached artifact's size and local modification time,
not a content hash or immutable upstream commit.

For TTS playback, Android records one-second MediaPlayer playhead samples plus
duration, `isPlaying`, audio-session ID, player gain, MUSIC stream volume/mute,
focus, route, service types, and app visibility. A completed MediaPlayer event
means the player reached its completion callback; it is not acoustic
confirmation. The native log records the same terminal snapshot for Logcat.
Notification tones use AudioTrack, so an audible notification alone does not
prove the distinct MediaPlayer speech path is audible.

Artifact provenance is the upstream `model-files-v1.0` release. The
`kokoro-onnx` package is MIT-licensed and the Kokoro model is Apache-2.0. If a
deployment overrides either build URL, it must also override the corresponding
checksum build argument. The backend still fails closed with `tts_unavailable`
if the bundled files are missing or invalid at runtime.
`GLASSES_AUDIO_TTL_SECONDS` controls the default five-minute TTL; a successful
authenticated download deletes the object immediately.
