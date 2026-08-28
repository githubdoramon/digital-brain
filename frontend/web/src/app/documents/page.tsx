"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type DocumentItem = {
  document_id: string;
  title: string;
  tags: string[];
  description?: string | null;
  document_date?: string | null;
  file_name: string;
  file_mime?: string | null;
  file_size?: number | null;
  download_url: string;
  created_at: string;
  updated_at: string;
  snippet?: string | null;
  content_preview?: string | null;
  raw_metadata?: Record<string, unknown>;
  enhancement_status: "pending" | "processing" | "complete" | "failed";
  enhancement_error?: string | null;
  enhancement_attempts: number;
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

function toDateInputValue(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSearching, setIsSearching] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [status, setStatus] = useState<StatusMessage>(DEFAULT_STATUS);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchTags, setSearchTags] = useState("");
  const [sortBy, setSortBy] = useState<"document_date" | "created_at">("document_date");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [missingEnhancement, setMissingEnhancement] = useState(false);

  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [uploadDescription, setUploadDescription] = useState("");
  const [uploadDate, setUploadDate] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const [editingDocument, setEditingDocument] = useState<DocumentItem | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editDate, setEditDate] = useState("");
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setStatus(DEFAULT_STATUS);
    try {
      const params = new URLSearchParams({ sort_by: sortBy, sort_direction: sortDirection });
      if (missingEnhancement) {
        params.set("missing_enhancement", "true");
      }
      const data = await api.get<DocumentCollection>(`/documents?${params.toString()}`);
      setDocuments(data.documents ?? []);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load documents";
      setStatus({ kind: "error", message });
    } finally {
      setIsLoading(false);
    }
  }, [missingEnhancement, sortBy, sortDirection]);

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
      if (uploadDescription.trim()) {
        formData.append("description", uploadDescription.trim());
      }
      if (uploadDate) {
        formData.append("document_date", uploadDate);
      }
      formData.append("file", uploadFile);

      setIsUploading(true);
      setStatus(DEFAULT_STATUS);

      try {
        const created = await api.post<DocumentItem>("/ingest/document", formData);
        setDocuments((previous) => [created, ...previous.filter((doc) => doc.document_id !== created.document_id)]);
        setStatus({ kind: "success", message: `Uploaded "${created.title}"; enhancement queued` });
        setUploadTitle("");
        setUploadTags("");
        setUploadDescription("");
        setUploadDate("");
        setUploadFile(null);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to upload document";
        setStatus({ kind: "error", message });
      } finally {
        setIsUploading(false);
      }
    },
    [uploadDate, uploadDescription, uploadFile, uploadTags, uploadTitle]
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
          missing_enhancement: missingEnhancement,
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
    [loadDocuments, missingEnhancement, searchQuery, searchTags]
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

  const handleRetryEnhancement = useCallback(async (document: DocumentItem) => {
    setRetryingId(document.document_id);
    setStatus(DEFAULT_STATUS);
    try {
      const updated = await api.post<DocumentItem>(
        `/documents/${encodeURIComponent(document.document_id)}/enhance`
      );
      setDocuments((previous) =>
        previous.map((item) => (item.document_id === updated.document_id ? updated : item))
      );
      setStatus({ kind: "success", message: `Enhancement queued for "${document.title}"` });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to queue enhancement";
      setStatus({ kind: "error", message });
    } finally {
      setRetryingId(null);
    }
  }, []);

  const beginEditing = useCallback((document: DocumentItem) => {
    setEditingDocument(document);
    setEditTitle(document.title ?? "");
    setEditTags(document.tags.join(", "));
    setEditDescription(document.description ?? "");
    setEditDate(toDateInputValue(document.document_date));
    setEditError(null);
  }, []);

  const closeEdit = useCallback(() => {
    if (isSavingEdit) {
      return;
    }
    setEditingDocument(null);
    setEditTitle("");
    setEditTags("");
    setEditDescription("");
    setEditDate("");
    setEditError(null);
  }, [isSavingEdit]);

  const handleEditSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!editingDocument) {
        return;
      }

      setIsSavingEdit(true);
      setEditError(null);

      const trimmedTitle = editTitle.trim();
      const trimmedDescription = editDescription.trim();
      const parsedTags = parseTagsInput(editTags);

      const payload: Record<string, unknown> = {
        title: trimmedTitle,
        description: trimmedDescription,
        tags: parsedTags,
        document_date: editDate ? new Date(editDate).toISOString() : null,
      };

      try {
        const updated = await api.patch<DocumentItem>(
          `/documents/${encodeURIComponent(editingDocument.document_id)}`,
          payload
        );
        setDocuments((previous) =>
          previous.map((item) => (item.document_id === updated.document_id ? updated : item))
        );
        setStatus({ kind: "success", message: `Updated "${updated.title}"` });
        setEditingDocument(null);
        setEditTitle("");
        setEditTags("");
        setEditDescription("");
        setEditDate("");
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to update document";
        setEditError(message);
      } finally {
        setIsSavingEdit(false);
      }
    },
    [editDate, editDescription, editTags, editTitle, editingDocument]
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
    <>
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
            <span style={{ fontWeight: 600 }}>Document date</span>
            <input
              type="date"
              value={uploadDate}
              onChange={(event) => setUploadDate(event.target.value)}
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "8px 12px",
              }}
            />
            <small style={{ color: "#6b7280" }}>Leave empty to auto-detect from content</small>
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

            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "end" }}>
              <label style={{ display: "grid", gap: "4px" }}>
                <span style={{ fontWeight: 600 }}>Sort by</span>
                <select
                  value={sortBy}
                  onChange={(event) => setSortBy(event.target.value as "document_date" | "created_at")}
                  style={{ border: "1px solid #d1d5db", borderRadius: "8px", padding: "8px 12px" }}
                >
                  <option value="document_date">Document date</option>
                  <option value="created_at">Upload date</option>
                </select>
              </label>
              <label style={{ display: "grid", gap: "4px" }}>
                <span style={{ fontWeight: 600 }}>Order</span>
                <select
                  value={sortDirection}
                  onChange={(event) => setSortDirection(event.target.value as "asc" | "desc")}
                  style={{ border: "1px solid #d1d5db", borderRadius: "8px", padding: "8px 12px" }}
                >
                  <option value="desc">Newest first</option>
                  <option value="asc">Oldest first</option>
                </select>
              </label>
              <label style={{ display: "flex", gap: "8px", alignItems: "center", paddingBottom: "9px" }}>
                <input
                  type="checkbox"
                  checked={missingEnhancement}
                  onChange={(event) => setMissingEnhancement(event.target.checked)}
                />
                <span style={{ fontWeight: 600 }}>Needs enhancement</span>
              </label>
            </div>

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
                onEdit={beginEditing}
                onDelete={handleDelete}
                isDeleting={deletingId === document.document_id}
                onRetryEnhancement={handleRetryEnhancement}
                isRetrying={retryingId === document.document_id}
              />
            ))
          )}
        </div>
      </section>
    </section>

    {editingDocument && (
      <div
        role="dialog"
        aria-modal="true"
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15, 23, 42, 0.45)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
          zIndex: 50,
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            closeEdit();
          }
        }}
      >
        <div
          style={{
            background: "#fff",
            borderRadius: "12px",
            boxShadow: "0 20px 45px rgba(15, 23, 42, 0.2)",
            padding: "24px",
            maxWidth: "560px",
            width: "100%",
            display: "grid",
            gap: "16px",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1.25rem", fontWeight: 600 }}>Edit document metadata</h2>
          <p style={{ margin: 0, color: "#4b5563", fontSize: "0.9rem" }}>
            Update the document details. Leave fields blank to regenerate suggestions.
          </p>

          {editError && (
            <div
              role="alert"
              style={{
                background: "#fee2e2",
                border: "1px solid #fca5a5",
                color: "#991b1b",
                borderRadius: "8px",
                padding: "10px 12px",
                fontSize: "0.9rem",
              }}
            >
              {editError}
            </div>
          )}

          <form style={{ display: "grid", gap: "12px" }} onSubmit={handleEditSubmit}>
            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Title</span>
              <input
                type="text"
                value={editTitle}
                onChange={(event) => setEditTitle(event.target.value)}
                placeholder="Auto-generated if left blank"
                disabled={isSavingEdit}
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                }}
              />
            </label>

            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Document date</span>
              <input
                type="date"
                value={editDate}
                onChange={(event) => setEditDate(event.target.value)}
                disabled={isSavingEdit}
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                }}
              />
              <small style={{ color: "#6b7280" }}>Clear to re-infer from content</small>
            </label>

            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Tags</span>
              <input
                type="text"
                value={editTags}
                onChange={(event) => setEditTags(event.target.value)}
                placeholder="finance, planning"
                disabled={isSavingEdit}
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                }}
              />
              <small style={{ color: "#6b7280" }}>Separate tags with commas</small>
            </label>

            <label style={{ display: "grid", gap: "4px" }}>
              <span style={{ fontWeight: 600 }}>Description</span>
              <textarea
                value={editDescription}
                onChange={(event) => setEditDescription(event.target.value)}
                rows={3}
                placeholder="Provide a summary or leave blank for auto description"
                disabled={isSavingEdit}
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 12px",
                  resize: "vertical",
                }}
              />
            </label>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                onClick={closeEdit}
                disabled={isSavingEdit}
                style={{
                  background: "transparent",
                  border: "1px solid #d1d5db",
                  borderRadius: "8px",
                  padding: "8px 16px",
                  fontWeight: 600,
                  cursor: isSavingEdit ? "not-allowed" : "pointer",
                  color: "#1f2937",
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSavingEdit}
                style={{
                  background: "#0b6bcb",
                  color: "#fff",
                  border: "none",
                  borderRadius: "8px",
                  padding: "8px 16px",
                  fontWeight: 600,
                  cursor: isSavingEdit ? "progress" : "pointer",
                  opacity: isSavingEdit ? 0.75 : 1,
                }}
              >
                {isSavingEdit ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </div>
      </div>
    )}
    </>
  );
}

type DocumentCardProps = {
  document: DocumentItem;
  onEdit: (document: DocumentItem) => void;
  onDelete: (document: DocumentItem) => Promise<void>;
  isDeleting: boolean;
  onRetryEnhancement: (document: DocumentItem) => Promise<void>;
  isRetrying: boolean;
};

function DocumentCard({ document, onEdit, onDelete, isDeleting, onRetryEnhancement, isRetrying }: DocumentCardProps) {
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
          <span style={{ fontSize: "0.85rem", color: "#6b7280", display: "flex", flexWrap: "wrap", gap: "8px" }}>
            <span>Uploaded {formatDate(document.created_at)}</span>
            <span>· {formatBytes(document.file_size)}</span>
            <span>· {document.file_name}</span>
            {document.document_date && (
              <span>· Document date {formatDate(document.document_date)}</span>
            )}
          </span>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          {document.enhancement_status !== "complete" && (
            <button
              type="button"
              onClick={() => void onRetryEnhancement(document)}
              disabled={isRetrying}
              style={{
                background: "#fef3c7",
                color: "#92400e",
                border: "1px solid #fcd34d",
                borderRadius: "8px",
                padding: "8px 14px",
                fontWeight: 600,
                cursor: isRetrying ? "progress" : "pointer",
              }}
            >
              {isRetrying ? "Queueing…" : "Retry enhancement"}
            </button>
          )}
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
            onClick={() => onEdit(document)}
            style={{
              background: "#f3f4f6",
              color: "#1f2937",
              border: "1px solid #d1d5db",
              borderRadius: "8px",
              padding: "8px 14px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Edit metadata
          </button>
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

      {document.enhancement_status !== "complete" && (
        <div
          role="status"
          style={{
            background: document.enhancement_status === "failed" ? "#fee2e2" : "#fef3c7",
            color: document.enhancement_status === "failed" ? "#991b1b" : "#92400e",
            borderRadius: "8px",
            padding: "8px 12px",
            fontSize: "0.9rem",
          }}
        >
          <strong>
            {document.enhancement_status === "failed"
              ? "Enhancement failed"
              : document.enhancement_status === "processing"
                ? "Enhancement in progress"
                : "Enhancement pending"}
          </strong>
          {document.enhancement_error && <span> — {document.enhancement_error}</span>}
        </div>
      )}

      {document.tags.length > 0 && (
        <div style={{ display: "grid", gap: "4px" }}>
          <strong style={{ fontSize: "0.85rem", color: "#1d4ed8" }}>Tags</strong>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
            {document.tags.map((tag) => (
              <span
                key={tag}
                style={{
                  background: "#eff6ff",
                  color: "#1d4ed8",
                  borderRadius: "999px",
                  padding: "2px 10px",
                  fontSize: "0.8rem",
                  fontWeight: 600,
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {(document.description || document.snippet) && (
        <p style={{ margin: 0, color: "#374151", lineHeight: 1.5 }}>
          {document.description || document.snippet}
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
