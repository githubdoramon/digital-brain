# Decision model benchmark

Compare configurable HTTP models on five bounded semantic tasks, without starting
the application, accessing PostgreSQL, or changing production LLM configuration.
No UI or image generation is involved.

The bundled suite contains **70 synthetic, manually labelled cases** in English
and Portuguese. All names, venues and organizations are invented.

| Task | What is scored | Production reference |
| --- | --- | --- |
| `event_matching` | New / existing / neutral event-update intent | `commands/handlers/event.py::_classify_event_match_reference` |
| `event_fields` | Exact set of seven event fields requested by a correction | `commands/handlers/event.py::_infer_follow_up_target_fields` |
| `briefing_quality` | Pass/fail prose quality | `agents/daily_briefing/validators.py::_validate_llm_judge` |
| `routing` | Intent, pre-resolve contacts, extract durable user facts | `agent/router.py::_llm_classify` |
| `contact_disambiguation` | Choose a supplied candidate / new person / uncertain | `agents/contacts/resolver.py::_llm_disambiguate_contact` |

This measures **semantic decision replacement**, not full production execution.
It uses common explicit decision rubrics for every model, without generated
explanations, contact mention extraction, event-candidate retrieval, deterministic
shortcuts, briefing structural/source validation, DB writes, or agent tool loops.
Event matching here is the existing new/update/neutral classifier, not a benchmark
of the full similar-event search/merge algorithm. Production prompts are not
imported; `tasks.py` records the intentionally simplified contract. Update the
rubrics/labels if production behavior changes.

## Run

From the repository root:

```bash
cp backend/orchestrator/decision_benchmark/models.example.json \
   backend/orchestrator/decision_benchmark/models.local.json

# Edit model names, full endpoint URLs, protocol, and optional authentication.
# models.local.json and default output directories are Git-ignored.
backend/orchestrator/.venv/bin/python \
  backend/orchestrator/scripts/benchmark_decisions.py \
  --config backend/orchestrator/decision_benchmark/models.local.json \
  --dry-run

backend/orchestrator/.venv/bin/python \
  backend/orchestrator/scripts/benchmark_decisions.py \
  --config backend/orchestrator/decision_benchmark/models.local.json \
  --models local-chat jev --repetitions 3 --warmups 1
```

Set any environment variables named in your selected model configurations before
the real run. The script does not read `.env` or change process LLM settings.
Unused models do not require credentials. Example placeholder models must be
edited or excluded with `--models`.

Other useful options:

```bash
# No config, network or credentials required.
backend/orchestrator/.venv/bin/python backend/orchestrator/scripts/benchmark_decisions.py --list-cases

# Any model IDs can share a URL while selecting different model names/options.
backend/orchestrator/.venv/bin/python backend/orchestrator/scripts/benchmark_decisions.py \
  --config backend/orchestrator/decision_benchmark/models.local.json \
  --models local-chat --tasks event_matching event_fields --languages en \
  --repetitions 5 --warmups 1 --seed 17 --fail-under 95 \
  --output-dir /tmp/decision-benchmark-run
```

The output directory must not exist. Default:
`backend/orchestrator/storage/decision-benchmarks/<UTC timestamp>/`.

## Configure providers

`models.example.json` documents the file shape. Each entry has a unique `id`,
`model`, `protocol` and **full request URL**, including its path. There is no
implicit `/v1` or endpoint suffix. Protocols:

- `openai`: chat completions; reads `choices[0].message.content` as a JSON object.
  `structured_output` can be `json_schema` (default), `json_object`, or `none` for
  servers without structured-output support. Every mode still requires exactly
  the declared enum answers. One surrounding JSON Markdown fence is tolerated;
  arbitrary prose, missing/extra answer keys, refusals and truncated completions fail.
- `jev`: native [TypeSafe System One API](https://docs.typesafe.ai/api), taking
  `state` and `questions`, returning Choice answers, confidence and probabilities.
- `this_that`: the bridge below, using the same Choice envelope. This is our local
  bridge contract, not an assertion about FLock's hosted decision endpoint.

`api_key_env` reads a secret from the named environment variable. It defaults to
`Authorization: Bearer <value>`. Change `auth_header` and `auth_prefix` for other
schemes. For FLock's documented hosted chat authentication, use
`"auth_header": "x-litellm-api-key", "auth_prefix": ""` with `protocol: openai`.
See [FLock's API docs](https://docs.flock.io/flock-products/api-platform/api-endpoint);
confirm your particular hosted decision model supports this contract.

`headers_env` maps additional header names to environment variable names, for
example `{"X-API-Key": "BENCH_OTHER_KEY"}`. Credentials are optional for local
servers. Endpoint URLs may not contain embedded credentials or query strings.
Keep secrets in the environment, not model IDs or model names.

For OpenAI-compatible endpoints, `request_options` passes supported provider
settings such as `temperature`, `max_tokens`, `reasoning_effort`, `reasoning`, or
`keep_alive`. No sampling/reasoning setting is silently imposed. Configure equal
budgets or separate model IDs for the variants you want to compare. Contract
fields (`messages`, `model`, `stream`, `response_format`, etc.) cannot be overridden.
No retries, fallbacks, redirects, response repair models or LLM judges run.

### THIS/THAT local bridge

Install the optional upstream runtime on the inference machine, in a separate
environment if necessary; it is not a production backend dependency:

```bash
git clone https://github.com/FLock-io/this-that-model.git /tmp/this-that-model
backend/orchestrator/.venv/bin/pip install -e /tmp/this-that-model
backend/orchestrator/.venv/bin/python backend/orchestrator/scripts/serve_this_that_benchmark.py \
  --model flock-io/this-that-model-1.2 --device auto
```

Run the benchmark with `--models this-that`, or configure a remote bridge URL.
The bridge loads the named model once, serializes inference, passes full option
descriptions to upstream `Question`, and maps returned indices back to stable
answer keys. It synchronizes the backend around inference. It checks state token
length and overrides upstream's truncating default so state is never silently
shortened. Over-budget state returns 413. Model loading happens before serving;
its time is not included in request latency. `--api-key-env` optionally requires
Bearer authentication. Bind address defaults to loopback.

The bridge follows the upstream [TypedDecider](https://github.com/FLock-io/this-that-model)
API and pins an explicit checkpoint in the example. Record the upstream commit,
runtime versions and hardware with your experimental notes when comparing runs.

## Results and interpretation

- `attempts.jsonl`: flushed after every request, including warm-ups; case/task IDs,
  labels, normalized predictions, per-question correctness, probability/confidence
  values where available, safe token counts, duration, and bounded error codes.
- `report.json`: per-model overall, task and language summaries, confusion counts,
  first-request/warm-up statistics, confidence threshold slices, timestamps, suite,
  configuration and decision-contract hashes.
- `summary.csv` and `summary.txt`: compact model/task/language comparisons.

**Exact accuracy** requires every decision in a case to match its manual label.
Failures count as incorrect. `valid_answer_accuracy` excludes request/format
errors; `decision_accuracy` scores individual choices (including each event-field
flag). Use exact accuracy as the primary metric: many unchanged fields can make
per-decision accuracy look good. Repetitions measure latency/consistency and do
not increase the number of independent labelled examples.

Latency uses `perf_counter` around payload assembly, the complete non-streaming
HTTP response, parsing and answer validation. Reports separate all measured
requests, valid predictions, correct predictions, warm-ups, and the first observed
request per model. Console/CSV percentiles use **valid predictions**, including
wrong answers. HTTP failures and timeouts remain visible separately. It is not
time-to-first-token or whole user-flow latency. Requests are sequential with seeded
case shuffling and rotating model order. Warm-ups are excluded from accuracy and
measured latency. The first request is **not proof of a cold model**: server caches,
model eviction, network locality, provider load and hardware affect results.

Confidence slices report accepted coverage and measured accuracy at 0.5/0.8/0.9,
using the lowest confidence among a case's questions. OpenAI chat outputs do not
invent a self-reported confidence and have no eligible confidence samples. Jev's
distribution-based confidence and THIS/THAT's top-option probability have different
meanings; equal numeric thresholds are not equivalent confidence levels.

No URLs, headers, credential values, raw exception/HTTP bodies, prompts, input
state or raw model completions are written to reports. Custom cases are sent to
the configured providers. Reports do retain case IDs and expected/predicted enum
answers; choose non-sensitive IDs/labels for private suites.

Exit status: `0` completed (wrong predictions are recorded), `1` any measured
request/format error or a model below `--fail-under`, `2` configuration/file error,
`130` interrupted. Interruptions preserve finished attempts and partial summaries.

Extend `cases.json` with reviewed ground truth, or use `--cases your-suite.json`.
For `event_fields`, use `"expected": {"fields": ["where", "when"]}`; other tasks
use exact question IDs and enum keys shown by `--list-cases`. All expected keys and
labels are validated before requests. Ground truth is never included in prompts.
This curated suite is an initial regression comparison, not a production accuracy
estimate; keep a separate unseen holdout before selecting production thresholds.
