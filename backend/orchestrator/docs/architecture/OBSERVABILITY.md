# Observability hooks

The orchestrator retains provider-neutral instrumentation hooks in
`observability/instrumentation.py`. They are inactive: they collect and export
no traces or metrics. Existing local logging and diagnostics remain independent.
A future provider must preserve privacy: never export prompts, outputs, tool
arguments or results, user or conversation identifiers, or credentials.
