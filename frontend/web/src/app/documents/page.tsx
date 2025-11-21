"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type DocumentItem = {
  document_id: string;
  title: string;
  tags: string[];
  summary?: string | null;
  description?: string | null;
  file_name: string;
  file_mime?: string | null;
  file_size?: number | null;
  download_url: string;
  created_at: string;
  updated_at: string;
  snippet?: string | null;
  content_preview?: string | null;
  raw_metadata?: Record<string, unknown>;
};

type DocumentCollection = {
  documents: DocumentItem[];
};

type StatusMessage =
  | { kind: "idle" }
  | { kind: "error"; message: string }
  | { kind: "success"; message: string };

const DEFAULT_STATUS: StatusMessage = { kind: "idle" };

function parseTagsInput(input: string): string[] {
  return input
    .split(/[,;\n]/)
    .map((segment) => segment.trim())
    .filter((segment) => segment.length > 0);
}

function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) {
    return "Unknown size";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSearching, setIsSearching] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [status, setStatus] = useState<StatusMessage>(DEFAULT_STATUS);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchTags, setSearchTags] = useState("");

  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [uploadSummary, setUploadSummary] = useState("");
  const [uploadDescription, setUploadDescription] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setStatus(DEFAULT_STATUS);
    try {
      const data = await api.get<DocumentCollection>("/documents");
      setDocuments(data.documents ?? []);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load documents";
      setStatus({ kind: "error", message });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  const handleUpload = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!uploadFile) {
        setStatus({ kind: "error", message: "Select a file before uploading" });
        return;
      }

      const trimmedTitle = uploadTitle.trim();
      const tags = parseTagsInput(uploadTags);
      const formData = new FormData();
      formData.append("title", trimmedTitle);
      if (tags.length > 0) {
        formData.append("tags", JSON.stringify(tags));
      }
      if (uploadSummary.trim()) {
        formData.append("summary", uploadSummary.trim());
      }
      if (uploadDescription.trim()) {
        formData.append("description", uploadDescription.trim());
      }
      formData.append("file", uploadFile);

      setIsUploading(true);
      setStatus(DEFAULT_STATUS);

      try {
        const created = await api.post<DocumentItem>("/documents", formData);
        setDocuments((previous) => [created, ...previous.filter((doc) => doc.document_id !== created.document_id)]);
        setStatus({ kind: "success", message: `Uploaded "${created.title}"` });
        setUploadTitle("");
        setUploadTags("");
        setUploadSummary("");
        setUploadDescription("");
        setUploadFile(null);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to upload document";
        setStatus({ kind: "error", message });
      } finally {
        setIsUploading(false);
      }
    },
    [uploadDescription, uploadFile, uploadSummary, uploadTags, uploadTitle]
  );

  const handleSearch = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmedQuery = searchQuery.trim();
      const tags = parseTagsInput(searchTags);
      if (!trimmedQuery && tags.length === 0) {
        void loadDocuments();
        return;
      }

      setIsSearching(true);
      setStatus(DEFAULT_STATUS);

      try {
        const payload = {
          query: trimmedQuery,
          tags,
          limit: 50,
        };
        const result = await api.post<DocumentCollection>("/documents/search", payload);
        setDocuments(result.documents ?? []);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to search documents";
        setStatus({ kind: "error", message });
      } finally {
        setIsSearching(false);
      }
    },
    [loadDocuments, searchQuery, searchTags]
  );

  const resetSearch = useCallback(() => {
    setSearchQuery("");
    setSearchTags("");
    void loadDocuments();
  }, [loadDocuments]);

  const handleDelete = useCallback(
    async (document: DocumentItem) => {
      const confirmed = window.confirm(`Delete document "${document.title}"? This action cannot be undone.`);
      if (!confirmed) {
        return;
      }

      setDeletingId(document.document_id);
      setStatus(DEFAULT_STATUS);

      try {
        await api.delete(`/documents/${encodeURIComponent(document.document_id)}`);
        setDocuments((previous) => previous.filter((item) => item.document_id !== document.document_id));
        setStatus({ kind: "success", message: `Deleted "${document.title}"` });
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to delete document";
        setStatus({ kind: "error", message });
      } finally {
        setDeletingId(null);
      }
    },
    []
  );

  const totalCount = documents.length;

  const summaryText = useMemo(() => {
    if (isLoading) {
      return "Loading documents…";
    }
    if (totalCount === 0) {
      return "No documents yet";
    }
    return `${totalCount} document${totalCount === 1 ? "" : "s"}`;
  }, [isLoading, totalCount]);

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <header style={{ display: "grid", gap: "8px" }}>
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Documents</h1>
        <p style={{ color: "#555" }}>
          Upload documents for safe keeping, automatic parsing, and quick retrieval. Extracted text is embedded so you
          can find files by their content.
        </p>
      </header>

      {status.kind === "error" && (
        <div
          role="alert"
          style={{
            background: "#fee2e2",
            border: "1px solid #fca5a5",
            color: "#991b1b",
            borderRadius: "8px",
            padding: "12px 16px",
          }}
        >
          {status.message}
        </div>
      )}

      {status.kind === "success" && (
        <div
          role="status"
          style={{
            background: "#dcfce7",
            border: "1px solid #86efac",
            color: "#166534",
            borderRadius: "8px",
            padding: "12px 16px",
          }}
        >
          {status.message}
        </div>
      )}

      <section
        style={{
          display: "grid",
          gap: "16px",
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "20px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
        }}
      >
        <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Upload new document</h2>
        <form style={{ display: "grid", gap: "12px" }} onSubmit={handleUpload}>
          <label style={{ display: "grid", gap: "4px" }}>
            <span style={{ fontWeight: 600 }}>Title</span>
            <input
              type="text"
              value={uploadTitle}
              onChange={(event) => setUploadTitle(event.target.value)}
              placeholder="Quarterly financial report"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 12px",
              }}
            />
            <small style={{ color: "#6b7280" }}>Leave empty to auto-generate from content</small>
          </label>

          <label style={{ display: "grid", gap: "4px" }}>
            <span style={{ fontWeight: 600 }}>Tags</span>
            <input
              type="text"
              value={uploadTags}
              onChange={(event) => setUploadTags(event.target.value)}
              placeholder="finance, q1, board"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 12px",
              }}
            />
            <small style={{ color: "#6b7280" }}>Separate tags with commas</small>
          </label>

          <label style={{ display: "grid", gap: "4px" }}>
            <span style={{ fontWeight: 600 }}>Summary</span>
            <textarea
              value={uploadSummary}
              onChange={(event) => setUploadSummary(event.target.value)}
              rows={2}
              placeholder="Key points covered in the document"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 12px",
                resize: "vertical",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "4px" }}>
            <span style={{ fontWeight: 600 }}>Description</span>
            <textarea
              value={uploadDescription}
              onChange={(event) => setUploadDescription(event.target.value)}
              rows={3}
              placeholder="Provide additional context or notes"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 12px",
                resize: "vertical",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "4px" }}>
            <span style={{ fontWeight: 600 }}>File *</span>
            <input
              type="file"
              accept=".pdf,.doc,.docx,.txt,.md"
              onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
              required
            />
            <small style={{ color: "#6b7280" }}>Supports PDF, Word, and text files</small>
          </label>

          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="submit"
              disabled={isUploading}
              style={{
                background: "#0b6bcb",
                color: "#fff",
                border: "none",
                borderRadius: "8px",
                padding: "10px 18px",
                fontWeight: 600,
                cursor: isUploading ? "progress" : "pointer",
                opacity: isUploading ? 0.75 : 1,
              }}
            >
              {isUploading ? "Uploading…" : "Upload"}
            </button>
          </div>
        </form>
      </section>

      <section
        style={{
          display: "grid",
          gap: "16px",
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "20px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
        }}
      >
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "16px" }}>
          <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Your documents</h2>
          <span style={{ color: "#666" }}>{summaryText}</span>
        </header>

        <form
          style={{
            display: "grid",
            gap: "12px",
            border: "1px solid #e5e7eb",
            borderRadius: "10px",
            padding: "16px",
            background: "#f9fafb",
          }}
          onSubmit={handleSearch}
        >
          <div style={{ display: "grid", gap: "8px" }}>
            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Search query</span>
              <input
                type="text"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search by content or title"
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                }}
              />
            </label>

            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Filter by tags</span>
              <input
                type="text"
                value={searchTags}
                onChange={(event) => setSearchTags(event.target.value)}
                placeholder="project, research"
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                }}
              />
              <small style={{ color: "#6b7280" }}>Separate tags with commas</small>
            </label>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
            <button
              type="button"
              onClick={resetSearch}
              disabled={isSearching || isLoading}
              style={{
                background: "transparent",
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 16px",
                fontWeight: 600,
                color: "#2563eb",
                cursor: isSearching ? "progress" : "pointer",
              }}
            >
              Reset
            </button>
            <button
              type="submit"
              disabled={isSearching}
              style={{
                background: "#0b6bcb",
                color: "#fff",
                border: "none",
                borderRadius: "8px",
                padding: "8px 16px",
                fontWeight: 600,
                cursor: isSearching ? "progress" : "pointer",
                opacity: isSearching ? 0.75 : 1,
              }}
            >
              {isSearching ? "Searching…" : "Search"}
            </button>
          </div>
        </form>

        <div style={{ display: "grid", gap: "16px" }}>
          {isLoading ? (
            <p style={{ color: "#6b7280" }}>Loading documents…</p>
          ) : documents.length === 0 ? (
            <p style={{ color: "#6b7280" }}>
              No documents found. Upload a file or adjust your search filters.
            </p>
          ) : (
            documents.map((document) => (
              <DocumentCard
                key={document.document_id}
                document={document}
                onDelete={handleDelete}
                isDeleting={deletingId === document.document_id}
              />
            ))
          )}
        </div>
      </section>
    </section>
  );
}

type DocumentCardProps = {
  document: DocumentItem;
  onDelete: (document: DocumentItem) => Promise<void>;
  isDeleting: boolean;
};

function DocumentCard({ document, onDelete, isDeleting }: DocumentCardProps) {
  return (
    <article
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: "12px",
        padding: "16px",
        display: "grid",
        gap: "12px",
        background: "#fff",
      }}
    >
      <header style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "flex-start" }}>
        <div style={{ display: "grid", gap: "6px" }}>
          <h3 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 600 }}>{document.title}</h3>
          <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>
            Uploaded {formatDate(document.created_at)} · {formatBytes(document.file_size)} · {document.file_name}
          </span>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <a
            href={`/api/orchestrator${document.download_url}`}
            style={{
              background: "#0b6bcb",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              padding: "8px 14px",
              fontWeight: 600,
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            target="_blank"
            rel="noopener noreferrer"
          >
            Download
          </a>
          <button
            type="button"
            onClick={() => void onDelete(document)}
            disabled={isDeleting}
            style={{
              background: "#fee2e2",
              color: "#b91c1c",
              border: "1px solid #fecaca",
              borderRadius: "8px",
              padding: "8px 14px",
              fontWeight: 600,
              cursor: isDeleting ? "progress" : "pointer",
              opacity: isDeleting ? 0.75 : 1,
            }}
          >
            {isDeleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </header>

      {document.tags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", fontSize: "0.85rem", color: "#2563eb" }}>
          <strong style={{ fontWeight: 600 }}>Tags:</strong>
          {document.tags.map((tag) => (
            <span key={tag}>#{tag}</span>
          ))}
        </div>
      )}

      {(document.summary || document.description || document.snippet) && (
        <p style={{ margin: 0, color: "#374151", lineHeight: 1.5 }}>
          {document.summary || document.description || document.snippet}
        </p>
      )}

      {document.content_preview && (
        <details style={{ fontSize: "0.9rem", color: "#4b5563" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Content preview</summary>
          <pre
            style={{
              marginTop: "8px",
              padding: "12px",
              background: "#f9fafb",
              border: "1px solid #e5e7eb",
              borderRadius: "8px",
              whiteSpace: "pre-wrap",
              maxHeight: "240px",
              overflow: "auto",
              fontSize: "0.8rem",
            }}
          >
            {document.content_preview}
          </pre>
        </details>
      )}
    </article>
  );
}

