# Request budgets and working-context compaction

`llm_context.py` owns complete-request measurement and the combined input/output
budget. `llm_helpers.py` checks assembled requests before synchronous, synchronous
streaming and asynchronous streaming chat-completion calls. This runs again for
every agent round, after tool definitions, response schemas and results are present.
Small warm-up requests remain fixed-size. No database migration is needed.

Startup model warm-up runs once as a cancellable async background task after job
workers start; it never gates ASGI readiness. Native Ollama requests use empty
history to load the model without generating a response, following the
[Ollama API contract](https://github.com/ollama/ollama/blob/main/docs/api.md#load-a-model-1).
When a proxy lacks the native endpoint, the compatible fallback requests only one
token with reasoning disabled. `LLM_WARMUP_TIMEOUT` (default 180 seconds) is one
deadline per model, including connection setup and both attempts, independent of
normal/high-reasoning timeouts. Shutdown cancels in-flight HTTP I/O before stopping
workers and telemetry. Warm-up failures log only the exception type.

The default window is 65,536 tokens, with a 4,096-token safety margin. Calls without
an explicit output limit reserve 8,192 generated tokens, or 12,000 for medium/high
reasoning. Explicit output limits and provider output ceilings are respected.
Reasoning uses the generated-token reserve. Providers' lower input/context limits
are remembered per model and endpoint for one hour, including streaming failures.
Intelligence uses these same budget/measurement functions with its phase-specific
output reserve and evidence-splitting policy.

Text uses a local `cl100k_base` BPE count of the serialized request with 20% headroom
and per-message overhead, including JSON escaping, instructions, tool schemas and
response schemas. This is a model-independent estimate, not the selected provider's
exact tokenizer count. Counting every byte as a token previously rejected normal
initial chat prompts before any history existed. Public vocabulary data is cached
in the Docker image at build time, so requests need no tokenizer download. If the
tokenizer is unavailable, an explicit warning accompanies the UTF-8 upper-bound
fallback. Images reserve 8,192 tokens each instead of
counting base64 transport bytes as text; provider-specific vision tokenization
can differ. Images are retained unchanged. Optional `LLM_CONTEXT_WINDOW_*_TOKENS`
profile overrides and `LLM_IMAGE_INPUT_TOKENS` support different providers.

## Conversational requests

Above 80% of the available input budget, the preflight attempts LLM summaries of
older dialogue and successful registered read-only tool results. The summary model
is the model selected for that request, with reasoning disabled, no tools and a
512-token output limit. Input fragments are bounded before each summary call;
at most sixteen summary calls are permitted per preflight. This cannot recursively
invoke another compactor. Summary requests and outputs use the same local
diagnostic controls as other source-processing requests.

The controller owns a separate compaction policy/cache for each run, scoped with
a `ContextVar`. Cache keys include the model, budget, current-request focus and
original content; only derived summaries are retained. Later rounds can reuse
them. Different runs do not share a summary cache. Original message lists and
controller state are never rewritten. Stored conversation history remains intact.

The following remain verbatim:

- System/developer instructions and tool parameter contracts.
- The original current user request and subsequent controller/user follow-ups.
- Assistant tool calls, arguments and tool-result IDs, preserving their pairing.
- Unknown/mutating tool results, failures and validation feedback.
- Multimodal messages.

Before LLM summarization, verbose tool descriptions can be shortened to 240
characters each. Names, parameter types, required fields, enums, constants and
defaults remain unchanged. This can reduce the first request even without history;
it does not summarize instructions or silently discard a required contract.

Read-only result summaries carry a controller-derived reference index with exact
IDs, source references, URLs, titles, dates and result state. The model does not
generate those values. Summaries explicitly identify themselves as derived,
untrusted working context that can omit details. Retrieve original sources before
exact quotations or decisions that require omitted details. A summary does not
establish a fact or prove a mutation succeeded.

## Source-processing requests

Single-prompt generation and structured extraction calls still pass through the
shared budget check. They do not automatically replace their source text with a
summary. The caller owns evidence splitting and any synthesis appropriate to its
task. Shared knowledge/intelligence extraction already splits primary sources and
reconciles small candidate groups; it never learns from compactor output.

After compaction, the complete request is measured again. Empty, failed or
oversized summary outputs cannot replace any source fragment. If the original
still fits, it can be sent unchanged. Otherwise a typed `context_length_exceeded`
error returns to the caller before an oversized provider request is made. An
oversized protected current request, instruction block, mutation receipt or schema
therefore needs caller-specific splitting or reduction; the preflight does not
silently discard it. Budget diagnostics include estimator, estimated tokens, wire
bytes, per-message/tool costs, compaction decisions and the protected context that
prevented fitting. Detailed local logs include source/model content and validation
feedback at the user's request. `LLM_DIAGNOSTICS_ENABLED` defaults to true; set it
to false to retain only ordinary bounded status logs. Each diagnostic entry is
capped by `LLM_DIAGNOSTIC_MAX_CHARS` (60,000 by default), with explicit truncation.
Configured credentials and structured secret fields are redacted. These records
go to the local authenticated system-log stream, never to external telemetry.

Defaults work without editing `.env` for a 64k deployment. Optional settings are
documented in `backend/env.template`. Live summary quality, latency and provider
token accounting require separate runtime evaluation.
