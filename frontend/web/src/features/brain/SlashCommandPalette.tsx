"use client";

const COMMANDS = [
  {
    name: "event",
    description: "Capture or update an event",
  },
  {
    name: "contact",
    description: "Create or update contacts and relationships",
  },
];

export function SlashCommandPalette({
  query,
  onSelect,
}: {
  query: string;
  onSelect: (command: string) => void;
}) {
  const normalizedQuery = query.trim().toLowerCase();
  const commands = COMMANDS.filter((command) => command.name.startsWith(normalizedQuery));
  if (commands.length === 0) return null;

  return (
    <div
      style={{
        border: "1px solid #cbd5e1",
        borderRadius: "8px",
        background: "#ffffff",
        boxShadow: "0 12px 30px rgba(15, 23, 42, 0.14)",
        display: "grid",
        overflow: "hidden",
        width: "min(360px, calc(100vw - 48px))",
      }}
    >
      {commands.map((command) => (
        <button
          key={command.name}
          type="button"
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(command.name);
          }}
          style={{
            border: "none",
            background: "transparent",
            cursor: "pointer",
            display: "grid",
            gap: "2px",
            padding: "10px 12px",
            textAlign: "left",
          }}
        >
          <span style={{ color: "#0f172a", fontSize: "0.9rem", fontWeight: 650 }}>
            /{command.name}
          </span>
          <span style={{ color: "#64748b", fontSize: "0.78rem" }}>{command.description}</span>
        </button>
      ))}
    </div>
  );
}
