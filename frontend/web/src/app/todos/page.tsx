"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Todo = {
  todo_id: string;
  description: string;
  status: string;
  due_date: string | null;
  created_at: string | null;
  updated_at: string | null;
  contacts: string[];
  events: string[];
  places: string[];
};

type StatusMessage =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

const ACCOMPLISHED_STATUS = "accomplished";

function formatDate(date: string | null): string {
  if (!date) {
    return "No due date";
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(new Date(date));
  } catch (error) {
    console.error("Failed to format date", error);
    return date;
  }
}

export default function TodosPage() {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [status, setStatus] = useState<StatusMessage>({ kind: "idle" });
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const loadTodos = useCallback(async () => {
    setIsLoading(true);
    setStatus((prev) => (prev.kind === "error" ? { kind: "idle" } : prev));

    try {
      const data = await api.get<{ todos: Todo[] }>("/todos");
      setTodos(data.todos ?? []);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to load todos";
      setStatus({ kind: "error", message });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTodos();
  }, [loadTodos]);

  async function toggleTodo(todo: Todo) {
    const nextStatus =
      todo.status?.toLowerCase() === ACCOMPLISHED_STATUS
        ? "pending"
        : ACCOMPLISHED_STATUS;

    setUpdatingId(todo.todo_id);
    setStatus({ kind: "idle" });

    try {
      await api.post("/ingest/todo", {
        todo_id: todo.todo_id,
        description: todo.description,
        status: nextStatus,
        due_date: todo.due_date,
        contact_ids: todo.contacts,
        event_ids: todo.events,
        place_ids: todo.places,
      });

      setTodos((previous) =>
        previous.map((item) =>
          item.todo_id === todo.todo_id
            ? {
                ...item,
                status: nextStatus,
                updated_at: new Date().toISOString(),
              }
            : item
        )
      );
      setStatus({
        kind: "success",
        message:
          nextStatus === ACCOMPLISHED_STATUS
            ? `Marked ${todo.todo_id} as accomplished`
            : `Reopened ${todo.todo_id}`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to update todo status";
      setStatus({ kind: "error", message });
    } finally {
      setUpdatingId(null);
    }
  }

  async function deleteTodo(todo: Todo) {
    const confirmed = window.confirm(`Delete todo "${todo.todo_id}"? This cannot be undone.`);
    if (!confirmed) {
      return;
    }

    setUpdatingId(todo.todo_id);
    setStatus({ kind: "idle" });

    try {
      await api.delete(`/todos/${encodeURIComponent(todo.todo_id)}`);
      setTodos((previous) => previous.filter((item) => item.todo_id !== todo.todo_id));
      setStatus({ kind: "success", message: `Deleted ${todo.todo_id}` });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to delete todo";
      setStatus({ kind: "error", message });
    } finally {
      setUpdatingId(null);
    }
  }

  const [pendingTodos, accomplishedTodos] = useMemo(() => {
    const pending: Todo[] = [];
    const accomplished: Todo[] = [];

    for (const todo of todos) {
      if (todo.status?.toLowerCase() === ACCOMPLISHED_STATUS) {
        accomplished.push(todo);
      } else {
        pending.push(todo);
      }
    }

    return [pending, accomplished];
  }, [todos]);

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div style={{ display: "grid", gap: "8px" }}>
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Todos</h1>
        <p style={{ color: "#555" }}>
          Review outstanding tasks and mark them as accomplished once you finish
          them. Completed tasks stay available for reference.
        </p>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: "#666", fontSize: "0.95rem" }}>
          {isLoading
            ? "Loading todos…"
            : todos.length === 0
            ? "No todos yet"
            : `${pendingTodos.length} pending · ${accomplishedTodos.length} accomplished`}
        </span>
        <button
          type="button"
          onClick={loadTodos}
          disabled={isLoading || updatingId !== null}
          style={{
            background: "transparent",
            border: "1px solid #d0d0d0",
            borderRadius: "8px",
            padding: "8px 16px",
            cursor: isLoading ? "progress" : "pointer",
            color: "#444",
            fontWeight: 500,
          }}
        >
          Refresh
        </button>
      </div>

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

      <div style={{ display: "grid", gap: "32px" }}>
        <TodoSection
          title="Pending"
          emptyLabel="All caught up!"
          todos={pendingTodos}
          onToggle={toggleTodo}
          onDelete={deleteTodo}
          updatingId={updatingId}
        />
        <TodoSection
          title="Accomplished"
          emptyLabel="No accomplished todos yet"
          todos={accomplishedTodos}
          onToggle={toggleTodo}
          onDelete={deleteTodo}
          updatingId={updatingId}
        />
      </div>
    </section>
  );
}

type TodoSectionProps = {
  title: string;
  emptyLabel: string;
  todos: Todo[];
  onToggle: (todo: Todo) => Promise<void>;
  onDelete: (todo: Todo) => Promise<void>;
  updatingId: string | null;
};

function TodoSection({ title, emptyLabel, todos, onToggle, onDelete, updatingId }: TodoSectionProps) {
  if (todos.length === 0) {
    return (
      <section
        style={{
          display: "grid",
          gap: "12px",
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "20px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
        }}
      >
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>{title}</h2>
          <span style={{ color: "#999", fontSize: "0.9rem" }}>0</span>
        </header>
        <p style={{ color: "#777", fontSize: "0.9rem" }}>{emptyLabel}</p>
      </section>
    );
  }

  return (
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
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>{title}</h2>
        <span style={{ color: "#666", fontSize: "0.9rem" }}>{todos.length}</span>
      </header>

      <div style={{ display: "grid", gap: "12px" }}>
        {todos.map((todo) => {
          const isAccomplished = todo.status?.toLowerCase() === ACCOMPLISHED_STATUS;
          const isUpdating = updatingId === todo.todo_id;

          return (
            <article
              key={todo.todo_id}
              style={{
                border: "1px solid #e5e7eb",
                borderRadius: "10px",
                padding: "16px",
                display: "grid",
                gap: "12px",
                background: isAccomplished ? "#f8fafc" : "#fff",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                <div style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontSize: "0.75rem", color: "#9ca3af", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    {todo.todo_id}
                  </span>
                  <p style={{ margin: 0, fontSize: "1rem", lineHeight: 1.4, color: "#1f2937" }}>{todo.description}</p>
                </div>
                <span
                  style={{
                    background: isAccomplished ? "#dcfce7" : "#fee2e2",
                    color: isAccomplished ? "#166534" : "#b91c1c",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    padding: "4px 8px",
                    borderRadius: "999px",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  {isAccomplished ? "Accomplished" : todo.status || "Pending"}
                </span>
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", fontSize: "0.85rem", color: "#4b5563" }}>
                <span>
                  <strong>Due:</strong> {formatDate(todo.due_date)}
                </span>
                {todo.contacts.length > 0 && (
                  <span>
                    <strong>Contacts:</strong> {todo.contacts.join(", ")}
                  </span>
                )}
                {todo.events.length > 0 && (
                  <span>
                    <strong>Events:</strong> {todo.events.join(", ")}
                  </span>
                )}
                {todo.places.length > 0 && (
                  <span>
                    <strong>Places:</strong> {todo.places.join(", ")}
                  </span>
                )}
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button
                  type="button"
                  onClick={() => onToggle(todo)}
                  disabled={isUpdating}
                  style={{
                    background: isAccomplished ? "#fef3c7" : "#0b6bcb",
                    color: isAccomplished ? "#92400e" : "#fff",
                    border: "none",
                    borderRadius: "8px",
                    padding: "8px 16px",
                    fontWeight: 600,
                    cursor: isUpdating ? "progress" : "pointer",
                    opacity: isUpdating ? 0.7 : 1,
                  }}
                >
                  {isUpdating
                    ? "Saving..."
                    : isAccomplished
                    ? "Mark as pending"
                    : "Mark accomplished"}
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(todo)}
                  disabled={isUpdating}
                  style={{
                    background: "#fee2e2",
                    color: "#b91c1c",
                    border: "1px solid #fecaca",
                    borderRadius: "8px",
                    padding: "8px 16px",
                    fontWeight: 600,
                    cursor: isUpdating ? "progress" : "pointer",
                    opacity: isUpdating ? 0.7 : 1,
                  }}
                >
                  {isUpdating ? "Working..." : "Delete"}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}


