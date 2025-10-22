'use client';

import Link from "next/link";
import { FormEvent, useState, useRef, useEffect } from "react";

type Message = {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
};

const API_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
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
      const response = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: userMessage.content,
          limit: 5,
        }),
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "Failed to get response");
      }

      const data = await response.json();
      const assistantMessage: Message = {
        role: "assistant",
        content: data.answer || "I couldn't generate a response.",
        timestamp: new Date(),
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
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Welcome</h1>
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
          }}
        >
          <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>
            Chat with your Digital Brain
          </h2>
          <p style={{ fontSize: "0.875rem", color: "#666", marginTop: "4px" }}>
            Ask about your memories, contacts, meetings, and more
          </p>
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
                Examples: "What meetings did I have last week?" or "Tell me about my conversations with Monica"
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
                  whiteSpace: "pre-wrap",
                }}
              >
                {message.content}
              </div>
              <div
                style={{
                  fontSize: "0.75rem",
                  color: "#999",
                  marginTop: "4px",
                  paddingLeft: message.role === "user" ? "0" : "8px",
                  paddingRight: message.role === "user" ? "8px" : "0",
                }}
              >
                {message.timestamp.toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
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