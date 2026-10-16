# Glasses speech comparisons

Settings → Glasses → Listening → Compare recognition runs the same saved glasses audio through selectable local models and the existing Digital Brain server. It reports accuracy and latency without choosing a winner. Replay has no access to command dispatch: gates and camera are never operated.

## Dataset

Copy [examples/glasses-speech-dataset.json](examples/glasses-speech-dataset.json) as `dataset.json` into a folder containing the WAV files you want to evaluate. Edit each reference transcript by listening to the recording, independently of existing Whisper output. Include the wake phrase and any preceding/extra speech actually present. Set `expectedCommand` to `open_front_gate`, `toggle_car_gate`, `take_picture`, or JSON `null` when the utterance must be rejected. `tags` is optional and retained in exports. Use empty reference text for genuinely silent samples.

All filenames must be unique, flat paths in that folder. A run is limited to 10,000 attempts (samples × candidates × cold/warm repetitions). The first version accepts 1–512 mono PCM16, 16 kHz WAVs, each at most thirty seconds. The existing glasses capture format is supported. Original WAV/JSON files are never modified. The runner copies selected WAVs and human labels into account-scoped private storage, hashes the dataset and each WAV, and keeps one current resumable run. Starting another run replaces that private run; export before replacing it. Exported reports remain user-owned files.

Choose the dataset folder, models and number of warm repetitions, then Start new comparison. Models download only when selected. Pause stops further work; Resume retains already journaled results. Exports go to `<Digital Brain>/Exports/Speech tests` as JSON and CSV. JSON includes per-sample transcripts/reference labels, decisions, errors, timings, hashes and aggregate results; CSV has one row per measured attempt. Neither audio nor transcripts enter general diagnostics or observability.

## Models and execution

Local catalog: English Tiny/Base/Small/Medium in full precision and Q5/Q8 variants, plus Large v3 in full precision/Q5 and Large v3 Turbo in full precision/Q5/Q8 variants. Artifact sizes, revisions and SHA-256 checksums are pinned in `speech-eval/models.json`. Models are checked before use. Large models may exceed available memory or the installed engine's capabilities; failures are results, not automatic fallback. No model is downloaded merely by opening the screen.

The local path uses the same `GlassesWhisperBridge` and `GlassesCommandMatcher` as listening. It retains English, two CPU threads, greedy decoding, initial temperature zero and the installed whisper.cpp defaults (including temperature fallback). The server uses faster-whisper with CPU int8, beam size one, no prior-text context, fixed temperature zero and no extra VAD. Engine/decoding differences are included in results: same named models across engines are a practical deployment comparison, not an assertion of numerically identical inference.

One native WorkManager worker owns replay, with its own JNI context/job IDs and one serial lane. Listening is disarmed and unfinished captures discarded before replay; completed queued captures finish saving before replay, and staged captures remain recoverable. The worker waits for the listening inference worker to exit before allocating another model. Inference has a sixty-second abort timer; network requests and server subprocesses have separate bounds. Memory checks reject oversized local allocations before loading. Completed results are fsynced/atomically replaced one attempt at a time. Sign-out/account changes invalidate work and prevent stale result publication. The worker can run with the screen locked and continues in bounded work slices. Android may defer/cancel work under quota or resource pressure; completed results survive. WorkManager 2.10.5 handles Android foreground dataSync timeouts. Source: [Android foreground-service troubleshooting](https://developer.android.com/develop/background-work/services/fgs/troubleshooting).

On completion/pause, listening can rearm only while the app is foreground; otherwise open the app to resume it. A new microphone session is never promoted from the background by replay. Avoid running chat dictation alongside comparisons, since both consume phone resources.

Each sample has one **cold** attempt with a newly initialized model, followed by 1–5 **warm** attempts. Cold means a new model context, not an empty OS file cache or freshly booted phone. Model downloads/checksum verification, dataset import and unscored warm preparation are outside measured latency. After an interruption the runner initializes and runs an unscored replay before resuming a local warm measurement. Server warm attempts also perform an unscored request first and fail as `warm_context_lost` if the measured response did not reuse a loaded context. These preparations are exported separately. No synthetic warm-up is added to production listening.

## Measurements

New production capture JSONs keep legacy `transcriptionDurationMs` (inference only) and add `timing`: monotonic queue wait, staging, model initialization, inference, matching, `modelAlreadyLoaded`, model filename, and capture-completion-to-decision duration. First inference after loading remains fully included. Failed stages retain elapsed time; a decision duration is present only if a decision exists. Older captures cannot retroactively acquire missing timings.

Replay `totalMs` covers local model initialization plus inference and matching, or phone-observed server request duration plus matching. Server request time includes fresh-token acquisition, connection/write and response; `authMs`, `requestWriteMs` and `responseWaitMs` expose that breakdown. Request write is not proof that all bytes have physically arrived at the server. Server queue/download/load/inference/processing durations are separately reported. Do not sum independent server and phone clocks or call their difference pure network latency. Dataset reads, input validation/conversion and report persistence are outside the measured replay interval. Live endpointing (currently 1.5 seconds of silence), microphone transport, action dispatch and physical gate/photo completion are outside replay latency.

Accuracy groups successful attempts by candidate and cold/warm mode. Counts include correct decisions (including correct rejections), missed commands, wrong commands, false acceptance of rejection samples and failed attempts. Accuracy denominators exclude failed attempts; failure counts remain visible. Word error rate uses case/punctuation normalization and word-level edit distance; empty-reference insertions contribute errors, and all-empty-reference groups have undefined WER. Latency median/p95 use successful attempts and nearest-rank percentiles. Warm repetitions are repeated measurements, not additional independent examples. Per-sample rows/tags and failures allow independent analysis. App/device/engine versions, model revisions/hashes, scorer/matcher versions, network type and process PSS are exported for reproducibility. PSS is sampled after the attempt, not a peak-memory measurement.

## CPU server

The endpoint is opt-in via `GLASSES_SPEECH_EVAL_ENABLED=true` in the existing backend environment. `GLASSES_SPEECH_EVAL_THREADS=2` defaults to two CPU threads; `GLASSES_SPEECH_EVAL_CACHE=storage/whisper-models` is mounted persistently in Compose. Rebuild/recreate the orchestrator after installing the new dependency/configuration. Model weights persist in this cache; uploaded audio is bounded, in memory only, and released after processing. The current server hardware capacity still requires deployment inspection; host tests are not VM performance evidence.

All phone requests use the configured authenticated frontend proxy:

- `GET /mobile/glasses/speech-eval/models`: catalog and enabled state.
- `POST /mobile/glasses/speech-eval/prepare?model=small.en`: download the pinned model, without a sample; up to ten minutes.
- `POST /mobile/glasses/speech-eval/transcribe?model=small.en&cold=true`: raw WAV body, up to 180 seconds inference/process budget.

Supported CPU candidates: Base English, Small English, Medium English, Large v3 and Large v3 Turbo. One disposable subprocess/one model context serves requests serially; an occupied slot returns `server_busy` rather than growing a queue. Timeout/crash discards the subprocess. Idle model memory releases after sixty seconds. Reference labels never leave the phone. The existing command endpoint and production transcription mode are unchanged. The proxy/reverse proxy must permit these request durations; gateway timeout must be checked on the deployed route.

## Verification boundary

Focused JVM tests validate audio parsing, scoring and monotonic timing. Backend tests cover authentication, upload bounds, safe errors, fixed model selection and subprocess timeout cleanup. Kotlin compilation/manifest merging, TypeScript and lint checks do not establish physical Pixel accuracy, locked-phone lifecycle behavior, memory limits, battery impact or actual server/network latency. Those require an installed native build, server deployment and labeled recordings.
