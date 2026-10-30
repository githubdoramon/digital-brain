# Spoken chat through glasses

Android routes a completed “Hey Brain” capture to the fixed local command matcher
first. A matched gate/photo command retains its existing execution path, even if
execution fails or is not configured. If no command matches, a nonempty phrase
with a verified, anchored Hey/Okay/OK Brain wake enters regular chat. The same
bounded Brane/bring/Brian wake aliases apply. Negated, ambiguous and multiple
command phrases may be discussed in chat; they cannot bypass device execution
checks. Empty phrases and missing wakes do nothing.

## Shared session and evidence

`POST /mobile/glasses/chat` uses the existing frontend mobile proxy and Google
bearer authentication. It accepts a bounded transcript, random request UUID,
capture timestamp and capture timezone. The backend verifies the wake again,
strips it, and calls the same nonstreaming `ask` handler as regular chat without
an explicit thread ID. Existing main-session resolution owns the thirty-minute
idle timeout and history. “Slash new” maps to `/new`. Each turn requires a wake;
there is no follow-up microphone window.

The original capture time/timezone follow the request into model calls and
persisted message metadata. The normal chat persistence/knowledge path stores
the user phrase as evidence and the answer as assistant text. Assistant text
never becomes evidence. User metadata marks `input_source=glasses`; assistant
metadata marks `response_modality=speech`. No schema migration is required.

Main and memory-expert prompts receive a request-scoped voice instruction:
usually one or two short sentences, around twenty seconds, natural English,
without Markdown, URLs or long lists. Formatting cleanup happens before
persistence and synthesis so one canonical answer is stored and spoken.
The controller also enforces at most sixty words/600 characters before persistence,
ending at a complete sentence when possible, so verbose model output cannot
produce an unbounded spoken reply. Other requests and restored chat history do
not inherit the voice modality.
Existing confirmation/clarification workflows remain owned by normal chat.

The controller excludes Home Assistant, shell and skill-script tools from this
modality and rejects attempted calls before dispatch, including calls not present
in the advertised tool list. Gate and camera execution remain owned by the fixed
command path. The language model can explain a rejection or ask for a single
supported command, but cannot execute a rejected device request through chat.

## Speech and native playback

The backend image bundles the original checksum-verified quantized Kokoro model,
voices and pinned CPU runtime. `TTS_PROVIDER=kokoro` is the default. No TTS URL
is required, and legacy `TTS_BASE_URL`/key/model/voice settings cannot switch
the provider. Inference is serialized on
one worker with two CPU threads; timeouts retain the worker slot until inference
actually finishes, so interrupted calls cannot accumulate queued synthesis.

For an explicit external-provider override only, configure:

```dotenv
TTS_PROVIDER=openai_compatible
TTS_BASE_URL=http://tts.example:8880/v1
TTS_API_KEY=
TTS_MODEL=kokoro
TTS_VOICE=af_heart
TTS_TIMEOUT_SECONDS=20
```

The URL also accepts a host or full `/v1/audio/speech` path. Without that override, synthesis runs inside the backend. LLM credentials are
never reused. Complete synthesis is requested as WAV; responses must be
nonempty mono PCM16 at a supported rate, at most four MiB and forty-five seconds.
Provider redirects, retries, oversized streams and malformed/truncated WAVs are
rejected. Local CPU synthesis has a sixty-second deadline, including cold initialization
and phonemization, within the 300-second chat request. The optional HTTP provider
retains its separate deadline capped at twenty-five seconds. Local deadline
failures report `tts_timeout`; timed-out inference retains its slot until it
actually finishes, without retrying or queuing new work. Validated PCM and its rate are returned inline in a
`no-store` authenticated JSON response. Reply audio stays in bounded memory on
both sides; no reply audio files or public download URLs are created.

The Android native speech worker owns the request and playback. During a chat
turn, the detector is closed and incoming PCM/VAD is ignored. The microphone
stays armed under the existing runtime ownership. A fresh detector resumes after
playback/failure, retaining the wake cooldown and excluding response echo/pre-roll.
There is one serial worker, with no new JS, Headless JS or offline request queue.

Speech reuses `GlassesAlertPlayback`: preferred glasses-only Bluetooth output,
muted route verification, immediate stop on route change/additional outputs,
transient ducking focus, existing chime gain, and Android media mute/DND. Calls
interrupt speech; notification chimes and wake cues do not. Speech never falls
back to the phone or changes system volume. Playback is bounded to fifty seconds.

## Cancellation and recovery

Calls, disconnect, OTA, manual recording, sign-out and disabling listening
invalidate the native generation, cancel the HTTP connection, and stop playback.
The proxy forwards client cancellation. The backend cancels its running chat/TTS
task on disconnect/deadline. An already dispatched regular chat tool effect
cannot be undone by transport cancellation; requests are never automatically
retried. Native requests have a 320-second watchdog and job wake locks are
bounded to 420 seconds including transcription and playback.

Before chat/tool execution the backend fsyncs an owner-scoped UUID claim under
`GLASSES_COMMAND_RECEIPT_DIR/chat` (the existing persistent command mount).
Duplicates return conflict, including after process restart or uncertain timeout.
Claims contain no text/audio and are bounded to 4096 per owner/day. Captures older
than ninety seconds expire, so old requests cannot replay after claim rotation.
Native SAF-save retries and capture recovery never dispatch chat or speech.

If speech fails after chat completes, the text reply remains in the normal chat
and the native Listening screen reports that it could not play the reply.
Private wake JSON records chat status, thread reference and safe audio failure
reason. General logs include only fixed `[glasses-chat]` stage/reason events and suppress content-bearing chat work in this modality,
including its deferred callbacks. Mirador retains static request/agent/model/tool
timing and outcomes without text, IDs or audio. Original capture WAV/JSON saving
remains unchanged.

## Validation

Backend tests exercise authenticated routing, shared-handler/session selection,
origin time, owner-scoped duplicate claims, cancelled/timed-out tasks, canonical
answer persistence, audio failure, bounded WAV validation, Kokoro request format,
device-tool blocking and log suppression. Native JVM tests cover ordinary and
ambiguous fallback, missing/empty wakes, matched-command priority, length bounds
and PCM validation. The mobile proxy test checks bearer/cancellation forwarding
and `no-store` responses.

Hardware validation is still required for locked-phone chat, physical audio
routing, response echo exclusion, real provider latency, call/OTA/disconnect races,
and memory/battery cost. Host tests and compilation do not establish those results.

Audio validation diagnostics distinguish empty and overlong Kokoro output and
record only frame count, sample rate and duration. Android waits up to 310 seconds
for the bounded 300-second server turn, with a 320-second HTTP watchdog. These
bounds accommodate regular chat latency; capture freshness and session inactivity
limits remain unchanged.

Android speech completion uses the end-of-audio playback marker and actual frame
progress after resetting the muted primer, rather than elapsed duration. Restart
return codes are checked; stalled playback fails with a safe reason. The existing
glasses debug export includes bounded content-free playback metadata. A `spoken`
result establishes Android playback progress on the verified route, not acoustic
proof that the wearer heard it.

Remote WAV encoders may use explicit unknown-length RIFF/data size sentinels
(`0xFFFFFFFF` or `0x7FFFFFFF`) for streaming output. After the bounded HTTP body
is fully received, validate duration from actual PCM bytes for those headers.
Ordinary declared-length truncation, partial PCM frames, unsupported formats,
empty/overlong audio and response-size limits remain rejected.

The whole-turn server deadline is 300 seconds; Android reads for 310 seconds
with a 320-second request watchdog. Native job wake locks and recognition-test
worker joins are bounded to 420 seconds to include transcription and playback.
Synchronous routing/model inference runs on worker threads with copied time,
privacy, tracing and request-scoped model policy context, so it cannot prevent
the HTTP event loop from observing cancellation and deadline expiration.
Already-running provider inference can finish after cancellation, but the
cancelled coroutine cannot continue to tool execution, persistence or speech.
This does not make a multi-round model response fast; it permits longer normal
chat runs within an explicit bound. The observed failing run took 206 seconds,
including an initial routing failure and seven model rounds, and exceeded the
previous phone watchdog before synthesis could finish.

## Explicit spoken-reply test

`POST /mobile/glasses/chat/speech-test` accepts an authenticated, explicit test with
`{"text":"Hello."}` (1–400 trimmed characters). It calls the same configured TTS
provider and returns the normal ephemeral PCM envelope and safe `audio_error`.
It never calls the chat model, claims a chat request, changes the main session,
persists text/audio or queues learning. It shares per-owner admission with chat,
allows no automatic retries and cancels synthesis on disconnect or its 80-second
server deadline. The frontend proxy propagates cancellation and sets `no-store`.

Settings → Glasses → Test spoken reply owns a foreground native test using the
same authenticated native transport, PCM validator and glasses-only player.
The native read deadline is 85 seconds, with a 90-second request watchdog and the
normal bounded playback wait. Screen exit/background, calls, recording, OTA,
disconnect, sign-out and disable invalidate pending output. Listening is paused
without enabling it or arming a microphone; an already-active detector is replaced
with a fresh one afterward. The diagnostic export retains only test timestamps,
outcome and safe errors alongside content-free playback metadata.
