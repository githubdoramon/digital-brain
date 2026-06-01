"use client";

import type { HTMLAttributes, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message } from "./types";

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

export function AssistantMarkdown({ content, role }: { content: string; role: Message["role"] }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={getMarkdownComponents(role)}>
      {content}
    </ReactMarkdown>
  );
}
