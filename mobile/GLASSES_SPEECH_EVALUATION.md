# Glasses speech comparisons

Settings → Glasses → Listening → Compare recognition runs the same saved glasses audio through selectable local models and the existing Digital Brain server. It reports accuracy and latency without choosing a winner. Replay has no access to command dispatch: gates and camera are never operated.

## GPU speech endpoint

**On your GPU server → large-v3-turbo** adds the candidate `remote:large-v3-turbo`, independently of the CPU model/thread options. It sends the same WAVs through the authenticated Digital Brain frontend proxy to the backend, which makes a multipart OpenAI-compatible `/audio/transcriptions` request. Labels/tags and original filenames are never sent to the provider. All candidates use the same local command matcher and correctness scoring. Production listening continues using on-device Whisper.

The backend uses `LLM_BASE_URL` and `LLM_API_KEY` by default. Optional environment overrides in the orchestrator env file are:

```dotenv
GLASSES_SPEECH_EVAL_REMOTE_BASE_URL=https://your-speech-service/v1
GLASSES_SPEECH_EVAL_REMOTE_API_KEY=your-speech-key
```

Unset overrides inherit the corresponding LLM setting. An explicitly empty speech key sends no Authorization header. The base URL includes the API prefix (normally `/v1`); a bare host gets `/v1` appended. Custom base paths are retained. Model selection is fixed to `large-v3-turbo`; requests specify English, JSON responses and temperature zero. Backend credentials never enter the phone, model catalog, reports or speech logs. `GLASSES_SPEECH_EVAL_ENABLED=true` remains required. Deploy the updated backend and install a new native phone build before starting a new comparison.

GPU results use **First request** (`first`) and **Repeated requests** (`repeat`), with `coldStartControlled=false`. These are per-sample replay positions: the first request does not unload the remote model, and an unscored transcription precedes each repeated measurement. Preparation validates local configuration only; it does not verify the remote credentials, load a remote model or claim readiness. The runner does not assume that the provider keeps the model loaded. GPU cold-start time and pure inference time are unavailable through this API.

`totalMs`/`roundTripMs` measure phone-to-result time through Digital Brain; `upstreamRequestMs` measures the backend's complete provider request, including transfer, queueing and inference; `serverTotalMs` includes backend slot waiting as well. Remote results intentionally omit `inferenceMs`, CPU threads, model-load timings and `modelAlreadyLoaded`. JSON and CSV retain provider, model, timing fields and safe error metadata. Reports keep first/repeat results separate from CPU/local cold/warm results.

GPU requests use `provider=remote` on the existing routes. For a command-line smoke test, use the frontend URL, a Google ID token and a compatible wake WAV:

```bash
curl --fail-with-body --max-time 200 \
  'https://YOUR_BRAIN_SERVER/api/mobile/glasses/speech-eval/transcribe?provider=remote&model=large-v3-turbo' \
  -H "Authorization: Bearer $BRAIN_ID_TOKEN" \
  -H 'Content-Type: audio/wav' \
  --data-binary '@/absolute/path/to/sample.wav' \
  -w '\nHTTP %{http_code} | Total: %{time_total}s\n'
```

The GPU adapter has a separate serial slot from CPU evaluation so an in-flight CPU download does not block the remote candidate. It retains the same audio size/format limits, bounds response JSON to 32 KiB, uses 10-second connect/60-second read timeouts with a 120-second elapsed check while reading chunks, and never follows redirects with provider credentials. Failures retain bounded HTTP status/stage rather than provider error bodies. Neither URL nor key may be supplied by a client request. Preparation and transcription responses must identify the remote provider/model/engine; older backends that ignore `provider` cannot be scored as GPU results.

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

## Failure diagnostics

The backend emits `[speech_eval]` events through the orchestrator parent logger, so they appear in System logs and container stderr. Events include request start/completion/failure, allow-listed model and operation, download/load/inference stage, durations, warm/cold state, inference process CPU time, and initial numeric CPU affinity/quota/thread configuration. Third-party child logging stays suppressed; sanitized stage messages cross IPC to the parent instead. Audio, transcripts, reference labels, file paths, URLs, tokens and raw exception messages are excluded.

Failures distinguish provider HTTP errors (with numeric upstream status), unavailable downloads, timeouts, disk-full/cache-permission failures, insufficient memory, model-load/inference failure and child exit codes. A negative child exit code identifies a signal, not its underlying cause; OS/container evidence is still required to establish an OOM kill. Requests rejected by authentication or a reverse proxy before reaching this endpoint require phone HTTP status and/or gateway logs.

`server_busy` means the single evaluation slot could not be acquired within one second; it is not a host CPU/RAM measurement or a model-size restriction. Busy logs include the active model, operation, stage, thread count and elapsed milliseconds. Preparation can hold the slot for up to 600 seconds of child waiting and transcription for up to 180 seconds (plus bounded startup/cleanup); client disconnect or Pause does not cancel the backend thread. A gateway/client timeout can therefore be followed by busy responses for later candidates. Inspect preceding `[speech_eval] started`, `stage`, `completed` and `failed` events to identify the original operation. Let that operation finish or hit its deadline before starting a new comparison; do not run parallel model downloads/inferences to bypass the slot. Restarting the orchestrator clears an in-flight job but interrupts all other orchestrator work too.

New phone exports retain `failureStage`, safe `error`, `httpStatus`, `upstreamHttpStatus`, and `preparationDurationMs` where available. Preparation duration belongs to a shared preparation attempt, not to each subsequent skipped replay; do not sum its repeated value across rows. A failed preparation still marks dependent planned attempts failed without transcribing them. Old exports cannot recover discarded causes. CPU time is process-wide CPU consumed during inference (potentially across threads), distinct from elapsed wall time and from VM CPU utilization.

## CPU thread comparison

Select server models, then select 2, 4 and/or 8 under **Server CPU threads**. Start a new comparison: each server model/thread pair is a separate candidate, with its own cold/warm results, correctness and latency. Local choices are unchanged. The worker calls `prepare` and `transcribe` with `threads=2`, `threads=4` or `threads=8`; the backend restarts its isolated process when the thread configuration changes. Model/decoding settings remain fixed. The native runner rejects responses whose reported CPU thread count differs from the requested setting, including older servers that silently ignore the query parameter.

Candidate keys use `server:small.en@threads=4`; legacy `server:small.en` still uses `GLASSES_SPEECH_EVAL_THREADS`. The configured default remains two threads; benchmark overrides do not change production settings. Expanded model/thread combinations count toward the 10,000-attempt limit. Reports group each configuration independently; CPU counts are also explicit in JSON/CSV. The model catalog endpoint exposes supported thread options and safe numeric CPU capacity information. Compare within the VM's allocation and record competing load; more threads need not reduce single-command latency.

The current transport remains one authenticated HTTP POST per completed WAV through the frontend proxy. Uploads are not sent during live capture. A future wake-scoped streaming test needs separate connection/authentication, first-chunk, capture-end and final-result timings; a WebSocket transport alone does not turn full-utterance Whisper inference into incremental recognition. No live audio-upload behavior is changed by the CPU experiment.

### Checking the deployed backend

An older backend accepts unknown `threads` query parameters but omits `cpuThreads` from successful preparation responses. The phone rejects those responses before sending audio, so container logs can show only `prepare ... 200 OK` while every dependent row fails. New phone builds report `server_update_required` for missing thread confirmation, retain HTTP 200 and stage `request`, and expose safe failure counts in summaries. Existing phone builds report `cpu_configuration_mismatch` for this case. A present but different thread count remains `cpu_configuration_mismatch`; never score it as the requested configuration.

On the server, inspect the actual running image without loading any model:

```bash
docker exec mem-orchestrator python -c 'import inspect, glasses_speech_eval as s; from pathlib import Path; print("Thread-aware engine:", "threads" in inspect.signature(s.engine.request).parameters); print("Speech lifecycle logs:", "[speech_eval] started" in inspect.getsource(s.SpeechEvalEngine.request)); print("Thread-aware routes:", "cpuThreadOptions" in (Path(s.__file__).parent / "routes/glasses_speech_eval.py").read_text())'
```

All three values should be `True`. False values mean the running container lacks this update; deploy an image containing the updated backend and recreate the orchestrator container. Restarting the old image does not install the new source. A new phone run is needed because completed failure rows are retained on Resume. During that run, `docker logs --since 5m -f mem-orchestrator 2>&1 | rg 'speech_eval|speech-eval'` should show parent lifecycle events and transcription requests. Preparation only downloads model files; HTTP 200 there does not establish that a model has loaded or transcribed successfully.
