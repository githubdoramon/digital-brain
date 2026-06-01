"use client";

import Link from "next/link";
import type { LinkedItem } from "@/lib/api";

function routeForLinkedItem(item: LinkedItem): string | null {
  const entityId = item.entity_id.trim();
  if (!entityId) return null;

  const encoded = encodeURIComponent(entityId);
  if (item.entity_type === "event") return `/meetings/${encoded}`;
  if (item.entity_type === "document") return `/documents?document_id=${encoded}`;
  if (item.entity_type === "contact") return `/contacts?contact_id=${encoded}`;
  if (item.entity_type === "place") return `/contacts?place_id=${encoded}`;
  return null;
}

export function LinkedItemsRow({ items }: { items: LinkedItem[] }) {
  if (items.length === 0) return null;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", maxWidth: "80%" }}>
      {items.map((item, index) => {
        const href = routeForLinkedItem(item);
        const label = item.title?.trim() || item.entity_id;
        const content = (
          <>
            <span style={{ fontWeight: 650 }}>{label}</span>
            {item.role ? <span style={{ color: "#64748b" }}>{item.role}</span> : null}
          </>
        );
        const style = {
          border: "1px solid #cbd5e1",
          background: "#ffffff",
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
            <span key={`${item.entity_type}:${item.entity_id}:${index}`} style={style}>
              {content}
            </span>
          );
        }

        return (
          <Link key={`${item.entity_type}:${item.entity_id}:${index}`} href={href} style={style}>
            {content}
          </Link>
        );
      })}
    </div>
  );
}
