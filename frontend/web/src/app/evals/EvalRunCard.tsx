"use client";

import { EvalRunResult } from "./types";

type EvalRunCardProps = {
  run: EvalRunResult;
  runKey: string;
};

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatDuration(value: number): string {
  return `${Math.round(value)} ms`;
}

export function EvalRunCard({ run, runKey }: EvalRunCardProps) {
  return (
    <article
      style={{
        border: "1px solid rgba(16, 35, 61, 0.12)",
        borderRadius: 18,
        padding: 20,
        background: "linear-gradient(180deg, #ffffff 0%, #f7f9fc 100%)",
        boxShadow: "0 14px 32px rgba(16, 35, 61, 0.08)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: "0.08em", color: "#6b7280", marginBottom: 6 }}>
            {run.flow.label}
          </div>
          <h2 style={{ margin: 0, fontSize: "1.2rem", color: "#10233d" }}>
            {run.llm_model?.trim() || "default model"}
          </h2>
          <p style={{ margin: "6px 0 0", color: "#526070", maxWidth: 720 }}>{run.flow.description}</p>
        </div>
        <div style={{ minWidth: 220, display: "grid", gap: 8 }}>
          <div style={{ fontSize: "0.95rem", color: "#10233d", fontWeight: 600 }}>
            Pass rate {formatPercent(run.summary.pass_rate)}
          </div>
          <div style={{ color: "#526070", fontSize: "0.9rem" }}>
            {run.summary.passed_attempts}/{run.summary.measured_attempts} passing measured attempts across {run.repetitions} repetitions
          </div>
          <div style={{ color: "#526070", fontSize: "0.9rem" }}>
            Avg {formatDuration(run.summary.avg_duration_ms)} - Total {formatDuration(run.summary.total_duration_ms)}
          </div>
          {typeof run.timeout_seconds === "number" ? (
            <div style={{ color: "#526070", fontSize: "0.9rem" }}>
              Eval timeout {run.timeout_seconds}s per LLM call
            </div>
          ) : null}
          {run.keep_alive != null ? (
            <div style={{ color: "#526070", fontSize: "0.9rem" }}>
              Keep-alive {String(run.keep_alive)} for requested model
            </div>
          ) : null}
          {run.request_options ? (
            <div style={{ color: "#526070", fontSize: "0.9rem" }}>
              Payload tweaks: stream {run.request_options.stream ? "on" : "off"}
              {run.request_options.temperature != null ? ` - temp ${run.request_options.temperature}` : ""}
              {run.request_options.max_tokens != null ? ` - max ${run.request_options.max_tokens}` : ""}
              {run.request_options.reasoning_effort ? ` - reasoning ${run.request_options.reasoning_effort}` : ""}
            </div>
          ) : null}
          <div style={{ color: "#526070", fontSize: "0.9rem" }}>
            {run.discard_first_attempt
              ? `Discarded first attempt per case (${run.summary.discarded_attempts} total)`
              : "All attempts counted"}
          </div>
          {run.warmup?.attempted ? (
            <div style={{ color: "#526070", fontSize: "0.9rem" }}>
              Warm-up {run.warmup.performed ? "completed" : "attempted"}
              {typeof run.warmup.duration_ms === "number" ? ` in ${formatDuration(run.warmup.duration_ms)}` : ""}
            </div>
          ) : null}
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        {run.cases.map((caseResult) => (
          <details
            key={`${runKey}-${caseResult.case_id}`}
            style={{
              border: "1px solid rgba(16, 35, 61, 0.08)",
              borderRadius: 14,
              background: "#fff",
              padding: 14,
            }}
          >
            <summary style={{ cursor: "pointer", listStyle: "none" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                <div>
                  <div style={{ fontWeight: 700, color: "#10233d" }}>{caseResult.title}</div>
                  {caseResult.description ? (
                    <div style={{ fontSize: "0.88rem", color: "#6b7280", marginTop: 4 }}>{caseResult.description}</div>
                  ) : null}
                </div>
                <div style={{ display: "flex", gap: 14, flexWrap: "wrap", color: "#526070", fontSize: "0.9rem" }}>
                  <span>{formatPercent(caseResult.metrics.pass_rate)} pass</span>
                  <span>{formatDuration(caseResult.metrics.avg_duration_ms)} avg</span>
                  <span>{caseResult.metrics.discarded_attempts} discarded</span>
                  <span>{caseResult.metrics.variant_count} variants</span>
                </div>
              </div>
            </summary>

            <div style={{ marginTop: 14, display: "grid", gap: 12 }}>
              <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
                <div style={{ background: "#f7f9fc", borderRadius: 12, padding: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 8, color: "#10233d" }}>Input</div>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "0.82rem", color: "#334155" }}>
                    {JSON.stringify(caseResult.input, null, 2)}
                  </pre>
                </div>
                <div style={{ background: "#f7f9fc", borderRadius: 12, padding: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 8, color: "#10233d" }}>Expected</div>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "0.82rem", color: "#334155" }}>
                    {JSON.stringify(caseResult.expected, null, 2)}
                  </pre>
                </div>
                <div style={{ background: "#f7f9fc", borderRadius: 12, padding: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 8, color: "#10233d" }}>Effective payload</div>
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "0.82rem", color: "#334155" }}>
                    {JSON.stringify(
                      {
                        request_options: run.request_options ?? {},
                        case_json_schema: caseResult.response_json_schema ?? null,
                      },
                      null,
                      2
                    )}
                  </pre>
                </div>
              </div>

              <div style={{ display: "grid", gap: 10 }}>
                {caseResult.attempts.map((attempt) => (
                  <div
                    key={`${runKey}-${caseResult.case_id}-attempt-${attempt.attempt}`}
                    style={{
                      border: `1px solid ${attempt.passed ? "rgba(22, 163, 74, 0.24)" : "rgba(220, 38, 38, 0.2)"}`,
                      background: attempt.passed ? "rgba(240, 253, 244, 0.9)" : "rgba(254, 242, 242, 0.9)",
                      borderRadius: 12,
                      padding: 12,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
                      <strong style={{ color: "#10233d" }}>
                        Attempt {attempt.attempt} - {attempt.discarded ? "discarded" : attempt.passed ? "pass" : "fail"}
                      </strong>
                      <span style={{ color: "#526070", fontSize: "0.88rem" }}>{formatDuration(attempt.duration_ms)}</span>
                    </div>
                    {attempt.discarded ? (
                      <div style={{ color: "#7c3aed", fontSize: "0.85rem", marginBottom: 8 }}>
                        Excluded from pass rate and latency averages to reduce first-load bias.
                      </div>
                    ) : null}
                    {attempt.notes.length > 0 ? (
                      <div style={{ color: "#7f1d1d", fontSize: "0.85rem", marginBottom: 8 }}>{attempt.notes.join(" | ")}</div>
                    ) : null}
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "0.8rem", color: "#334155" }}>
                      {JSON.stringify(attempt.summary, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          </details>
        ))}
      </div>
    </article>
  );
}
