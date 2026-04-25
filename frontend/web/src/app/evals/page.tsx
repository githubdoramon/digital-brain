"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { EvalRunCard } from "./EvalRunCard";
import { EvalFlowMeta, EvalRunJob, EvalRunResult } from "./types";

type EvalFlowsResponse = {
  flows: EvalFlowMeta[];
};

const PAYLOAD_PRESETS = [
  {
    id: "baseline",
    label: "Baseline",
    description: "Simple deterministic baseline with no extra reasoning controls.",
    values: {
      stream: false,
      temperature: "0",
      maxTokens: "128",
      reasoningEffort: "",
    },
  },
  {
    id: "reasoning-none",
    label: "Reasoning None",
    description: "Fast controller-style payload with reasoning disabled via effort controls.",
    values: {
      stream: false,
      temperature: "0",
      maxTokens: "128",
      reasoningEffort: "none",
    },
  },
  {
    id: "strict-schema",
    label: "Strict Schema",
    description: "Structured-output mode for models that behave best with per-case JSON schema.",
    values: {
      stream: false,
      temperature: "0",
      maxTokens: "128",
      reasoningEffort: "",
    },
  },
] as const;

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function EvalsPage() {
  const [flows, setFlows] = useState<EvalFlowMeta[]>([]);
  const [selectedFlowId, setSelectedFlowId] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [repetitions, setRepetitions] = useState("5");
  const [stream, setStream] = useState(false);
  const [temperature, setTemperature] = useState("0");
  const [maxTokens, setMaxTokens] = useState("128");
  const [reasoningEffort, setReasoningEffort] = useState("none");
  const [runs, setRuns] = useState<Array<{ id: string; result: EvalRunResult }>>([]);
  const [activeJob, setActiveJob] = useState<EvalRunJob | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;

    async function loadFlows() {
      try {
        const result = await api.get<EvalFlowsResponse>("/evals/flows");
        if (ignore) return;
        setFlows(result.flows);
        setSelectedFlowId((current) => current || result.flows[0]?.flow_id || "");
      } catch (loadError) {
        if (ignore) return;
        setError(loadError instanceof Error ? loadError.message : "Failed to load eval flows");
      } finally {
        if (!ignore) {
          setIsBootstrapping(false);
        }
      }
    }

    loadFlows();
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!activeJob || activeJob.status === "completed" || activeJob.status === "failed") {
      return undefined;
    }

    const intervalId = window.setInterval(async () => {
      try {
        const updatedJob = await api.get<EvalRunJob>(`/evals/runs/${activeJob.job_id}`);
        setActiveJob(updatedJob);

        if (updatedJob.status === "completed") {
          const result = updatedJob.result;
          if (result) {
            setRuns((current) => {
              const runId = `${updatedJob.job_id}-result`;
              if (current.some((run) => run.id === runId)) {
                return current;
              }
              return [{ id: runId, result }, ...current];
            });
          }
          setIsLoading(false);
          setActiveJob(null);
        }

        if (updatedJob.status === "failed") {
          setError(updatedJob.error || "Eval run failed");
          setIsLoading(false);
          setActiveJob(null);
        }
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : "Failed to poll eval job");
        setIsLoading(false);
      }
    }, 1200);

    return () => window.clearInterval(intervalId);
  }, [activeJob]);

  const selectedFlow = useMemo(
    () => flows.find((flow) => flow.flow_id === selectedFlowId) ?? null,
    [flows, selectedFlowId]
  );

  const effectivePayloadPreview = useMemo(() => {
    return {
      stream,
      temperature: temperature.trim() === "" ? undefined : Number(temperature),
      max_tokens: maxTokens.trim() === "" ? undefined : Number(maxTokens),
      reasoning_effort: reasoningEffort || undefined,
    };
  }, [maxTokens, reasoningEffort, stream, temperature]);

  async function handleRun() {
    if (!selectedFlowId) return;
    setIsLoading(true);
    setError(null);
    setActiveJob(null);
    try {
      const job = await api.post<EvalRunJob>("/evals/run", {
        flow_id: selectedFlowId,
        llm_model: llmModel.trim() || undefined,
        repetitions: Number(repetitions) || 5,
        discard_first_attempt: true,
        stream,
        temperature: temperature.trim() === "" ? undefined : Number(temperature),
        max_tokens: maxTokens.trim() === "" ? undefined : Number(maxTokens),
        reasoning_effort: reasoningEffort || undefined,
      });
      setActiveJob(job);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Eval run failed");
      setIsLoading(false);
    }
  }

  function applyPreset(presetId: (typeof PAYLOAD_PRESETS)[number]["id"]) {
    const preset = PAYLOAD_PRESETS.find((item) => item.id === presetId);
    if (!preset) return;
    setStream(preset.values.stream);
    setTemperature(preset.values.temperature);
    setMaxTokens(preset.values.maxTokens);
    setReasoningEffort(preset.values.reasoningEffort);
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "radial-gradient(circle at top left, rgba(192, 219, 255, 0.45), transparent 32%), linear-gradient(180deg, #f6f8fb 0%, #eef3f8 100%)",
        padding: "40px 20px 80px",
      }}
    >
      <div style={{ maxWidth: 1180, margin: "0 auto", display: "grid", gap: 24 }}>
        <section
          style={{
            borderRadius: 24,
            padding: 24,
            background: "linear-gradient(135deg, #10233d 0%, #1f4068 100%)",
            color: "#f8fafc",
            boxShadow: "0 28px 60px rgba(16, 35, 61, 0.2)",
          }}
        >
          <div style={{ maxWidth: 760, display: "grid", gap: 10 }}>
            <div style={{ textTransform: "uppercase", letterSpacing: "0.12em", fontSize: "0.8rem", color: "rgba(226, 232, 240, 0.8)" }}>
              Live LLM evals
            </div>
            <h1 style={{ margin: 0, fontSize: "clamp(2rem, 4vw, 3rem)", lineHeight: 1.05 }}>Run the same flow repeatedly against any Ollama model.</h1>
            <p style={{ margin: 0, color: "rgba(226, 232, 240, 0.9)", fontSize: "1rem", lineHeight: 1.6 }}>
              Pick a flow, type a model name, and compare repeated live outputs against lightweight expectations. Results stay on this page only.
            </p>
            <p style={{ margin: 0, color: "rgba(226, 232, 240, 0.82)", fontSize: "0.95rem", lineHeight: 1.6 }}>
              By default, the first measured attempt is discarded so model load jitter does not skew benchmark timing.
            </p>
          </div>
        </section>

        <section
          style={{
            display: "grid",
            gap: 18,
            gridTemplateColumns: "minmax(280px, 360px) minmax(0, 1fr)",
          }}
        >
          <div style={{ borderRadius: 20, background: "#fff", padding: 20, boxShadow: "0 16px 36px rgba(16, 35, 61, 0.08)", display: "grid", gap: 16 }}>
            <div>
              <h2 style={{ margin: 0, color: "#10233d", fontSize: "1.1rem" }}>Runner</h2>
              <p style={{ margin: "6px 0 0", color: "#526070", lineHeight: 1.5 }}>
                Keep it simple: choose one flow, one Ollama model, and 1-20 repetitions. Each run starts as a background job so the browser avoids long request timeouts.
              </p>
            </div>

            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ fontWeight: 600, color: "#10233d" }}>Presets</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {PAYLOAD_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => applyPreset(preset.id)}
                    disabled={isLoading}
                    style={{
                      borderRadius: 999,
                      border: "1px solid #d8dee8",
                      background: "#f7f9fc",
                      color: "#10233d",
                      padding: "8px 12px",
                      fontSize: "0.85rem",
                      fontWeight: 600,
                      cursor: isLoading ? "not-allowed" : "pointer",
                    }}
                    title={preset.description}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>

            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontWeight: 600, color: "#10233d" }}>Flow</span>
              <select
                value={selectedFlowId}
                onChange={(event) => setSelectedFlowId(event.target.value)}
                disabled={isBootstrapping || isLoading}
                style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem", background: "#fff" }}
              >
                {flows.map((flow) => (
                  <option key={flow.flow_id} value={flow.flow_id}>
                    {flow.label}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontWeight: 600, color: "#10233d" }}>Ollama model name</span>
              <input
                value={llmModel}
                onChange={(event) => setLlmModel(event.target.value)}
                disabled={isLoading}
                placeholder="llama3.1:8b, qwen3:14b, mistral-small..."
                style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem" }}
              />
            </label>

            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontWeight: 600, color: "#10233d" }}>Repetitions</span>
              <select
                value={repetitions}
                onChange={(event) => setRepetitions(event.target.value)}
                disabled={isLoading}
                style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem", background: "#fff" }}
              >
                {[1, 3, 5, 10, 20].map((count) => (
                  <option key={count} value={count}>
                    {count}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "flex", gap: 10, alignItems: "center", color: "#10233d", fontWeight: 600 }}>
              <input type="checkbox" checked={stream} onChange={(event) => setStream(event.target.checked)} disabled={isLoading} />
              Stream response
            </label>

            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
              <label style={{ display: "grid", gap: 6 }}>
                <span style={{ fontWeight: 600, color: "#10233d" }}>Temperature</span>
                <input value={temperature} onChange={(event) => setTemperature(event.target.value)} disabled={isLoading} style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem" }} />
              </label>
              <label style={{ display: "grid", gap: 6 }}>
                <span style={{ fontWeight: 600, color: "#10233d" }}>Max tokens</span>
                <input value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} disabled={isLoading} style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem" }} />
              </label>
            </div>

            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontWeight: 600, color: "#10233d" }}>Reasoning effort</span>
              <select value={reasoningEffort} onChange={(event) => setReasoningEffort(event.target.value)} disabled={isLoading} style={{ borderRadius: 12, border: "1px solid #d8dee8", padding: "12px 14px", fontSize: "0.95rem", background: "#fff" }}>
                <option value="">Default</option>
                <option value="none">none</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </select>
            </label>

            <div style={{ display: "grid", gap: 6 }}>
              <span style={{ fontWeight: 600, color: "#10233d" }}>Effective payload preview</span>
              <pre
                style={{
                  margin: 0,
                  borderRadius: 12,
                  border: "1px solid #d8dee8",
                  padding: "12px 14px",
                  fontSize: "0.8rem",
                  fontFamily: "monospace",
                  color: "#334155",
                  background: "#f7f9fc",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {JSON.stringify(effectivePayloadPreview, null, 2)}
              </pre>
            </div>

            <button
              type="button"
              onClick={handleRun}
              disabled={isBootstrapping || isLoading || !selectedFlowId}
              style={{
                border: 0,
                borderRadius: 14,
                padding: "14px 16px",
                background: isLoading ? "#93a4ba" : "linear-gradient(135deg, #10233d 0%, #285c8a 100%)",
                color: "#fff",
                fontWeight: 700,
                cursor: isLoading ? "progress" : "pointer",
              }}
            >
              {isLoading ? "Running eval..." : "Run flow"}
            </button>

            {activeJob && isLoading ? (
              <div style={{ borderRadius: 12, background: "#f7f9fc", padding: 12, color: "#334155", fontSize: "0.92rem", lineHeight: 1.5 }}>
                <div style={{ fontWeight: 700, color: "#10233d", marginBottom: 4 }}>
                  {activeJob.status === "queued" ? "Queued" : "Running"} eval job
                </div>
                <div>
                  Case {activeJob.progress?.current_case ?? 0}/{activeJob.progress?.total_cases ?? 0}
                  {activeJob.progress?.current_case_title ? ` - ${activeJob.progress.current_case_title}` : ""}
                </div>
                <div>
                  Attempt {activeJob.progress?.current_attempt ?? 0}/{activeJob.progress?.total_attempts ?? 0}
                </div>
              </div>
            ) : null}

            {error ? <div style={{ color: "#b91c1c", fontSize: "0.92rem" }}>{error}</div> : null}
          </div>

          <div style={{ borderRadius: 20, background: "rgba(255,255,255,0.88)", padding: 20, boxShadow: "0 16px 36px rgba(16, 35, 61, 0.08)", display: "grid", gap: 16 }}>
            <div>
              <h2 style={{ margin: 0, color: "#10233d", fontSize: "1.1rem" }}>Selected flow</h2>
              <p style={{ margin: "6px 0 0", color: "#526070", lineHeight: 1.5 }}>
                {selectedFlow?.description || "Loading flow details..."}
              </p>
            </div>

            {selectedFlow ? (
              <>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <div style={{ borderRadius: 999, padding: "8px 12px", background: "#e8eef7", color: "#10233d", fontWeight: 600 }}>
                    {selectedFlow.case_count} cases
                  </div>
                  <div style={{ borderRadius: 999, padding: "8px 12px", background: "#eef7f1", color: "#1f5135", fontWeight: 600 }}>
                    Local-only history
                  </div>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  {selectedFlow.cases.map((flowCase) => (
                    <div key={flowCase.case_id} style={{ borderRadius: 14, padding: 14, background: "#fff", border: "1px solid rgba(16, 35, 61, 0.08)" }}>
                      <div style={{ fontWeight: 700, color: "#10233d" }}>{flowCase.title}</div>
                      {flowCase.description ? (
                        <div style={{ marginTop: 6, color: "#526070", fontSize: "0.9rem" }}>{flowCase.description}</div>
                      ) : null}
                      <label style={{ display: "grid", gap: 6, marginTop: 12 }}>
                        <span style={{ color: "#10233d", fontWeight: 600, fontSize: "0.9rem" }}>Defined response JSON schema</span>
                        <pre
                          style={{
                            margin: 0,
                            borderRadius: 12,
                            border: "1px solid #d8dee8",
                            padding: "12px 14px",
                            fontSize: "0.82rem",
                            fontFamily: "monospace",
                            color: "#334155",
                            background: "#f7f9fc",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                          }}
                        >
                          {flowCase.response_json_schema
                            ? JSON.stringify(flowCase.response_json_schema, null, 2)
                            : "No schema defined for this case."}
                        </pre>
                      </label>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </div>
        </section>

        <section style={{ display: "grid", gap: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "end" }}>
            <div>
              <h2 style={{ margin: 0, color: "#10233d", fontSize: "1.25rem" }}>Recent runs</h2>
              <p style={{ margin: "6px 0 0", color: "#526070" }}>Refresh clears everything, so compare models while they are on-screen.</p>
            </div>
            {runs.length > 0 ? (
              <div style={{ color: "#526070", fontSize: "0.92rem" }}>
                Best visible pass rate {formatPercent(Math.max(...runs.map((run) => run.result.summary.pass_rate)))}
              </div>
            ) : null}
          </div>

          {runs.length === 0 ? (
            <div style={{ borderRadius: 20, padding: 28, background: "rgba(255,255,255,0.78)", color: "#526070", boxShadow: "inset 0 0 0 1px rgba(16, 35, 61, 0.08)" }}>
              No runs yet. Start with `router` or `event_extraction` and compare 5 repetitions across two models.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 18 }}>
              {runs.map((run) => (
                <EvalRunCard key={run.id} runKey={run.id} run={run.result} />
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
