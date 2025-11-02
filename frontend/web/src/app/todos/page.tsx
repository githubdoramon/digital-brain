"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
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

function normalizeIsoDate(date: string | null): string | null {
  if (!date) {
    return null;
  }

  const timestamp = Date.parse(date);
  if (Number.isNaN(timestamp)) {
    return date;
  }

  return new Date(timestamp).toISOString();
}

function toDateInputValue(date: string | null): string {
  if (!date) {
    return "";
  }

  const timestamp = Date.parse(date);
  if (Number.isNaN(timestamp)) {
    return "";
  }

  return new Date(timestamp).toISOString().slice(0, 10);
}

function getComparableTime(date: string | null): number {
  if (!date) {
    return Number.POSITIVE_INFINITY;
  }

  const timestamp = Date.parse(date);
  if (Number.isNaN(timestamp)) {
    return Number.POSITIVE_INFINITY;
  }

  return timestamp;
}

function compareTodosByDueDate(a: Todo, b: Todo): number {
  const aTime = getComparableTime(a.due_date);
  const bTime = getComparableTime(b.due_date);

  if (aTime !== bTime) {
    return aTime - bTime;
  }

  return a.todo_id.localeCompare(b.todo_id);
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

  async function updateTodoDueDate(todo: Todo, dueDate: string | null) {
    const normalizedNextDueDate = normalizeIsoDate(dueDate);
    const normalizedCurrentDueDate = normalizeIsoDate(todo.due_date);

    if (normalizedNextDueDate === normalizedCurrentDueDate) {
      return;
    }

    setUpdatingId(todo.todo_id);
    setStatus({ kind: "idle" });

    try {
      await api.post("/ingest/todo", {
        todo_id: todo.todo_id,
        description: todo.description,
        status: todo.status,
        due_date: normalizedNextDueDate,
        contact_ids: todo.contacts,
        event_ids: todo.events,
        place_ids: todo.places,
      });

      setTodos((previous) =>
        previous.map((item) =>
          item.todo_id === todo.todo_id
            ? {
                ...item,
                due_date: normalizedNextDueDate,
                updated_at: new Date().toISOString(),
              }
            : item
        )
      );

      setStatus({
        kind: "success",
        message: normalizedNextDueDate
          ? `Updated due date for ${todo.todo_id}`
          : `Cleared due date for ${todo.todo_id}`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to update due date";
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

    pending.sort(compareTodosByDueDate);
    accomplished.sort(compareTodosByDueDate);

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
          onUpdateDueDate={updateTodoDueDate}
          updatingId={updatingId}
        />
        <TodoSection
          title="Accomplished"
          emptyLabel="No accomplished todos yet"
          todos={accomplishedTodos}
          onToggle={toggleTodo}
          onDelete={deleteTodo}
          onUpdateDueDate={updateTodoDueDate}
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
  onUpdateDueDate: (todo: Todo, dueDate: string | null) => Promise<void>;
  updatingId: string | null;
};

function TodoSection({ title, emptyLabel, todos, onToggle, onDelete, onUpdateDueDate, updatingId }: TodoSectionProps) {
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
        {todos.map((todo) => (
          <TodoCard
            key={todo.todo_id}
            todo={todo}
            isUpdating={updatingId === todo.todo_id}
            onToggle={onToggle}
            onDelete={onDelete}
            onUpdateDueDate={onUpdateDueDate}
          />
        ))}
      </div>
    </section>
  );
}

type TodoCardProps = {
  todo: Todo;
  isUpdating: boolean;
  onToggle: (todo: Todo) => Promise<void>;
  onDelete: (todo: Todo) => Promise<void>;
  onUpdateDueDate: (todo: Todo, dueDate: string | null) => Promise<void>;
};

function TodoCard({ todo, isUpdating, onToggle, onDelete, onUpdateDueDate }: TodoCardProps) {
  const isAccomplished = todo.status?.toLowerCase() === ACCOMPLISHED_STATUS;
  const [isEditingDueDate, setIsEditingDueDate] = useState(false);
  const [draftDueDate, setDraftDueDate] = useState(() => toDateInputValue(todo.due_date));
  const dateInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setDraftDueDate(toDateInputValue(todo.due_date));
  }, [todo.due_date]);

  useEffect(() => {
    if (isEditingDueDate && dateInputRef.current) {
      if (typeof dateInputRef.current.showPicker === "function") {
        dateInputRef.current.showPicker();
      }
      dateInputRef.current.focus();
    }
  }, [isEditingDueDate]);

  const handleDueDateClick = useCallback(() => {
    if (isUpdating) {
      return;
    }

    setIsEditingDueDate(true);
  }, [isUpdating]);

  const handleDueDateChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setDraftDueDate(value);

      if (value) {
        const timestamp = Date.parse(value);
        const isoValue = Number.isNaN(timestamp) ? null : new Date(timestamp).toISOString();
        await onUpdateDueDate(todo, isoValue);
      } else {
        await onUpdateDueDate(todo, null);
      }

      setIsEditingDueDate(false);
    },
    [onUpdateDueDate, todo]
  );

  const handleClearDueDate = useCallback(async () => {
    setDraftDueDate("");
    await onUpdateDueDate(todo, null);
    setIsEditingDueDate(false);
  }, [onUpdateDueDate, todo]);

  const handleInputBlur = useCallback((event: FocusEvent<HTMLInputElement>) => {
    const nextFocus = event.relatedTarget as HTMLElement | null;
    if (nextFocus?.dataset?.role === "todo-clear-due-date") {
      return;
    }

    setIsEditingDueDate(false);
  }, []);

  const handleInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setDraftDueDate(toDateInputValue(todo.due_date));
        setIsEditingDueDate(false);
      }
    },
    [todo.due_date]
  );

  return (
    <article
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
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <strong>Due:</strong>
          {isEditingDueDate ? (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input
                ref={dateInputRef}
                type="date"
                value={draftDueDate}
                onChange={handleDueDateChange}
                onBlur={handleInputBlur}
                onKeyDown={handleInputKeyDown}
                disabled={isUpdating}
                style={{
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  padding: "4px 8px",
                  fontSize: "0.85rem",
                  color: "#1f2937",
                  background: isUpdating ? "#f3f4f6" : "#fff",
                }}
              />
              {(todo.due_date || draftDueDate) && (
                <button
                  type="button"
                  data-role="todo-clear-due-date"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={handleClearDueDate}
                  disabled={isUpdating}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#b91c1c",
                    fontSize: "0.8rem",
                    cursor: isUpdating ? "not-allowed" : "pointer",
                    fontWeight: 600,
                  }}
                >
                  Clear
                </button>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={handleDueDateClick}
              disabled={isUpdating}
              style={{
                background: "transparent",
                border: "1px solid transparent",
                padding: "4px 8px",
                borderRadius: "6px",
                fontSize: "0.85rem",
                color: isUpdating ? "#9ca3af" : "#2563eb",
                cursor: isUpdating ? "not-allowed" : "pointer",
                textDecoration: "underline",
                textUnderlineOffset: "2px",
              }}
            >
              {formatDate(todo.due_date)}
            </button>
          )}
        </div>
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
}


