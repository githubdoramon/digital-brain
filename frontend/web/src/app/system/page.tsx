"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type ServiceVersion = {
  id: string;
  name: string;
  version: string;
  git_sha?: string | null;
  build_time?: string | null;
  image?: string | null;
  sources: string[];
  notes?: string | null;
  metadata?: Record<string, unknown>;
};

type ServiceVersionResponse = {
  generated_at: string;
  services: ServiceVersion[];
  manifest_path?: string | null;
  manifest_metadata?: Record<string, unknown>;
  env_entry_count: number;
  fallback_count: number;
};

function formatDate(input?: string | null): string {
  if (!input) {
    return "—";
  }

  const parsed = new Date(input);
  if (Number.isNaN(parsed.getTime())) {
    return input;
  }

  return parsed.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function abbreviateSha(sha?: string | null): string {
  if (!sha) {
    return "—";
  }
  return sha.length > 10 ? sha.slice(0, 10) : sha;
}

export default function SystemStatusPage() {
  const [data, setData] = useState<ServiceVersionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const frontendMetadata = {
    version: process.env.NEXT_PUBLIC_APP_VERSION ?? "unknown",
    gitSha: process.env.NEXT_PUBLIC_APP_GIT_SHA ?? null,
    buildTime: process.env.NEXT_PUBLIC_APP_BUILD_TIME ?? null,
    deployment: process.env.NEXT_PUBLIC_APP_DEPLOYMENT ?? null,
  };

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const response = await api.get<ServiceVersionResponse>("/system/versions");
        if (!cancelled) {
          setData(response);
        }
      } catch (err) {
        if (!cancelled) {
          console.error("Failed to load service version data", err);
          setError(err instanceof Error ? err.message : "Failed to load system version data.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 600 }}>System Status</h1>
        <p style={{ color: "#555", marginTop: "8px" }}>
          Overview of running containers, their versions, and build timestamps exposed by the orchestrator.
        </p>
      </div>

      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "16px",
          background: "#fff",
          display: "grid",
          gap: "8px",
        }}
      >
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>Frontend Build</h2>
        <div style={{ fontSize: "0.95rem", color: "#111" }}>
          <strong>Version:</strong>{" "}
          <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{frontendMetadata.version}</span>
        </div>
        <div style={{ fontSize: "0.95rem", color: "#374151" }}>
          <strong>Git SHA:</strong>{" "}
          <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{abbreviateSha(frontendMetadata.gitSha)}</span>
        </div>
        <div style={{ fontSize: "0.95rem", color: "#374151" }}>
          <strong>Built At:</strong> {formatDate(frontendMetadata.buildTime)}
        </div>
        {frontendMetadata.deployment && (
          <div style={{ fontSize: "0.95rem", color: "#4b5563" }}>
            <strong>Deployment:</strong>{" "}
            <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{frontendMetadata.deployment}</span>
          </div>
        )}
        <div style={{ fontSize: "0.9rem" }}>
          <a href="/api/version" style={{ color: "#0b6bcb", textDecoration: "underline" }}>
            View frontend version JSON
          </a>
        </div>
      </div>

      {error ? (
        <div
          style={{
            border: "1px solid #fca5a5",
            background: "#fee2e2",
            color: "#b91c1c",
            padding: "16px",
            borderRadius: "8px",
          }}
        >
          <strong style={{ display: "block", marginBottom: "4px" }}>Unable to fetch versions</strong>
          <span>{error}</span>
        </div>
      ) : isLoading ? (
        <div style={{ color: "#666" }}>Loading service information...</div>
      ) : data ? (
        <>
          <div
            style={{
              border: "1px solid #e2e2e2",
              borderRadius: "12px",
              padding: "16px",
              background: "#fff",
              overflowX: "auto",
            }}
          >
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "720px" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #e2e2e2" }}>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Service</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Version</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Git SHA</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Built At</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Image</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Sources</th>
                  <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Notes</th>
                </tr>
              </thead>
              <tbody>
                {data.services.map((service) => (
                  <tr key={service.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "12px 8px", fontWeight: 500, color: "#0f172a" }}>{service.name}</td>
                    <td style={{ padding: "12px 8px", fontFamily: "var(--font-mono, monospace)" }}>{service.version}</td>
                    <td style={{ padding: "12px 8px", fontFamily: "var(--font-mono, monospace)" }}>
                      {abbreviateSha(service.git_sha)}
                    </td>
                    <td style={{ padding: "12px 8px" }}>{formatDate(service.build_time)}</td>
                    <td style={{ padding: "12px 8px", fontFamily: "var(--font-mono, monospace)", fontSize: "0.85rem" }}>
                      {service.image || "—"}
                    </td>
                    <td style={{ padding: "12px 8px" }}>
                      {service.sources.length > 0 ? service.sources.join(", ") : "—"}
                    </td>
                    <td style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#475569" }}>
                      {service.notes || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div
            style={{
              border: "1px solid #e2e2e2",
              borderRadius: "12px",
              padding: "16px",
              background: "#f9fafb",
              color: "#475569",
            }}
          >
            <div style={{ display: "grid", gap: "6px" }}>
              <div>
                <strong>Manifest Path:</strong>{" "}
                <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
                  {data.manifest_path ?? "Not provided"}
                </span>
              </div>
              <div>
                <strong>Generated At:</strong>{" "}
                <span>{formatDate(data.generated_at)}</span>
              </div>
              <div>
                <strong>Environment Overrides:</strong> {data.env_entry_count}
              </div>
              <div>
                <strong>Fallback Entries:</strong> {data.fallback_count}
              </div>
            </div>
          </div>
        </>
      ) : (
        <div style={{ color: "#666" }}>No version data available.</div>
      )}
    </section>
  );
}

