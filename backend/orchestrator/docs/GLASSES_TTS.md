# Smart-glasses speech output

Smart-glasses agent replies use `kokoro-onnx` and `onnxruntime` on CPU only.
Synthesis is complete (not streamed), encoded as mono 16-bit WAV, and held in
the process-local ephemeral audio store. The canonical answer text is persisted
through the existing conversation/session flow so follow-ups retain the same
answer that was sent to TTS. Only generated audio is ephemeral: it is not
written to conversations, documents, or memory.

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
concurrency only after measuring the deployment's CPU capacity.

Artifact provenance is the upstream `model-files-v1.0` release. The
`kokoro-onnx` package is MIT-licensed and the Kokoro model is Apache-2.0. If a
deployment overrides either build URL, it must also override the corresponding
checksum build argument. The backend still fails closed with `tts_unavailable`
if the bundled files are missing or invalid at runtime.
`GLASSES_AUDIO_TTL_SECONDS` controls the default five-minute TTL; a successful
authenticated download deletes the object immediately.
