# Glasses recognition tests

Settings → Glasses → Listening → Compare recognition evaluates saved WAVs through the same GPU transcription path as live glasses commands. Phone and CPU-server model downloads, inference contexts, thread choices and model catalogs have been removed. Chat dictation uses the same GPU provider with its own authenticated foreground upload route. Tests never dispatch gates or camera actions.

## Configuration and request path

The backend module `speech_transcription.py` is the single provider implementation. Live captures POST a bounded WAV to `/mobile/glasses/transcribe`; replay uses `/mobile/glasses/speech-eval/prepare` and `/transcribe`. Clients use the configured frontend proxy base (`/api/mobile/...` externally). Both routes require the signed-in user's Google ID token. The backend makes a multipart request to the configured `/audio/transcriptions` endpoint with the selected allow-listed model. Whisper requests specify English, JSON responses and temperature zero. Parakeet requests specify its exact model ID and JSON responses, leaving language/decoder settings to the provider rather than forwarding Whisper-specific parameters. Actual model and decoding metadata are retained in each result.

The provider defaults to `LLM_BASE_URL` and `LLM_API_KEY`. Optional overrides:

```dotenv
SPEECH_BASE_URL=https://speech.example/v1
SPEECH_API_KEY=your-speech-key
```

An unset key inherits the main key; an explicitly empty override sends no bearer header. Older `GLASSES_TRANSCRIPTION_*` and `GLASSES_SPEECH_EVAL_REMOTE_*` overrides remain compatible during upgrades. A bare host receives the `/v1` prefix; custom configured API base paths are retained. Credentials are exclusively server-side. Live speech does not depend on `GLASSES_SPEECH_EVAL_ENABLED`; this opt-in gates replay experiments only.

The adapter bounds WAVs to mono PCM16/16 kHz, thirty seconds and 964096 bytes, and provider JSON to 32 KiB. There are two bounded concurrent requests, one-second slot acquisition, five-second connect/fifteen-second read timeouts, and a twenty-second elapsed check between response chunks. Redirects and transcription retries are disabled. Logs contain only fixed operation/model/provider labels, safe status/error codes and durations. Audio, transcripts, labels, filenames, URLs, keys and provider error bodies are excluded.

## Dataset and runs

Place a manually labeled `dataset.json` and unique, flat WAV filenames in one folder. Use [examples/glasses-speech-dataset.json](examples/glasses-speech-dataset.json) as the schema reference. Each reference transcript must describe the recording rather than a model output. `expectedCommand` is `open_front_gate`, `toggle_car_gate`, `take_picture`, or JSON `null` for rejection. Optional `tags` remain in private reports only. Listen to recordings before resolving conflicting labels; identical text cannot deterministically produce different intents without extra trusted context.

Choose the folder and repetitions, then Start new comparison. Select either or both GPU models: `remote:large-v3-turbo` (served as `whisper-large-v3-turbo`) and `remote:parakeet-tdt-0.6b-v3`. Both use the same configured URL/key, recordings, matcher and scoring. Candidates have separate results; production chat/glasses transcription remains pinned to Whisper Turbo. The existing Whisper candidate ID remains stable for saved runs. Retired phone/CPU results remain exportable, but their unfinished runs cannot resume. No old engine is silently substituted. The runner copies and hashes selected recordings into account-scoped private storage; originals are never modified. There are 1–512 samples, 1–5 repeated requests and at most 10,000 measured attempts. Starting a new run replaces the previous private run, so export first. Phone listening pauses while a test runs and resumes only under its existing foreground/eligibility rules.

Each recording has a `first` request followed by `repeat` requests. An unscored provider request precedes each repeated measurement. These positions do not force remote unload or prove model readiness; `coldStartControlled=false`. Preparation checks local configuration only. Pure GPU inference and remote load time are unavailable through this API.

## Reports and matcher comparisons

Exports go to `<Digital Brain>/Exports/Speech tests` as JSON and CSV. Reports include transcripts, expected and selected commands, word errors, missed/wrong commands, false acceptances, failures, median and p95. Per-row `matcherVersion` identifies the actual matcher used after upgrades; start a new run for a consistent summary.

One transcript is evaluated by three pure matcher paths without additional ASR requests:

- `literalCommand` / `literalCorrect`: canonical full-utterance phrases plus the existing one-edit spelling allowance.
- `grammarCommand` / `grammarCorrect`: the same grammar with one known target/noun word-boundary repair.
- `command` / `correct`: grammar plus the bounded phonetic/transposition fallback used by live listening.

`matchingStage`, `matchingCost`, `matchingMargin` and `rejectionReason` explain selection/abstention. Costs are edit budgets, not probabilities. Match costs may be absent when no grammar path is eligible. Margin is absent when only one distinct intent survives; multiple aliases of one intent never compete as separate intents. These details remain in private captures/reports only.

`totalMs` / `roundTripMs` include phone authentication, upload, backend/provider request and response; `totalMs` adds primary matching time. `upstreamRequestMs` includes the backend-to-provider network, provider queue and inference. `serverTotalMs` also includes backend slot waiting. `authMs`, `requestWriteMs` and `responseWaitMs` remain available in JSON. Unscored preparation/warm-up time is recorded separately. Failure rows retain safe reason, HTTP status and upstream status; shared preparation failure duration must not be summed across dependent rows.

## Matching design and evidence

Matcher v3 normalizes input and grammar consistently, requires an anchored wake and one complete command, and preserves protected actions and targets. Only known fused target/noun tokens are split. Non-critical words have a bounded spelling cost, adjacent-transposition cost, and vowel-preserving phonetic rules. Negations, questions, deferred instructions, repeated wakes, extra clauses and unknown targets are rejected rather than deleted. A maximum cost and separation from the next distinct intent enforce abstention. The initial thresholds preserve the existing one-edit budget; they are not calibrated probabilities or proof of field safety.

The design follows the sentence-template separation used by [Home Assistant](https://contributors.home-assistant.io/architecture/platform/voice/), informed by [context-dependent phonetic distance research](https://www.microsoft.com/en-us/research/publication/context-dependent-phonetic-string-edit-distance-for-automatic-speech-recognition/). It avoids substring/token-set acceptance: [RapidFuzz documents](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html) subset matches with perfect scores despite extra words. This is why generic closest-sentence or embedding similarity is not the controller.

Keep original recordings together when splitting calibration and validation data; repeated transcriptions of one recording are not independent validation cases. Use recordings from another session for the final check, with confusers and accepted-but-wrong actions reported explicitly. Do not edit reference labels to improve scores. No new audio collection is started by this feature.

Vocabulary comparisons are replay-only. `remote:large-v3-turbo@commands-v1` sends the fixed `COMMAND_VOCABULARY` as multipart `prompt` to the same Whisper model. It is shared across every sample and never built from expected labels. Verify that the deployed serving implementation forwards and uses `prompt`; an HTTP success alone does not prove it was applied. If unsupported, the variant fails without retrying as baseline. Live Listening and chat dictation continue without hints.

`remote:parakeet-tdt-0.6b-v3@commands-v1` uses an explicitly configured boosted upstream model alias, set with `SPEECH_EVAL_PARAKEET_BOOSTED_MODEL` on the orchestrator. This must route through LiteLLM to a serving model with NeMo TDT phrase boosting configured using the same canonical vocabulary. The setting only selects that deployment; it does not enable boosting in NeMo or install a dictionary. Until it is configured, prepare fails with `vocabulary_not_configured`. A missing alias or one equal to baseline is rejected; never silently score baseline as boosted. No Whisper-only parameters are sent to Parakeet. The exact upstream implementation and boost strength need to be recorded when configuring the alias; do not change them within a run.

CSV and JSON results include `vocabularyProfile` (`none` / `commands-v1`), `vocabularyMethod` (`none` / `prompt` / `server_alias`) and actual `upstreamModel`; candidate IDs keep baseline and hinted rows distinct. Existing baseline runs remain resumable. New runs default to both baselines plus Whisper vocabulary; enable the Parakeet vocabulary choice after configuring its alias. Use the same dataset, matcher and repeats, and compare false accepts as well as command recognition and latency. Version the profile when changing vocabulary.

A constrained decoder with an explicit unknown path remains a future experiment ([Vosk example](https://github.com/alphacep/vosk-api/blob/master/python/example/test_words.py), [Speech-to-Phrase](https://www.home-assistant.io/blog/2025/02/13/voice-chapter-9-speech-to-phrase/)).

## Deployment and verification

Deploy the updated backend and install a native phone build together. Live Listening is reset off once because previous opt-in covered local-only recognition. Enable it after reading the new upload description; no microphone capture is promoted at boot/headless launch. Chat dictation uses the same GPU provider; manual glasses recordings remain local and independent. Remove the retired Whisper cache mount from server Compose configuration; existing cached files can be removed manually once the old deployment is no longer running.

Focused tests cover format/authentication/bounds, provider configuration/errors, grammar/protection/rejection, replay scoring and native compilation. Saved-transcript replay does not establish live glasses capture, Bluetooth routing, locked-screen network behavior, provider availability, battery impact or a calibrated real-world false-acceptance rate. Those require the deployed endpoint and an installed device build. No real gates are operated by tests.

Parakeet comparison requires this updated backend and native app build. Start a new run to compare both models. Existing single-Whisper GPU runs can still resume when their matcher version matches; retired phone/CPU engines remain unavailable. Provider compatibility must be verified on the deployed service; HTTP 400/404 and other provider failures retain safe upstream status in exports.


## Matcher v4 and request-overhead comparison

Start a new run after updating the native app, backend and frontend. Runs from earlier matcher versions remain export-only so a run cannot mix matching policies. Production vocabulary settings and `commands-v1` vocabulary are unchanged.

Each new row additionally scores the frozen v3 behavior on the same ASR transcript in `previousMatcherVersion`, `previousMatcherCommand` and `previousMatcherCorrect`; this does not make a second provider request. `matchingRepairs` contains only bounded private matcher edit descriptions. Literal and grammar comparisons retain their v3 behavior. v4 adds take pick → take pic, cartgate → car gate and tuggle/tugle/duggle → toggle with the existing total cost and margin limits. Anchored wake verification, full-utterance matching, protected actions/targets, negations and multi-command rejection remain enforced.

`authSource` distinguishes `cached` from `silent_sign_in` on the native speech client. Reuse is limited to the current account/client with more than 120 seconds before JWT expiry; a 401 invalidates reuse without retrying the recording. Backend verification always checks each token and allow-list; only Google's public certificate responses are cached in memory using their HTTP cache headers. Verification runs outside the async event loop. A bounded pool of two exclusive Requests sessions reuses GPU HTTP/TLS connections, with no automatic retries and with provider cookies cleared between requests.

Speech-only numeric `Server-Timing` headers add CSV/JSON fields: `backendAuthMs` (verification and allow-list), `backendUploadMs` (backend body read), `backendRouteMs` (backend dependency/route execution), `proxyAuthMs` (frontend authorization selection), `proxyBackendMs` (frontend fetch until backend headers), and `proxyTotalMs` (frontend handling until headers). Parent stages overlap their child stages: do not sum these columns. `authMs` remains phone token acquisition; `upstreamRequestMs` remains backend-to-GPU HTTP including transfer, queueing and inference; `totalMs` remains phone round trip plus matching and excludes the separate `warmupMs`. Response transfer back to the phone and network transit are not pure inference. Only fixed names and bounded numeric durations are accepted from the header; no auth tokens or identities enter timing exports. Speech responses are marked no-store.

Run frontend proxy tests with `node frontend/web/scripts/test-mobile-speech-proxy.mjs`. Backend local HTTP tests in `tests/test_speech_latency.py` prove cache reuse/expiry, continued JWT validation and actual TCP connection reuse without production calls. Native policy tests prove expiry/account/rejection handling and numeric timing parsing. Real latency improvements and device Google sign-in behavior must be measured on the next installed-device benchmark; saved-transcript replay measures matcher changes only.
