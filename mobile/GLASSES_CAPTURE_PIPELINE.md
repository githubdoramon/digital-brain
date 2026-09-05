# Glasses capture pipeline

The Android app owns the Mentra Bluetooth connection and configures the physical
camera button for local gallery capture. Short press captures a maximum-quality
photo with medium compression. Long press starts a 720p30 video with audio;
the next press stops it and the glasses enforce a 15-minute maximum.

## Lifecycle

`@mentra/bluetooth-sdk` emits capture/gallery signals. The app reconciles
immediately after those signals and via an Android best-effort background task
with a 15-minute minimum interval. It first probes the glasses' current local
Wi-Fi camera server and only opens the glasses hotspot when that path is not
reachable. The published SDK's scoped local-network bridge is used when
available so joining the hotspot does not permanently change the phone's
internet route.

Each capture is processed sequentially:

1. Discover it from the paginated v3 manifest (`/api/v3/manifest`), falling back
   to `/api/gallery` for older camera-server builds.
2. Download original bytes to the app-private `Digital Brain/Capture Queue`
   directory, or directly to the managed `Glasses Capture Queue` subfolder of
   the user-selected Digital Brain base folder. Android can revoke or reject a
   persisted grant; in that case the visible copy is best-effort and the
   app-private queue remains authoritative.
3. Validate that the committed local file is non-empty and matches the remote
   size when supplied.
4. Acknowledge it with `/api/v3/ack` when protocol v3 is available, otherwise
   use the legacy `/api/delete-files` endpoint. Mentra retains acknowledged
   captures in recoverable trash for its supported seven-day window.
5. Media opens an authenticated upload session and sends sequential bounded
   1 MiB ranges to
   `/mobile/glasses/captures/upload-sessions`; the backend validates and
   reassembles those ranges in its persistent staging volume before using the
   same Immich commit path. This keeps phone-to-backend requests below proxy
   body limits without changing the original file or attempting a direct
   phone-to-Immich upload. The backend then verifies the asset, finds or
   creates the exact `Ramon eyes capture` album, and commits the
   capture ID/checksum record.
6. Delete the phone copy only after that backend confirmation.

Before upload, the app resolves the nearest phone location sample within a
10-minute tolerance. It first checks the local location drain queue (which may
contain samples not uploaded yet), then queries
`/mobile/location/history/nearest`. The stored location includes the capture
timestamp, selected sample timestamp, source, and offset provenance. Missing
location never blocks media upload.

The local queue persists every pending/failed record and its retry/backoff
state; it is not silently size-trimmed while media is awaiting upload. Per-entry
upload failures are surfaced in the Smart glasses sync card, rather than
being hidden behind a successful reconciliation pass.
On startup and every sync, retained media in `Glasses Capture Queue` is also
imported back into that upload queue when a prior app-data loss left a visible
file without its local queue record.

## Automatic image enhancement pipeline

Settings → Smart glasses → Automatic scene capture is an opt-in local
pipeline. Its toggle and one positive whole-minute interval persist in app
storage and are restored after process or device restart. On Android,
enabling it starts a connected-device foreground service with a persistent
system notification. A native foreground-service clock emits the configured
cadence to the live JavaScript runtime, while the JavaScript timer remains a
coalesced fallback. The app also registers an Android background task as a
restart/catch-up path. Android WorkManager enforces a 15-minute minimum and may
defer it further after the process is killed, so sub-15-minute recovery cannot
be guaranteed after force-stop/process death. Every native tick and scheduled
capture is recorded in `image-enhancement-pipeline.jsonl`.
Returning the app to the foreground also requests a catch-up capture when the
toggle is still enabled.

Each queued job requests one high-quality, medium-compression photo with
`save=false`, `sound=false`, and the SDK's local phone photo receiver. The
`auto` transfer mode attempts direct glasses Wi‑Fi delivery first and uses the
SDK's phone-relayed Bluetooth transfer when direct delivery cannot reach the
receiver. This is deliberately separate from gallery mode: physical-button
photos continue to be reconciled and uploaded by the normal queue, while
automatic photos never enter that queue. A successful automatic enhancement is
durably queued as a source-independent `moment_observation.v1` and delivered
to `POST /mobile/moments/batch`; it never uploads the source image or model
diagnostics. One automatic
capture/enhancement may be in flight at a time; overlapping interval ticks are
coalesced into at most one pending job and persisted before execution. Failed
jobs retry with bounded backoff. Native camera requests and
enhancement each have a bounded timeout, so a wedged glasses or model call is
recorded as a failure instead of leaving the settings screen in an endless
"capturing" state. An enhancement timeout actively interrupts ExecuTorch
generation before the coordinator unloads the model; it does not merely detach
the UI wait while CPU inference continues in the background.

Automatic photos are copied into the managed `Image Pipeline Temp` folder and
the JSONL diagnostic log into `Exports` inside the user-selected Digital Brain
storage base. App-private fallbacks use the same `Digital Brain/Image Pipeline
Temp` and `Digital Brain/Exports` layout. The validated app-private copy is the
capture critical path; Android document-provider copies run on a
serialized asynchronous storage queue and never delay image understanding.
The append-only private JSONL file is mirrored on a coalesced timer rather than
being fully rewritten and followed by a full photo-folder scan for every log
entry. The explicit **Save image enhancement log** action and general Mentra
diagnostics both write to `Exports`. Neither action bypasses the user-selected
Digital Brain base folder. Startup and explicit storage sync reconcile any
retained private files and migrate the previous experimental storage layout.
They are retained for manual
inspection. Every photo runs through
the serialized Fast Vision → Balanced VLM coordinator, which unloads native
resources after each stage and failure. Balanced inference uses a short-lived
canonical JPEG transcoded through Android's bitmap decoder. This preserves the
original Mentra file while avoiding OpenCV differences in raw glasses JPEG and
URI handling. The old selected-photo benchmark UI, LiteRT benchmark, and
benchmark run-history store are not part of this pipeline. In particular, the
coordinator no longer rewrites benchmark history at every model phase. Model
downloads and deletion live under Settings → Smart glasses → Scene analysis
models. On its first operation after an upgrade, the coordinator also removes
the old Gemma LiteRT model and incomplete download from the former app-private
model store so the retired benchmark cannot leave roughly 2.6 GB orphaned.
The separate JSONL export records safe
capture metadata, dimensions, model versions, timings, detector/enhancement
results, skipped ticks, and errors. Capture phases separately time connection,
receiver startup, camera request, transfer after request, private copy, shared
copy, and image understanding. Start/end/failure entries also record Android
battery percentage, charging state, thermal status, and app memory. Logs omit
paths, URIs, EXIF, auth data, and OCR text.

The moments queue is idempotent by a mobile-generated UUID. It retains an
entry until the backend returns `created`, `updated`, or `duplicate`; rejected
or offline entries remain durable for later retry. The moment includes the
canonical final observation plus the closest phone-location provenance when
available. Source photos remain in `Image Pipeline Temp` during this testing
phase and are not deleted by the moments acknowledgement.

Balanced receives explicit first-person context: every automatic photo comes
from the user's worn glasses, so the user is an active participant even when
behind the camera. It may cautiously infer what the user is doing from visible
scene evidence. It must not count the wearer as a visible person or invent the
wearer's pose, clothing, expression, or identity.
Likewise, a camera-server/hotspot connection failure remains visible even when
the queue is empty: zero discovered captures cannot be presented as an
up-to-date sync. The saved SDK default is a pairing target only, so the screen
reports it separately from an active, fully booted Mentra session and offers a
reconnect action.

Digital Brain serializes all Mentra controller ownership in one in-process
connection operation. Startup, foreground resume, sync, manual Connect, and
pairing join or wait for that operation instead of issuing `cancelExisting`
while a link is connecting/bonding/booting. This follows Mentra's own mobile
reconnect policy. Only after the boot window expires does the app perform one
ordered controller disconnect, short Android BLE release delay, and reconnect.
The regular Android Bluetooth/audio pairing is OS-owned and can coexist with
this control session; it is not a second companion app controlling the glasses.
Transfers stream directly to disk (including long videos), and the mobile
proxy forwards the request body as a stream. FastAPI keeps the incoming
`UploadFile` in a spooled temporary file; the backend hashes it in 1 MiB
chunks and sends a bounded multipart stream to Immich without materializing
the video in Python memory. Manually
deleting an unuploaded file marks it `missing` to prevent an infinite retry
loop. Captures are never discarded automatically before the backend confirms
the Immich asset.

Android's Storage Access Framework returns document/tree URIs whose exact shape
varies by provider. The app treats the selected Digital Brain base folder as the
permission boundary, creates managed subfolders through Android's document
provider, and preserves the exact child document URI returned by that provider.
It must never reconstruct a child tree URI because Android has not granted that
synthetic tree. A SAF copy failure never blocks the durable queue, glasses
acknowledgement, or backend upload; the file stays in app-private storage until
the backend confirms it.

## Glasses audio recording (Android-first)

This is an Android-only feature for now. Settings → Smart glasses → Glasses
recordings provides the only start/stop controls: it does not bind or alter a
physical glasses button, so the photo/video controls above retain their current
behavior.

The app reuses its one Mentra SDK session and turns on the glasses microphone
only while a recording is active. Mentra's 16 kHz mono PCM reaches a native
AAC encoder before the React Native bridge, which writes a user-visible `.m4a`
to `Digital Brain/Recordings`. A connected-device foreground service keeps the
native encoder alive while the app is backgrounded or the phone is locked. The
recording does not survive a force-stop or terminated process, and v1 has no
upload, transcription, backend processing, or retention cap.

Each completed file is indexed locally and shown newest first with its
timestamp, duration, and size. The app can play, rename, and delete the actual
SAF file. Bluetooth loss or low free storage finalizes a playable file when the
MP4 container can be completed; otherwise it deletes the invalid partial file.
After a process interruption, the next app launch keeps a partial file only
when Android's media extractor can prove it contains an audio track.
On user stop, native encoder shutdown stops accepting PCM first and the
mic-disable request follows without blocking the screen; the screen becomes
ready as soon as native capture has stopped, while SAF indexing and the
saved-recordings refresh complete asynchronously. Native
completion notifications are idempotently coalesced so Bluetooth teardown
cannot create duplicate library entries or race the user-stop save.

## Wake-word acknowledgement (Android-first)

Android builds automatically enable the local personalized wake-word detector
after a saved Mentra Live connection becomes both connected and fully booted.
The runtime is initialized from `mobile/index.js` before Expo Router, packages
the two ONNX feature models plus the personalized classifier, and maintains one
streaming detector while its glasses PCM session is continuous. Mentra delivers
16 kHz, mono, signed PCM16; the runtime serializes those chunks through a
bounded queue and resets detector history after reconnects, errors, or a real
audio-source handoff.

The existing connected-device foreground service is the shared glasses-runtime
lease for wake listening and automatic capture. On Android 14+, it starts from
background/headless work with only non-while-in-use service types; the
microphone type is promoted only after the host app is visibly resumed. This
avoids Android rejecting a background service restart simply because
`RECORD_AUDIO` is granted. It keeps the process eligible while the app is
backgrounded or locked, but it is not a promise to survive a force-stop or
restart before the app launches again. Location remains a separate Android
runtime: it preserves quiet stationary capture and uses its own location
foreground-service mode only while moving.

Glasses audio recording and video recording own the microphone. The wake
runtime releases its PCM subscription before either starts, resets its model
state, and resumes only after the owner reports completion. A confirmed wake
dispatches one short blue RGB LED blink through the SDK's non-blocking native
path; diagnostics record the JS-to-native dispatch time rather than waiting
for the glasses' optional response. Firmware exposes no separate target for an
internal light, so the first physical-device test must verify the visible LED before it is considered
the final acknowledgement.

Wake lifecycle, connection readiness, PCM validation, queue/backlog, inference
timing, model failures, detector scores, detections, mic ownership, and LED
responses all append to the existing exportable Mentra diagnostics JSONL. The
diagnostic log is bounded. The POC also maintains a separate, focused
wake-command JSONL with its matching local WAV clips; it contains no general
connection/capture noise. Both logs use authoritative file generations when
cleared, so pending writers cannot restore an old trace to the new export.
Each exported file includes a timestamp in its filename so a newly exported
file cannot be mistaken for an earlier one.

For the command-transcription POC, a confirmed wake immediately begins its
bounded in-memory command stream and retains both the detector's 1.8-second
pre-roll and any queued post-detection PCM. The speech-energy gate ignores the
first 350 ms of wake-word tail when deciding whether speech has started, while
still retaining that audio for Whisper. This prevents the pause after “hey
brain” from ending a command before it begins.
It runs a stateful 120 Hz–7 kHz band-pass copy of each command chunk before
voice activity detection and Whisper, removing handling rumble and high-
frequency hiss while retaining the original PCM unchanged for investigation.
The rolling low-percentile ambient threshold is adaptive on that filtered
signal (with a bounded 0.018–0.08 RMS range) to recognize the start of speech,
then requires a higher sustained continuation threshold (at least 0.075 RMS
and 1.6× the measured noise floor) before incidental room noise can keep a
command open; both raw and filtered levels, thresholds, and chunk counts are
recorded for diagnostics. It never endpoints within the initial 3-second
command window after wake, then ends after 1.5 seconds of silence or eight
seconds total. The shared warmed English-only `ggml-base.en.bin` Whisper
context receives the filtered 16 kHz PCM through `transcribeData`. The current
Android `whisper.rn` native build is CPU-only;
the command trace records the runtime-selected accelerator and the native
reason when GPU is unavailable. If the native bridge reports that its Whisper
context disappeared, the command path records the invalidation, recreates one
context, and retries the same PCM exactly once; it records both native context
identities and the recovery result. The retained PCM is never trimmed to remove the wake phrase:
the detector records its source-audio decision and pre-roll time bounds, but a
decision is not an exact phonetic boundary and cannot safely crop an immediate
command. After transcription, the POC uses the wake model label's final word
as a fuzzy anchor in the initial recognised words, which handles Whisper
variants such as `okay brain` without a list of aliases. If Whisper merges
the wake phrase into one near-phonetic token, the bounded first-token check
also removes variants such as `Hebrin` before dispatch. The focused trace
keeps raw and normalized transcripts, removal method, and source-audio timing.
For this debugging POC, the original command PCM (the unfiltered diagnostic
source) is also written as a 16 kHz mono WAV after endpointing. The app keeps
the latest 40 clips app-private; **Download wake-command investigation** copies
the focused trace and retained clips to `Digital Brain / Wake Command Debug`.
The trace records PCM chunk count/gaps, speech gate values and wake-tail guard,
endpoint reason, LED dispatch, and each model timing. The SDK has no yellow LED
value, so the listening-finish acknowledgement uses its supported orange RGB
value as the yellow-equivalent.

## Setup and test protocol

Install `@mentra/bluetooth-sdk` and build a native Android development or
production variant; Expo Go cannot provide the native Bluetooth module. On the
first search, Android must grant the app **Nearby devices** (Bluetooth scan and
connect) permission; the app requests it when **Search for glasses** is tapped.
Open Settings → Smart glasses, tap **Search for glasses**, select the
discovered Mentra Live, and wait for the connection to finish before applying
capture settings. Android's system Bluetooth pairing screen is not a substitute
for the SDK scan: the SDK also stores an app-local default device and BLE
address. No separate "pairing mode" is required when the glasses are awake and
advertising.
The app only attempts automatic reconnection after a default device has been
explicitly saved; an unpaired install remains idle instead of calling
`connectDefault()`. In Settings → Storage, choose the shared Digital Brain base
folder. The app creates its `Recordings`, `Glasses Capture Queue`, `Image Pipeline
Temp`, and `Exports`
subfolders through the granted document tree. Configure the backend with the
existing Immich variables (`IMMICH_SERVER_URL`, `IMMICH_API_KEY`) and run the
database migrations.

On a physical device, test: one short press online; one short press while the
phone has no internet; a long press followed by stop; a 15-minute-cap recording
only when safe; disconnect during download; kill and relaunch during each
queue state; retry after restoring internet; and duplicate reconciliation.
For each case confirm the original file is present while pending, the capture
is acknowledged on the glasses only after local validation, Immich contains
one asset in `Ramon eyes capture`, and the phone file disappears only after
backend confirmation. Also confirm a user-deleted pending file becomes
`missing`, and that scoped hotspot cleanup restores normal internet access.

The camera-server and ACK behavior is supplied by MentraOS. The package is
MIT-licensed; verify the release's SDK/firmware terms before distribution.

For glasses audio on a physical Android device, test a start/stop recording,
lock/background the phone while recording, disconnect Bluetooth mid-recording,
attempt a recording with low free storage, force-close/relaunch during a
recording, then play, rename, and delete the saved file from both Digital Brain
and another Files-capable app. Confirm the `.m4a` is playable outside the app,
photo/video buttons still work as before, and interrupted partials are either
usable or removed.

## Glasses alerts (Android-first)

Settings → Smart glasses → Glasses alerts is a separate local-only notification
feature. It uses Android notification access to receive a notification's source
package name, filters that package against the user's explicit allow-list, then
plays a short two-note PCM chime only through the active Bluetooth audio output
whose device name matches the remembered Mentra audio device. It never retains,
uploads, logs, or displays notification titles, body text, sender/people data,
actions, or icons. A package-agnostic two-second cooldown prevents bursts from
several selected apps becoming repeated tones.

Incoming phone calls are intentionally independent of the app allow-list.
After the user separately grants `READ_PHONE_STATE`, the notification listener
observes only `RINGING`, `OFFHOOK`, and `IDLE`: ringing starts a distinct
repeating two-note glasses ring and either later state stops it. The app never
reads, retains, or presents a caller number. A temporary media-playback
foreground service keeps the repeating ring alive while the call is incoming;
it has a silent, low-importance Android system notification and stops promptly
when the call state changes.
The notification listener starts that repeat loop before requesting the
foreground service, so an Android/OEM refusal to promote background media
playback does not degrade the alert to a single tone. `CATEGORY_CALL`
notifications from a selected dialer are excluded from the ordinary app-chime
path; only telephony state creates the incoming-call ring.

Do not replace or suppress Android's normal phone notification/ring behavior.
The glasses alert requests transient ducking audio focus, so existing glasses
media can reduce briefly while the alert plays. If the expected Mentra audio
route is not connected, the feature remains silent rather than routing sound to
the handset speaker or an unrelated headset. It also stays silent while Android
reports the phone is both interactive and unlocked; that privacy-preserving
proxy avoids a redundant glasses alert while the user is already using the
phone, without collecting usage history or screen content.
That suppression applies only to automatic alerts; the explicit settings app
chime and call-ring previews bypass it so the user can verify the selected
Mentra audio route while using the phone.

On a physical Android device, pair and audio-pair a Mentra Live, grant
notification access and phone-state permission, select two launchable apps, and
test the app chime plus call-ring previews. Verify a selected app creates one
glasses chime, an unselected app creates none, notification text never appears
in Digital Brain diagnostics, the phone's regular notification sound remains,
media ducks briefly, an incoming call repeats only in the glasses until it is
answered/declined, and disconnecting the Mentra audio route produces no sound
from the phone or another Bluetooth device.

## Wake commands and agent audio (Android-first)

After the existing local English `ggml-base.en` transcription completes, the
transcript enters a typed device-command interception registry. The registry is
empty in v1, so spoken `slash new` (which the backend maps to `/new`) and every
other transcript are sent through the authenticated `POST /mobile/glasses/commands` proxy using one stable UUID
`command_id`, the existing chat thread id when available, and the normal
timezone/location `client_context`. The mobile command state machine permits
one in-flight command, pauses wake listening after dispatch, and always resumes
it after completion, failure, or the 70-second hard deadline. It never creates a
new id for a retry or plays a response that arrived after timeout.

The backend outcome is discriminated as `control_completed`,
`shortcut_completed`, `agent_response`, or `error`. Gate/shortcut and slash-new
commands are silent. An `agent_response` supplies canonical answer text for
normal backend-owned conversation storage plus an authenticated ephemeral audio
route; the app downloads that audio to app-private cache storage, verifies it,
then plays it completely through the preferred Mentra Bluetooth audio device.
The temporary file is deleted on every terminal path. Orange means a completed
shortcut or audio ready immediately before playback; blue remains wake detected;
red blinks for backend, routing, download, TTS, playback, or lifecycle errors.

The Android `DigitalBrainGlassesAlerts` module owns file-based speech playback,
audio focus, explicit preferred-device routing, completion/error events, and
player cleanup. Audio bytes never cross the React Native bridge. Existing
retained wake-command WAVs and their debug logging are unchanged.

Physical-device validation checklist (static builds do not prove these paths):

1. Pair and audio-pair Mentra Live, lock/background the phone, say “Hey Brain”,
   and confirm blue wake acknowledgement, no handset audio, and wake listening
   pauses after local transcription.
2. Exercise a normal agent question, a `control_completed` gate, a
   `shortcut_completed` including spoken `slash new`, and an `error`. Confirm exact
   orange/red semantics, silence for shortcuts, and no duplicate execution.
3. Confirm agent audio downloads only with the authenticated route, plays fully
   through Mentra, ducks existing audio appropriately, resumes wake listening
   only after completion, and deletes the private temp file.
4. Disconnect glasses during backend execution, download, and playback; deny
   auth; kill/background the app; and hold the backend past 70 seconds. Confirm
   red blink, listener recovery, no new command id, no late playback, and no
   leaked audio/auth data in exported diagnostics.
