# Smart-glasses speech output

Smart-glasses agent replies use the configured OpenAI-compatible Qwen TTS
service. The backend posts the canonical, sanitized answer to
`/v1/audio/speech` and requests a complete WAV (`response_format: "wav"`). The
URL can be configured as a service root, an `/v1` base, or the full speech
endpoint. A bearer token is optional for deployments that require
authorization. TTS endpoint and credentials are independent of the LLM
endpoint and key; there is no implicit fallback to LLM settings or local
Kokoro inference.

Configure the deployment through these environment variables:

```text
TTS_BASE_URL=http://qwen-tts:8000
TTS_API_KEY=
TTS_MODEL=qwen3-tts
TTS_VOICE=aiden
TTS_TIMEOUT_SECONDS=45
GLASSES_AUDIO_TTL_SECONDS=300
```

`TTS_TIMEOUT_SECONDS` bounds the provider request and defaults to 45
seconds. A command's remaining deadline can reduce that timeout further. The
backend does not retry speech requests. Configure the actual service URL and
credential in the deployment environment; neither belongs in source control.
Startup logs provider/model/voice and whether the endpoint and authorization
are configured, but do not make a network request or print the endpoint or
secret. HTTP connections are pooled per worker and closed during shutdown.

The request contains `model`, `voice`, `input`, and `response_format`. The
backend forwards the confirmed-wake `command_id` in both `X-Glasses-Command-Id`
and `X-Request-ID` headers so the TTS service can correlate provider logs. The
answer text and returned audio are not logged. A successful response must be a
nonempty, uncompressed PCM WAV (one or two channels, 8/16/24/32-bit samples);
invalid or unsupported audio fails closed as `tts_unavailable`/`tts_failed`
rather than entering the playback path.

The canonical answer text follows the existing conversation/session
persistence flow. Generated audio stays in the process-local ephemeral audio
store and is returned as a short-lived authenticated reference; it is never
written to conversations, documents, or memory. `GLASSES_AUDIO_TTL_SECONDS`
defaults to five minutes, and a successful authenticated download deletes the
object immediately.

## Latency diagnostics

The final `[glasses] command latency` event includes the end-to-end command ID,
client timings, route/auth/agent phases, audio storage, and the following TTS
fields (prefixed with `tts_` in the event):

- `tts_synthesis_ms` and `tts_call_total_ms`: complete provider request and WAV
  validation duration.
- `tts_request_total_ms`: request start through completed response body.
- `tts_response_headers_ms` and `tts_response_body_ms`: service time before
  response headers and body transfer time.
- `tts_wav_validation_ms`, response byte count, WAV sample rate, frame count,
  and generated audio duration.
- Provider name, configured model and voice, HTTP status, outcome, safe failure
  class, and text character/word counts. Text contents, endpoint, credentials,
  and audio bytes are excluded.

The provider emits one `[glasses] TTS provider request` summary per request.
It includes status, request/header/body durations, response size, WAV duration,
and sample rate, or a safe failure class. The same command ID remains attached
to backend logs, mobile transport, proxy events, audio download, and playback.
The mobile `X-Glasses-Command-Id` header is preserved by the web proxy and
appears in `[mobile-proxy]` request/upstream/body-completion records. Command
and audio responses expose backend route duration/completion and proxy receive,
session-resolution, upstream-header, and response-ready timings. The app copies
these allow-listed fields into exported debug JSONL. Compare mobile fetch and
download durations with proxy and backend timings to locate time before the
proxy, between proxy and backend, inside the command handler, or returning the
body. Durations are more reliable than comparing timestamps across hosts.

Android speech events also record the selected native Whisper backend, whether
GPU was requested and activated, the native reason when acceleration is
unavailable, model cache/size/modified-time metadata, model-file and native
context initialization durations, and the configured transcription thread
count. The Hugging Face URL currently resolves mutable `main`, so these fields
identify the cached artifact's size and local modification time, not an
immutable upstream revision or content hash.

For TTS playback, Android records one-second MediaPlayer playhead samples plus
duration, `isPlaying`, audio-session ID, player gain, MUSIC stream volume/mute,
focus, route, service types, and app visibility. A completed MediaPlayer event
means the player reached its completion callback; it is not acoustic
confirmation. The native log records the same terminal snapshot for Logcat.
Notification tones use AudioTrack, so an audible notification alone does not
prove the distinct MediaPlayer speech path is audible.
