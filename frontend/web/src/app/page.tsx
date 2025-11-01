'use client';

import Link from "next/link";
import { FormEvent, useState, useRef, useEffect } from "react";
import type { ReactNode, HTMLAttributes } from "react";
import { useSession } from "next-auth/react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";

type Message = {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  memories?: string[];
};

type AskResponse = {
  answer?: string;
  memories_used?: string[];
};

type MarkdownCodeProps = HTMLAttributes<HTMLElement> & {
  inline?: boolean;
  children?: ReactNode;
};

function getMarkdownComponents(role: Message["role"]): Components {
  const sharedTextColor = { color: "inherit" };
  const blockSpacing = { margin: "0 0 0.75rem 0" };
  const listIndent = { margin: "0 0 0.75rem 1.2rem", padding: 0 };
  const codeBackground = role === "user" ? "rgba(255, 255, 255, 0.18)" : "rgba(15, 23, 42, 0.08)";
  const preBackground = role === "user" ? "rgba(255, 255, 255, 0.15)" : "rgba(15, 23, 42, 0.06)";

  const codeRenderer = ({ inline, children, ...props }: MarkdownCodeProps) => {
    if (inline) {
      return (
        <code
          style={{
            ...sharedTextColor,
            background: codeBackground,
            padding: "0.15rem 0.35rem",
            borderRadius: "4px",
            fontFamily:
              "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace)",
          }}
          {...props}
        >
          {children}
        </code>
      );
    }

    return (
      <pre
        style={{
          ...sharedTextColor,
          ...blockSpacing,
          background: preBackground,
          padding: "0.85rem",
          borderRadius: "8px",
          overflowX: "auto",
          fontSize: "0.9rem",
        }}
      >
        <code
          style={{
            fontFamily:
              "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace)",
            color: "inherit",
          }}
          {...props}
        >
          {children}
        </code>
      </pre>
    );
  };

  return {
    p: ({ children }) => (
      <p style={{ ...sharedTextColor, ...blockSpacing, lineHeight: 1.55 }}>{children}</p>
    ),
    a: ({ href, children }) => (
      <a
        href={href ?? "#"}
        target="_blank"
        rel="noopener noreferrer"
        style={{ ...sharedTextColor, textDecoration: "underline" }}
      >
        {children}
      </a>
    ),
    ul: ({ children }) => (
      <ul style={{ ...sharedTextColor, ...listIndent, display: "grid", gap: "0.35rem", listStylePosition: "outside" }}>
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol style={{ ...sharedTextColor, ...listIndent, display: "grid", gap: "0.35rem", listStylePosition: "outside" }}>
        {children}
      </ol>
    ),
    li: ({ children }) => (
      <li style={{ ...sharedTextColor, margin: 0, lineHeight: 1.45 }}>{children}</li>
    ),
    blockquote: ({ children }) => (
      <blockquote
        style={{
          ...sharedTextColor,
          ...blockSpacing,
          padding: "0.5rem 0.75rem",
          borderLeft: `4px solid ${role === "user" ? "rgba(255,255,255,0.4)" : "#cbd5f5"}`,
          background: role === "user" ? "rgba(255,255,255,0.12)" : "rgba(15, 23, 42, 0.04)",
          borderRadius: "6px",
        }}
      >
        {children}
      </blockquote>
    ),
    code: codeRenderer,
  } satisfies Components;
}

// Generate a unique session ID for this chat session
function generateSessionId(): string {
  return `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

export default function Home() {
  const { data: session } = useSession();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => generateSessionId());
  const [showMemories, setShowMemories] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const data = await api.post<AskResponse>("/ask", {
        question: userMessage.content,
        limit: 5,
        session_id: sessionId,
      });
      const assistantMessage: Message = {
        role: "assistant",
        content: data.answer || "I couldn't generate a response.",
        timestamp: new Date(),
        memories: data.memories_used || [],
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      const errorMessage: Message = {
        role: "assistant",
        content: `Error: ${error instanceof Error ? error.message : "Unexpected error occurred"}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section style={{ display: "grid", gap: "16px" }}>
      <div>
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>
          Welcome{session?.user?.name ? `, ${session.user.name.split(' ')[0]}` : ''}!
        </h1>
        <p style={{ color: "#555", marginTop: "8px" }}>
          Ask questions about your personal memories and get AI-powered insights.
        </p>
      </div>

      {/* Chat Interface */}
      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
          display: "grid",
          gridTemplateRows: "auto 1fr auto",
          height: "600px",
        }}
      >
        {/* Chat Header */}
        <div
          style={{
            padding: "20px 24px",
            borderBottom: "1px solid #e2e2e2",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>
              Chat with your Digital Brain
            </h2>
            <p style={{ fontSize: "0.875rem", color: "#666", marginTop: "4px" }}>
              Ask about your memories, contacts, meetings, and more
            </p>
          </div>
          <button
            onClick={() => setShowMemories(!showMemories)}
            style={{
              padding: "8px 12px",
              background: showMemories ? "#0b6bcb" : "#f5f5f5",
              color: showMemories ? "#fff" : "#666",
              border: "1px solid #d0d0d0",
              borderRadius: "6px",
              fontSize: "0.8rem",
              cursor: "pointer",
              whiteSpace: "nowrap",
              transition: "all 0.2s",
            }}
            title={showMemories ? "Hide memory details" : "Show memory details"}
          >
            {showMemories ? "🧠 Memories On" : "🧠 Memories Off"}
          </button>
        </div>

        {/* Messages Area */}
        <div
          style={{
            padding: "24px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          {messages.length === 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "#999",
                gap: "12px",
              }}
            >
              <div style={{ fontSize: "2.5rem" }}>💬</div>
              <p style={{ fontSize: "0.95rem" }}>
                Start a conversation by asking a question below
              </p>
              <div style={{ fontSize: "0.85rem", color: "#aaa", textAlign: "center", maxWidth: "400px" }}>
                Examples: &quot;What meetings did I have last week?&quot; or &quot;Tell me about my conversations with Monica&quot;
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: message.role === "user" ? "flex-end" : "flex-start",
              }}
            >
              <div
                style={{
                  maxWidth: "80%",
                  padding: "12px 16px",
                  borderRadius: "12px",
                  background: message.role === "user" ? "#0b6bcb" : "#f5f5f5",
                  color: message.role === "user" ? "#fff" : "#333",
                  wordWrap: "break-word",
                  overflowWrap: "anywhere",
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.5rem",
                }}
              >
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={getMarkdownComponents(message.role)}
                >
                  {message.content}
                </ReactMarkdown>
              </div>
              <div
                style={{
                  fontSize: "0.75rem",
                  color: "#999",
                  marginTop: "4px",
                  paddingLeft: message.role === "user" ? "0" : "8px",
                  paddingRight: message.role === "user" ? "8px" : "0",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <span>
                  {message.timestamp.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
                {message.memories && message.memories.length > 0 && (
                  <span
                    title={`Used ${message.memories.length} memories from previous conversations`}
                    style={{
                      background: "#e0f2fe",
                      color: "#0369a1",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      fontSize: "0.7rem",
                      fontWeight: 500,
                    }}
                  >
                    🧠 {message.memories.length} memories
                  </span>
                )}
              </div>
              {message.memories && message.memories.length > 0 && showMemories && (
                <div
                  style={{
                    maxWidth: "80%",
                    marginTop: "8px",
                    padding: "8px 12px",
                    background: "#f0f9ff",
                    border: "1px solid #bae6fd",
                    borderRadius: "8px",
                    fontSize: "0.8rem",
                    color: "#0c4a6e",
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                    Memories used:
                  </div>
                  <ul style={{ margin: 0, paddingLeft: "20px" }}>
                    {message.memories.map((mem, i) => (
                      <li key={i} style={{ marginBottom: "2px" }}>
                        {mem}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}

          {isLoading && (
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
              }}
            >
              <div
                style={{
                  padding: "12px 16px",
                  borderRadius: "12px",
                  background: "#f5f5f5",
                  color: "#666",
                }}
              >
                <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
                  <span>Thinking</span>
                  <span className="loading-dots">...</span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <form
          onSubmit={handleSubmit}
          style={{
            padding: "20px 24px",
            borderTop: "1px solid #e2e2e2",
            background: "#fafafa",
            borderRadius: "0 0 12px 12px",
          }}
        >
          <div style={{ display: "flex", gap: "12px" }}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question about your memories..."
              disabled={isLoading}
              style={{
                flex: 1,
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "12px 16px",
                fontSize: "0.95rem",
                outline: "none",
                background: "#fff",
              }}
            />
            <button
              type="submit"
              disabled={isLoading || !input.trim()}
              style={{
                background: "#0b6bcb",
                color: "#fff",
                border: "none",
                borderRadius: "8px",
                padding: "12px 24px",
                fontWeight: 600,
                cursor: isLoading || !input.trim() ? "not-allowed" : "pointer",
                opacity: isLoading || !input.trim() ? 0.6 : 1,
                whiteSpace: "nowrap",
              }}
            >
              {isLoading ? "Sending..." : "Send"}
            </button>
          </div>
        </form>
      </div>

      {/* Quick Links */}
      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "24px",
          background: "#fff",
        }}
      >
        <h2 style={{ fontSize: "1.25rem", marginBottom: "8px" }}>
          Quick Links
        </h2>
        <ul style={{ listStyle: "disc", paddingInlineStart: "20px", color: "#444" }}>
          <li>
            Manage your personal contacts through the
            {" "}
            <Link href="/contacts" style={{ color: "#0b6bcb" }}>
              Contacts page
            </Link>
            .
          </li>
          <li>
            Import new meeting transcripts through the
            {" "}
            <Link href="/meetings" style={{ color: "#0b6bcb" }}>
              Meetings page
            </Link>
            .
          </li>
          <li>
            Ask questions about your memories using the chat interface above.
          </li>
        </ul>
      </div>
    </section>
  );
}