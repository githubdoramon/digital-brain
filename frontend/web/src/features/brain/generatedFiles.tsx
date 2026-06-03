"use client";

import type { GeneratedFile } from "@/lib/api";

function fileLabel(file: GeneratedFile): string {
  return file.title?.trim() || file.filename?.trim() || "Generated PDF";
}

function fileHref(file: GeneratedFile): string | null {
  const direct = file.web_download_url?.trim();
  if (direct) return direct;
  const backendRelative = file.download_url?.trim();
  if (backendRelative) return `/api/orchestrator${backendRelative}`;
  return null;
}

export function GeneratedFilesRow({ files }: { files: GeneratedFile[] }) {
  if (files.length === 0) return null;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", maxWidth: "80%" }}>
      {files.map((file, index) => {
        const href = fileHref(file);
        const label = fileLabel(file);
        const key = `${file.kind}:${file.artifact_id || index}`;
        const content = (
          <>
            <span aria-hidden="true">PDF</span>
            <span style={{ fontWeight: 650 }}>{label}</span>
          </>
        );
        const style = {
          border: "1px solid #0f766e",
          background: "#ecfdf5",
          borderRadius: "999px",
          color: "#0f172a",
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          fontSize: "0.8rem",
          padding: "6px 10px",
          textDecoration: "none",
        };

        if (!href) {
          return (
            <span key={key} style={style}>
              {content}
            </span>
          );
        }

        return (
          <a key={key} href={href} download={file.filename || undefined} style={style}>
            {content}
          </a>
        );
      })}
    </div>
  );
}
