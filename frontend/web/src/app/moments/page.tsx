"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Moment = {
  id: string;
  source_type: string;
  observed_at: string;
  observed_timezone: string;
  observation: {
    summary: string;
    visible_text: string[];
    people_presence: string;
    people_count_min: number;
    people_count_max: number;
    people_details: string[];
    objects: Array<{ label: string; count_min: number; count_max: number; details: string[] }>;
    setting: string | null;
    interpretations: Array<{ claim: string; evidence: string[]; confidence: string }>;
    uncertainties: string[];
  };
  location: Record<string, unknown>;
  place_id: string | null;
};

type MomentCollection = { moments: Moment[]; total: number; limit: number; offset: number };

const PAGE_SIZE = 25;

function displayTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(
    new Date(value),
  );
}

export default function MomentsPage() {
  const [data, setData] = useState<MomentCollection | null>(null);
  const [sourceType, setSourceType] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (sourceType.trim()) query.set("source_type", sourceType.trim());
    if (after) query.set("observed_after", new Date(after).toISOString());
    if (before) query.set("observed_before", new Date(before).toISOString());
    try {
      setData(await api.get<MomentCollection>(`/moments?${query.toString()}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load moments.");
    } finally {
      setLoading(false);
    }
  }, [after, before, offset, sourceType]);

  useEffect(() => {
    void load();
  }, [load]);

  function submitFilters(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    if (offset === 0) void load();
  }

  return (
    <main style={{ maxWidth: 1040, margin: "0 auto", padding: "34px 20px 72px" }}>
      <header style={{ marginBottom: 24 }}>
        <p style={{ color: "#637083", marginBottom: 6 }}>Memory inspection</p>
        <h1 style={{ margin: 0 }}>Moments</h1>
        <p style={{ color: "#4a5668", maxWidth: 700 }}>
          Canonical observations received from your devices. Images and runtime diagnostics are intentionally not stored here.
        </p>
      </header>

      <form onSubmit={submitFilters} style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 24 }}>
        <input value={sourceType} onChange={(event) => setSourceType(event.target.value)} placeholder="Source type" />
        <input type="datetime-local" value={after} onChange={(event) => setAfter(event.target.value)} aria-label="Observed after" />
        <input type="datetime-local" value={before} onChange={(event) => setBefore(event.target.value)} aria-label="Observed before" />
        <button type="submit" disabled={loading}>Apply filters</button>
      </form>

      {error ? <p style={{ color: "#b42318" }}>{error}</p> : null}
      <p style={{ color: "#637083" }}>{data ? `${data.total} moment${data.total === 1 ? "" : "s"}` : "Loading…"}</p>

      <section style={{ display: "grid", gap: 14 }}>
        {data?.moments.map((moment) => {
          const location = moment.location ?? {};
          const locationLabel = typeof location.lat === "number" && typeof location.lon === "number"
            ? `${location.lat.toFixed(5)}, ${location.lon.toFixed(5)}`
            : "No location";
          return (
            <article key={moment.id} style={{ border: "1px solid #dce2e9", borderRadius: 14, padding: 20, background: "#fff" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <strong>{moment.observation.summary}</strong>
                <span style={{ color: "#637083" }}>{displayTime(moment.observed_at)}</span>
              </div>
              <p style={{ color: "#4a5668", marginBottom: 8 }}>{moment.source_type} · {locationLabel}</p>
              <p style={{ margin: "8px 0" }}>
                People: {moment.observation.people_presence} ({moment.observation.people_count_min}–{moment.observation.people_count_max})
                {moment.observation.setting ? ` · ${moment.observation.setting}` : ""}
              </p>
              {moment.observation.objects.length ? <p style={{ margin: "8px 0" }}>Objects: {moment.observation.objects.map((item) => `${item.label} (${item.count_min}–${item.count_max})`).join(", ")}</p> : null}
              {moment.observation.visible_text.length ? <p style={{ margin: "8px 0" }}>OCR: {moment.observation.visible_text.join(" · ")}</p> : null}
              {moment.observation.interpretations.length ? <p style={{ margin: "8px 0" }}>Interpretations: {moment.observation.interpretations.map((item) => item.claim).join(" · ")}</p> : null}
              {moment.observation.uncertainties.length ? <p style={{ margin: "8px 0", color: "#637083" }}>Uncertainties: {moment.observation.uncertainties.join(" · ")}</p> : null}
              <details>
                <summary>Canonical JSON</summary>
                <pre style={{ overflowX: "auto", fontSize: 12 }}>{JSON.stringify(moment, null, 2)}</pre>
              </details>
            </article>
          );
        })}
      </section>

      <nav style={{ display: "flex", gap: 12, marginTop: 24 }}>
        <button disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
        <button disabled={loading || !data || offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
      </nav>
    </main>
  );
}
